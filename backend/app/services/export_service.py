"""
services/export_service.py

Membungkus file-file hasil pipeline (log PLINK, .assoc dari GEMMA, VCF hasil
imputasi) menjadi satu ZIP yang di-generate secara streaming.

Prinsip desain:
- TIDAK PERNAH membaca isi file penuh ke memori. Setiap file dibaca chunk-per-chunk
  (default 8 MB) langsung dari disk/object storage.
- TIDAK PERNAH membangun ZIP penuh di memori/disk sementara sebelum dikirim. ZIP
  di-generate sebagai aliran byte (generator) yang langsung dikonsumsi oleh
  StreamingResponse FastAPI, sehingga penggunaan memori tetap konstan tidak peduli
  seberapa besar total ukuran file (bisa puluhan GB).
- Menggunakan `stream-zip` (pip install stream-zip), library yang secara khusus
  dirancang untuk membuat arsip ZIP dari sumber data yang non-seekable/streaming.
  Ini menghindari keterbatasan `zipfile.ZipFile` standar yang idealnya butuh
  stream yang bisa di-seek untuk menulis central directory secara efisien.

Jika file individual sudah berupa `.vcf.gz` (BGZF), kompresi ZIP tambahan
dilewati (STORE, bukan DEFLATE) agar CPU tidak terbuang mengompres ulang data
yang sudah padat.
"""

import os
from datetime import datetime
from typing import Iterable, Iterator, TypedDict

from stream_zip import NO_COMPRESSION_64, ZIP_32, stream_zip

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB per baca — aman untuk file berukuran GB


class ExportFile(TypedDict):
    arcname: str  # nama/path file di dalam ZIP, mis. "logs/plink_qc.log"
    path: str     # lokasi fisik file di storage server


def _file_chunk_reader(file_path: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Generator pembaca file secara streaming. Tidak pernah memuat seluruh isi ke memori."""
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _build_export_members(result_files: Iterable[ExportFile]):
    """
    Menghasilkan definisi member ZIP satu per satu (lazy) sesuai format yang
    diharapkan oleh stream_zip: (nama, waktu_modifikasi, mode_file, metode, chunks).
    """
    now = datetime.now()
    for rf in result_files:
        path = rf["path"]

        if not os.path.exists(path):
            # Lewati file yang hilang/gagal ditulis agar satu file rusak tidak
            # menggagalkan seluruh proses export bagi peneliti.
            continue

        method = NO_COMPRESSION_64 if path.endswith(".gz") or path.endswith(".bgz") else ZIP_32

        yield (
            rf["arcname"],
            now,
            0o600,
            method,
            _file_chunk_reader(path),
        )


def generate_zip_stream(result_files: list[ExportFile]) -> Iterator[bytes]:
    """
    Entry point utama: kembalikan generator byte ZIP siap pakai untuk
    StreamingResponse. Tidak melakukan I/O apa pun sampai generator ini
    benar-benar di-iterate oleh ASGI server.
    """
    return stream_zip(_build_export_members(result_files))
