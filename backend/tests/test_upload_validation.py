"""Unit tests untuk G01: validasi upload terbatas (gzip bomb, line limit, magic sniff, handle close)."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
from pathlib import Path

import pytest

from app.services.upload_validation import (
    GZIP_MAGIC,
    UploadValidationError,
    copy_upload_bounded,
    open_lines_bounded,
    sniff_compression,
)


def _write_gz(path: Path, payload: bytes) -> Path:
    with gzip.open(path, "wb") as gz:
        gz.write(payload)
    return path


def test_copy_upload_bounded_success_and_overflow_cleanup(tmp_path: Path):
    payload = b"hello\nworld\n" * 100
    dest_ok = tmp_path / "ok.bin"
    n_bytes, digest = copy_upload_bounded(io.BytesIO(payload), dest_ok, max_bytes=len(payload), chunk_size=64)
    assert n_bytes == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert dest_ok.read_bytes() == payload

    dest_over = tmp_path / "over.bin"
    with pytest.raises(UploadValidationError) as exc_info:
        copy_upload_bounded(io.BytesIO(payload), dest_over, max_bytes=len(payload) - 1, chunk_size=64)
    assert exc_info.value.code == "UPLOAD_TOO_LARGE"
    assert exc_info.value.http_status == 413
    assert not dest_over.exists()


@pytest.mark.parametrize(
    ("raw_bytes", "expected_kind", "expected_code"),
    [
        (GZIP_MAGIC + b"\x08\x00\x00\x00\x00\x00\x02\xff", "gzip", None),
        (b"G1\tGO:0006357\n", "plain", None),
        (b"PK\x03\x04fakezip", None, "UNSUPPORTED_COMPRESSION"),
        (b"BZh91AY&SY", None, "UNSUPPORTED_COMPRESSION"),
        (b"\xfd7zXZ\x00rest", None, "UNSUPPORTED_COMPRESSION"),
        (b"\x28\xb5\x2f\xfdrest", None, "UNSUPPORTED_COMPRESSION"),
        (b"abc\x00def\n", None, "BINARY_CONTENT"),
    ],
)
def test_sniff_compression_formats_and_errors(tmp_path: Path, raw_bytes: bytes, expected_kind, expected_code):
    target = tmp_path / "sample.dat"
    target.write_bytes(raw_bytes)
    if expected_code is not None:
        with pytest.raises(UploadValidationError) as exc_info:
            sniff_compression(target)
        assert exc_info.value.code == expected_code
        assert exc_info.value.http_status == 400
    else:
        assert sniff_compression(target) == expected_kind


def test_open_lines_bounded_rejects_decompressed_bomb_with_newlines(tmp_path: Path):
    gz_path = _write_gz(tmp_path / "bomb_nl.gz", (b"0" * 1023 + b"\n") * 2048)
    with pytest.raises(UploadValidationError) as exc_info:
        with open_lines_bounded(
            gz_path,
            max_decompressed_bytes=1024 * 1024,
            max_lines=1_000_000,
            max_line_bytes=65536,
        ) as lines:
            for _ in lines:
                pass
    assert exc_info.value.code == "DECOMPRESSED_TOO_LARGE"
    assert exc_info.value.http_status == 413


def test_open_lines_bounded_rejects_too_many_lines_including_empty(tmp_path: Path):
    gz_path = _write_gz(tmp_path / "many_lines.gz", b"\n" * 5000)
    with pytest.raises(UploadValidationError) as exc_info:
        with open_lines_bounded(
            gz_path,
            max_decompressed_bytes=10 * 1024 * 1024,
            max_lines=1000,
            max_line_bytes=65536,
        ) as lines:
            for _ in lines:
                pass
    assert exc_info.value.code == "TOO_MANY_LINES"
    assert exc_info.value.http_status == 413


def test_open_lines_bounded_rejects_line_too_long_without_newline(tmp_path: Path):
    gz_path = _write_gz(tmp_path / "long_line.gz", b"A" * (200 * 1024))
    with pytest.raises(UploadValidationError) as exc_info:
        with open_lines_bounded(
            gz_path,
            max_decompressed_bytes=10 * 1024 * 1024,
            max_lines=1000,
            max_line_bytes=65536,
        ) as lines:
            for _ in lines:
                pass
    assert exc_info.value.code == "LINE_TOO_LONG"
    assert exc_info.value.http_status == 400


def test_open_lines_bounded_rejects_corrupt_gzip(tmp_path: Path):
    gz_path = _write_gz(tmp_path / "good.gz", b"G1\tGO:0006357\n" * 20)
    truncated = tmp_path / "corrupt.gz"
    truncated.write_bytes(gz_path.read_bytes()[:-4])

    with pytest.raises(UploadValidationError) as exc_info:
        with open_lines_bounded(
            truncated,
            max_decompressed_bytes=1024 * 1024,
            max_lines=1000,
            max_line_bytes=65536,
        ) as lines:
            for _ in lines:
                pass
    assert exc_info.value.code == "CORRUPT_GZIP"
    assert exc_info.value.http_status == 400


def test_open_lines_bounded_decoding_bom_crlf_and_early_exit_closes_handle(tmp_path: Path):
    raw_payload = b"\xef\xbb\xbfline1\r\nline2_\xff\xfe_end\r\nline3\n"
    gz_path = _write_gz(tmp_path / "plain_named_no_ext", raw_payload)

    collected = []
    with open_lines_bounded(
        gz_path,
        max_decompressed_bytes=1024 * 1024,
        max_lines=100,
        max_line_bytes=65536,
    ) as lines:
        for line_no, text in lines:
            collected.append((line_no, text))

    assert collected[0] == (1, "line1")
    assert collected[1][0] == 2
    assert collected[1][1].startswith("line2_")
    assert "\ufffd" in collected[1][1]
    assert collected[2] == (3, "line3")

    # Uji keluar dini (break + raise) menutup handle sehingga os.remove sukses di Windows.
    class _EarlyAbort(RuntimeError):
        pass

    with pytest.raises(_EarlyAbort):
        with open_lines_bounded(
            gz_path,
            max_decompressed_bytes=1024 * 1024,
            max_lines=100,
            max_line_bytes=65536,
        ) as lines:
            for _ in lines:
                raise _EarlyAbort("stop early")

    os.remove(gz_path)
    assert not gz_path.exists()
