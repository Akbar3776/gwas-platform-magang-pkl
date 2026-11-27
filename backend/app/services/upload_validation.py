"""Utilitas pembacaan dan validasi unggahan teks/gzip secara terbatas dan aman."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
from pathlib import Path
from typing import BinaryIO, Iterator, Literal
import zlib

GZIP_MAGIC = b"\x1f\x8b"
_UNSUPPORTED_MAGICS: tuple[bytes, ...] = (
    b"PK\x03\x04",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",
)


class UploadValidationError(ValueError):
    """Error validasi unggahan terstruktur dengan kode stabil dan pesan aman untuk klien."""

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def copy_upload_bounded(
    src: BinaryIO,
    dest: Path,
    *,
    max_bytes: int,
    chunk_size: int = 1 << 20,
) -> tuple[int, str]:
    """Menyalin stream unggahan secara bertahap sambil menghitung SHA-256 dengan batas ukuran."""
    digest = hashlib.sha256()
    total = 0
    exceeded = False
    with dest.open("wb") as out:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                exceeded = True
                break
            digest.update(chunk)
            out.write(chunk)

    if exceeded:
        dest.unlink(missing_ok=True)
        raise UploadValidationError(
            "UPLOAD_TOO_LARGE",
            "Ukuran file unggahan melebihi batas maksimal yang diizinkan.",
            http_status=413,
        )
    return total, digest.hexdigest()


def sniff_compression(path: Path) -> Literal["gzip", "plain"]:
    """Memeriksa magic bytes pada <=8 KiB pertama untuk membedakan gzip, teks, dan biner."""
    with path.open("rb") as fh:
        head = fh.read(8192)

    for magic in _UNSUPPORTED_MAGICS:
        if head.startswith(magic):
            raise UploadValidationError(
                "UNSUPPORTED_COMPRESSION",
                "Format kompresi arsip tidak didukung; gunakan teks biasa atau .gz (gzip).",
                http_status=400,
            )

    if head.startswith(GZIP_MAGIC):
        return "gzip"

    if b"\x00" in head:
        raise UploadValidationError(
            "BINARY_CONTENT",
            "Berkas berisi data biner yang tidak valid untuk pemetaan teks.",
            http_status=400,
        )
    return "plain"


@contextlib.contextmanager
def open_lines_bounded(
    path: Path,
    *,
    max_decompressed_bytes: int,
    max_lines: int,
    max_line_bytes: int,
    encoding: str = "utf-8",
    stats: dict[str, int] | None = None,
) -> Iterator[Iterator[tuple[int, str]]]:
    """Membuka berkas teks/gzip dalam mode biner dengan batas byte dekompresi, baris, dan panjang baris."""
    with path.open("rb") as probe:
        is_gzip = probe.read(2) == GZIP_MAGIC

    fh = gzip.open(path, "rb") if is_gzip else path.open("rb")

    def _iter_lines() -> Iterator[tuple[int, str]]:
        total_bytes = 0
        line_no = 0
        gzip_errors: tuple[type[BaseException], ...] = (
            (EOFError, gzip.BadGzipFile, zlib.error, OSError)
            if is_gzip
            else (EOFError, zlib.error)
        )
        while True:
            try:
                raw = fh.readline(max_line_bytes + 1)
            except gzip_errors:
                raise UploadValidationError(
                    "CORRUPT_GZIP",
                    "Berkas terkompresi .gz rusak atau terpotong.",
                    http_status=400,
                ) from None

            if not raw:
                break

            if len(raw) > max_line_bytes:
                raise UploadValidationError(
                    "LINE_TOO_LONG",
                    "Panjang satu baris pada berkas melebihi batas maksimal yang diizinkan.",
                    http_status=400,
                )

            total_bytes += len(raw)
            if total_bytes > max_decompressed_bytes:
                raise UploadValidationError(
                    "DECOMPRESSED_TOO_LARGE",
                    "Ukuran isi berkas setelah dekompresi melebihi batas maksimal yang diizinkan.",
                    http_status=413,
                )

            line_no += 1
            if line_no > max_lines:
                raise UploadValidationError(
                    "TOO_MANY_LINES",
                    "Jumlah baris berkas melebihi batas maksimal yang diizinkan.",
                    http_status=413,
                )

            if stats is not None:
                stats["decompressed_bytes"] = total_bytes
                stats["lines"] = line_no

            text = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if line_no == 1 and text.startswith("\ufeff"):
                text = text[1:]
            yield line_no, text

    try:
        yield _iter_lines()
    finally:
        fh.close()
