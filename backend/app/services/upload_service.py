"""
Service inti untuk chunked upload: menyimpan potongan file ke disk (staging)
dan merakitnya kembali (reassemble) menjadi satu file VCF utuh.

Prinsip desain kunci (memenuhi Development Rule #1 - tahan banting terhadap
file GB-an tanpa kebocoran memori):
- TIDAK PERNAH memuat isi chunk atau file hasil rakitan secara penuh ke
  variabel/memori Python. Semua operasi baca-tulis menggunakan streaming
  dengan buffer tetap (`shutil.copyfileobj` dengan `length` eksplisit).
- Setiap fungsi I/O di sini bersifat SYNC (blocking) secara sengaja, agar
  bisa dipanggil baik dari route FastAPI sync (dijalankan otomatis di
  threadpool oleh FastAPI) maupun dari task Celery (yang memang sync).
"""

import hashlib
import re
import shutil
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

# Ukuran buffer untuk operasi copy streaming (1 MB).
# Nilai ini yang membatasi jejak memori per operasi baca-tulis, TERLEPAS
# dari seberapa besar ukuran chunk atau file akhir.
_COPY_BUFFER_SIZE = 1024 * 1024  # 1 MB

_STORAGE_ROOT = Path(settings.STORAGE_ROOT)
_STAGING_ROOT = _STORAGE_ROOT / "staging"
_DATASETS_ROOT = _STORAGE_ROOT / "datasets"

# Hanya izinkan karakter aman pada file_id untuk mencegah path traversal
# (mis. file_id = "../../etc/passwd" tidak boleh lolos jadi nama folder).
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_]+$")


class InvalidFileIdError(ValueError):
    """Dilempar jika file_id mengandung karakter yang tidak aman untuk path."""


def _validate_file_id(file_id: str) -> None:
    if not _SAFE_ID_PATTERN.match(file_id):
        raise InvalidFileIdError(f"file_id tidak valid: {file_id!r}")


def get_staging_dir(file_id: str) -> Path:
    """
    Mengembalikan (dan membuat jika belum ada) direktori staging untuk
    menampung chunk sementara milik satu file_id (biasanya = dataset.id).
    """
    _validate_file_id(file_id)
    staging_dir = _STAGING_ROOT / file_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    return staging_dir


def _chunk_path(file_id: str, chunk_index: int) -> Path:
    # Zero-padded agar urutan sorting alfabetis == urutan numerik saat assembly.
    return get_staging_dir(file_id) / f"chunk_{chunk_index:08d}.part"


def save_chunk(file_id: str, chunk_index: int, chunk_file: UploadFile) -> Path:
    """
    Menyimpan satu chunk ke disk secara streaming.

    `chunk_file.file` adalah SpooledTemporaryFile (Starlette) — dibaca dan
    ditulis dalam blok tetap (`_COPY_BUFFER_SIZE`), bukan `.read()` penuh,
    sehingga ukuran chunk (bisa 5-10 MB atau lebih) tidak pernah membengkak
    di memori Python sekaligus.
    """
    destination = _chunk_path(file_id, chunk_index)

    # Tulis ke file sementara dulu (`.tmp`) lalu rename atomik, supaya jika
    # proses terputus di tengah tulis, tidak ada chunk "setengah jadi" yang
    # ikut terhitung sebagai chunk valid oleh count_received_chunks().
    tmp_destination = destination.with_suffix(".tmp")
    with open(tmp_destination, "wb") as out_file:
        shutil.copyfileobj(chunk_file.file, out_file, length=_COPY_BUFFER_SIZE)

    tmp_destination.rename(destination)
    return destination


def count_received_chunks(file_id: str) -> int:
    """Menghitung jumlah chunk valid (bukan `.tmp`) yang sudah diterima di disk."""
    staging_dir = get_staging_dir(file_id)
    return len(list(staging_dir.glob("chunk_*.part")))


def get_received_chunk_indices(file_id: str) -> list[int]:
    """
    Mengembalikan daftar index chunk yang sudah diterima — dipakai frontend
    untuk fitur resume upload setelah koneksi terputus.
    """
    staging_dir = get_staging_dir(file_id)
    indices = []
    for path in staging_dir.glob("chunk_*.part"):
        match = re.search(r"chunk_(\d+)\.part", path.name)
        if match:
            indices.append(int(match.group(1)))
    return sorted(indices)


def assemble_file(file_id: str, total_chunks: int, original_filename: str) -> tuple[Path, int]:
    """
    Merakit seluruh chunk (index 0 .. total_chunks-1) menjadi satu file utuh,
    ditulis secara streaming langsung ke lokasi final di `_DATASETS_ROOT`.

    Dipanggil dari dalam Celery task (bukan dari request FastAPI langsung),
    karena proses ini bisa memakan waktu untuk file berukuran GB.

    Returns:
        (final_path, total_size_bytes)

    Raises:
        FileNotFoundError: jika ada chunk yang hilang (upload tidak lengkap).
    """
    _validate_file_id(file_id)
    staging_dir = get_staging_dir(file_id)

    final_dir = _DATASETS_ROOT / file_id
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / original_filename

    total_size = 0
    tmp_final_path = final_path.with_suffix(final_path.suffix + ".assembling")

    with open(tmp_final_path, "wb") as final_file:
        for index in range(total_chunks):
            chunk_path = staging_dir / f"chunk_{index:08d}.part"
            if not chunk_path.exists():
                raise FileNotFoundError(
                    f"Chunk index {index} tidak ditemukan untuk file_id={file_id}. "
                    f"Upload kemungkinan belum lengkap atau ada chunk yang gagal terkirim."
                )
            with open(chunk_path, "rb") as chunk_file:
                shutil.copyfileobj(chunk_file, final_file, length=_COPY_BUFFER_SIZE)
            total_size += chunk_path.stat().st_size

    tmp_final_path.rename(final_path)

    # Bersihkan direktori staging — chunk mentah tidak lagi diperlukan
    # setelah berhasil dirakit menjadi file final.
    shutil.rmtree(staging_dir, ignore_errors=True)

    return final_path, total_size


def compute_sha256(file_path: Path) -> str:
    """
    Menghitung checksum SHA-256 secara streaming (blok demi blok), aman
    dipakai untuk file berukuran GB tanpa membaca seluruh isi ke memori.
    """
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(_COPY_BUFFER_SIZE), b""):
            hasher.update(block)
    return hasher.hexdigest()
