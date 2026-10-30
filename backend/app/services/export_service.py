"""
services/export_service.py

Membungkus file-file hasil pipeline (log PLINK, .assoc dari GEMMA, VCF hasil
imputasi) menjadi satu ZIP yang di-generate secara streaming.
"""

import io
import os
import zipfile
from datetime import datetime
from typing import Iterable, Iterator, TypedDict

try:
    from stream_zip import NO_COMPRESSION_64, ZIP_32, stream_zip
    _HAS_STREAM_ZIP = True
except ImportError:  # pragma: no cover - fallback to standard library zipfile
    _HAS_STREAM_ZIP = False

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
        if not path or not os.path.exists(path):
            continue

        method = NO_COMPRESSION_64 if path.endswith(".gz") or path.endswith(".bgz") else ZIP_32

        yield (
            rf["arcname"],
            now,
            0o600,
            method,
            _file_chunk_reader(path),
        )


def _stdlib_zip_stream(result_files: list[ExportFile]) -> Iterator[bytes]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rf in result_files:
            path = rf["path"]
            if not path or not os.path.exists(path):
                continue
            compress_type = (
                zipfile.ZIP_STORED
                if path.endswith(".gz") or path.endswith(".bgz")
                else zipfile.ZIP_DEFLATED
            )
            zf.write(path, arcname=rf["arcname"], compress_type=compress_type)
    buffer.seek(0)
    while True:
        chunk = buffer.read(CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def generate_zip_stream(result_files: list[ExportFile]) -> Iterator[bytes]:
    """
    Entry point utama: kembalikan generator byte ZIP siap pakai untuk
    StreamingResponse.
    """
    if _HAS_STREAM_ZIP:
        return stream_zip(_build_export_members(result_files))
    return _stdlib_zip_stream(result_files)
