"""Unit tests untuk G14: endpoint upload/hapus ortolog BLAST dan listing dataset."""

from __future__ import annotations

import gzip
import io
from pathlib import Path
import uuid

from fastapi import HTTPException
import pytest
from starlette.datastructures import UploadFile

from app.api.routes import upload as upload_routes
from app.core.config import settings
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus, User, UserRole


def _gz_bytes(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(payload)
    return buf.getvalue()


def _make_fake_env(tmp_path: Path, monkeypatch, *, active_job: bool = False):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    other = User(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="c.vcf",
        storage_path=str(tmp_path / "c.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    jobs = (
        [
            AnalysisJob(
                id=uuid.uuid4(),
                dataset_id=ds.id,
                triggered_by=owner.id,
                overall_status=JobStatus.RUNNING_GWAS,
            )
        ]
        if active_job
        else []
    )

    class _DB:
        def get(self, model, oid):
            return ds if model is Dataset and str(oid) == str(ds.id) else None

        def query(self, *args):
            class _Q:
                def filter(self, *a):
                    return self

                def order_by(self, *a):
                    return self

                def limit(self, *a):
                    return self

                def all(self):
                    if args and args[0] is Dataset:
                        return [ds]
                    return jobs

            return _Q()

        def commit(self):
            pass

    return owner, other, ds, _DB()


def _assert_safe_error(
    exc: HTTPException, expected_status: int, expected_code: str, tmp_path: Path
) -> None:
    assert exc.status_code == expected_status
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == expected_code
    msg = exc.detail.get("message", "")
    assert isinstance(msg, str) and msg
    assert "Traceback" not in msg
    assert str(tmp_path) not in msg


def test_upload_ortholog_clean_storage_metadata_and_listing(tmp_path: Path, monkeypatch):
    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch)
    blast_payload = (
        "Query_id\tSubject_id\talignment_length\tmismatches\tgap_openings\t"
        "query_start\tquery_end\tsubject_start\tsubject_end\t%identity\te-value\tbit_score\n"
        "Asa7G00205\tAT3G05510.2\t29.07\t258\t141\t6\t1\t232\t115\t356\t2e-21\t99.4\n"
        "Asa2G00002\tAT2G00020.1\t50.00\t120\t40\t0\t1\t120\t1\t120\t1e-25\t110.0\n"
        "Asa2G00002\tAT2G00010.1\t50.00\t120\t40\t0\t1\t120\t1\t120\t1e-25\t110.0\n"
    ).encode("utf-8")
    uf = UploadFile(file=io.BytesIO(_gz_bytes(blast_payload)), filename="Asa_Ath.blast.gz")

    out = upload_routes.upload_ortholog_file(file_id=ds.id, file=uf, db=db, user=owner)
    assert "path" not in out
    assert out["filename"] == "Asa_Ath.blast.gz"
    assert out["n_rows"] == 3
    assert out["n_invalid_lines"] == 0
    assert out["n_queries"] == 2
    assert out["n_ties"] == 1
    assert out["sha256"]
    assert out["source_sha256"]
    assert out["source_bytes"] > 0
    assert out["decompressed_bytes"] > 0
    assert out["uploaded_at"]

    ds_dir = tmp_path / "datasets" / str(ds.id)
    assert [p.name for p in ds_dir.iterdir()] == ["ortholog_besthit.tsv.gz"]
    assert ds.extra_metadata["ortholog"]["path"] == str(ds_dir / "ortholog_besthit.tsv.gz")
    assert ds.extra_metadata["ortholog"]["sha256"] == out["sha256"]

    listed = upload_routes.list_uploaded_datasets(db=db, user=owner)
    assert len(listed) == 1
    assert listed[0]["has_ortholog"] is True
    assert listed[0]["ortholog_filename"] == "Asa_Ath.blast.gz"
    assert listed[0]["ortholog_n_genes"] == 2


def test_upload_ortholog_abuse_cases(tmp_path: Path, monkeypatch):
    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch)

    # 1. Bomb gzip decompressed size limit
    monkeypatch.setattr(settings, "ORTHOLOG_MAX_DECOMPRESSED_BYTES", 200)
    big_row = (
        "Asa7G00205\tAT3G05510.2\t29.07\t258\t141\t6\t1\t232\t115\t356\t2e-21\t99.4\n" * 10
    ).encode("utf-8")
    with pytest.raises(HTTPException) as exc_bomb:
        upload_routes.upload_ortholog_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(_gz_bytes(big_row)), filename="bomb.blast.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_bomb.value, 413, "DECOMPRESSED_TOO_LARGE", tmp_path)
    monkeypatch.setattr(settings, "ORTHOLOG_MAX_DECOMPRESSED_BYTES", 536_870_912)

    # 2. ZIP magic -> UNSUPPORTED_COMPRESSION
    with pytest.raises(HTTPException) as exc_zip:
        upload_routes.upload_ortholog_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"PK\x03\x04fakezip"), filename="bad.blast.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_zip.value, 400, "UNSUPPORTED_COMPRESSION", tmp_path)

    # 3. Binary NUL content -> BINARY_CONTENT
    with pytest.raises(HTTPException) as exc_bin:
        upload_routes.upload_ortholog_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"Asa1\x00\x01binary"), filename="bad.tsv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_bin.value, 400, "BINARY_CONTENT", tmp_path)

    # 4. 2-column GO TSV uploaded as ortholog -> NO_VALID_ROWS
    with pytest.raises(HTTPException) as exc_go:
        upload_routes.upload_ortholog_file(
            file_id=ds.id,
            file=UploadFile(
                file=io.BytesIO(b"Asa7G00001\tGO:0006357\nAsa7G00002\tGO:0009651\n"),
                filename="go.tsv",
            ),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_go.value, 400, "NO_VALID_ROWS", tmp_path)

    # 5. Truncated gzip -> CORRUPT_GZIP
    valid_gz = _gz_bytes(
        b"Asa7G00205\tAT3G05510.2\t29.07\t258\t141\t6\t1\t232\t115\t356\t2e-21\t99.4\n"
    )
    with pytest.raises(HTTPException) as exc_corrupt:
        upload_routes.upload_ortholog_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(valid_gz[:-5]), filename="trunc.blast.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_corrupt.value, 400, "CORRUPT_GZIP", tmp_path)


def test_upload_ortholog_active_job_409_and_other_user_404(tmp_path: Path, monkeypatch):
    owner, other, ds, db = _make_fake_env(tmp_path, monkeypatch, active_job=True)
    uf = UploadFile(file=io.BytesIO(b"Q1\tAT1G00001.1\t50\t1\t0\t1\t1\t1\t1\t1\t1e-5\t50\n"), filename="a.tsv")

    with pytest.raises(HTTPException) as exc_other:
        upload_routes.upload_ortholog_file(file_id=ds.id, file=uf, db=db, user=other)
    assert exc_other.value.status_code == 404

    with pytest.raises(HTTPException) as exc_active:
        upload_routes.upload_ortholog_file(file_id=ds.id, file=uf, db=db, user=owner)
    assert exc_active.value.status_code == 409
    assert exc_active.value.detail["code"] == "ACTIVE_JOB"

    with pytest.raises(HTTPException) as exc_del_active:
        upload_routes.delete_ortholog_file(file_id=ds.id, db=db, user=owner)
    assert exc_del_active.value.status_code == 409


def test_delete_ortholog_checks_under_storage_and_clears_metadata(tmp_path: Path, monkeypatch):
    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch)
    ds_dir = tmp_path / "datasets" / str(ds.id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    idx = ds_dir / "ortholog_besthit.tsv.gz"
    idx.write_bytes(_gz_bytes(b"query_id\tsubject_locus\n"))

    outside = tmp_path.parent / "outside_should_not_delete.txt"
    outside.write_text("keep", encoding="utf-8")

    try:
        ds.extra_metadata = {"ortholog": {"filename": "x.blast.gz", "path": str(outside), "n_queries": 1}}
        res_out = upload_routes.delete_ortholog_file(file_id=ds.id, db=db, user=owner)
        assert res_out == {"deleted": True}
        assert outside.exists()

        ds.extra_metadata = {"ortholog": {"filename": "x.blast.gz", "path": str(idx), "n_queries": 1}}
        res_in = upload_routes.delete_ortholog_file(file_id=ds.id, db=db, user=owner)
        assert res_in == {"deleted": True}
        assert not idx.exists()
        assert "ortholog" not in ds.extra_metadata

        listed = upload_routes.list_uploaded_datasets(db=db, user=owner)
        assert listed[0]["has_ortholog"] is False
        assert listed[0]["ortholog_filename"] is None
        assert listed[0]["ortholog_n_genes"] == 0
    finally:
        outside.unlink(missing_ok=True)
