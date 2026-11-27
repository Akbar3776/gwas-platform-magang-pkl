"""Unit tests untuk G02–G04: keamanan upload GO, resolver path tunggal, dan CSV injection."""

from __future__ import annotations

import gzip
import io
import uuid
from pathlib import Path

from fastapi import HTTPException
import pytest
from starlette.datastructures import UploadFile

from app.api.routes import upload as upload_routes
from app.core.config import settings
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus, User, UserRole
from app.services.go_enrichment_service import parse_go_annotation_file


def _gz_bytes(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(payload)
    return buf.getvalue()


def _make_fake_env(tmp_path: Path, monkeypatch, *, active_job: bool = False):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(id=uuid.uuid4(), email="owner@example.com", hashed_password="x", role=UserRole.RESEARCHER)
    other = User(id=uuid.uuid4(), email="other@example.com", hashed_password="x", role=UserRole.RESEARCHER)
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

                def all(self):
                    return jobs

            return _Q()

        def commit(self):
            pass

    return owner, other, ds, _DB()


def _assert_safe_error(exc: HTTPException, expected_status: int, expected_code: str, tmp_path: Path) -> None:
    assert exc.status_code == expected_status
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == expected_code
    msg = exc.detail.get("message", "")
    assert isinstance(msg, str) and msg
    assert "Traceback" not in msg
    assert str(tmp_path) not in msg
    assert 'File "' not in msg


def test_upload_generic_go_gz_and_canonical_cleanup(tmp_path: Path, monkeypatch):
    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch)
    gz_raw = _gz_bytes(b"Asa7G00001.1\tGO:0006357\nAsa7G00002.1\tGO:0009651\n")
    uf = UploadFile(file=io.BytesIO(gz_raw), filename="Sample.GO.gz")

    out = upload_routes.upload_go_annotation_file(file_id=ds.id, file=uf, db=db, user=owner)
    assert "path" not in out
    assert out["compressed"] is True
    assert out["format"] == "tsv"
    assert out["n_genes_raw"] == 2
    assert out["n_mappings_raw"] == 2
    assert out["n_genes"] == 2
    assert out["n_mappings"] == 2
    assert out["n_data_lines"] == 2
    assert out["n_invalid_lines"] == 0
    assert out["obo"] is None
    assert out["uploaded_at"]

    meta = (ds.extra_metadata or {})["go_annotation"]
    assert meta["path"].endswith("go_annotation.tsv.gz")
    assert Path(meta["path"]).is_file()
    assert meta["obo"] is None

    # Upload plain sesudah .gz -> tepat satu go_annotation* dan nol .part
    plain_uf = UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="plain.tsv")
    out2 = upload_routes.upload_go_annotation_file(file_id=ds.id, file=plain_uf, db=db, user=owner)
    assert out2["compressed"] is False
    ds_dir = tmp_path / "datasets" / str(ds.id)
    assert sorted(p.name for p in ds_dir.glob("go_annotation*")) == ["go_annotation.tsv"]
    assert list(ds_dir.glob("*.part*")) == []
    assert list(ds_dir.glob(".go_annotation*")) == []


def test_upload_go_abuse_cases_and_atomic_preservation(tmp_path: Path, monkeypatch):
    owner, other, ds, db = _make_fake_env(tmp_path, monkeypatch)

    # Upload valid awal harus tetap bertahan setelah semua serangan gagal
    good_raw = _gz_bytes(b"G1\tGO:0006357\nG2\tGO:0009651\n")
    good_out = upload_routes.upload_go_annotation_file(
        file_id=ds.id,
        file=UploadFile(file=io.BytesIO(good_raw), filename="good.GO.gz"),
        db=db,
        user=owner,
    )
    good_sha = good_out["sha256"]
    good_path = Path(ds.extra_metadata["go_annotation"]["path"])
    assert good_path.is_file()

    # User lain -> 404
    with pytest.raises(HTTPException) as exc_404:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="a.tsv"),
            db=db,
            user=other,
        )
    assert exc_404.value.status_code == 404

    # Ekstensi salah -> 400 GO_EXTENSION
    with pytest.raises(HTTPException) as exc_ext:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="bad.csv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_ext.value, 400, "GO_EXTENSION", tmp_path)

    # 1. Bom ber-newline melewati batas dekompresi -> 413 DECOMPRESSED_TOO_LARGE
    monkeypatch.setattr(settings, "GO_MAPPING_MAX_DECOMPRESSED_BYTES", 512 * 1024)
    bomb_gz = _gz_bytes((b"G1\tGO:0006357\n" * 60000))
    with pytest.raises(HTTPException) as exc_bomb:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(bomb_gz), filename="bomb.GO.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_bomb.value, 413, "DECOMPRESSED_TOO_LARGE", tmp_path)
    monkeypatch.setattr(settings, "GO_MAPPING_MAX_DECOMPRESSED_BYTES", 134_217_728)

    # 2. Terlalu banyak baris -> 413 TOO_MANY_LINES
    monkeypatch.setattr(settings, "GO_MAPPING_MAX_LINES", 20)
    many_gz = _gz_bytes(b"\n" * 50)
    with pytest.raises(HTTPException) as exc_lines:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(many_gz), filename="many.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_lines.value, 413, "TOO_MANY_LINES", tmp_path)
    monkeypatch.setattr(settings, "GO_MAPPING_MAX_LINES", 1_000_000)

    # 3. Baris terlalu panjang tanpa newline -> 400 LINE_TOO_LONG
    monkeypatch.setattr(settings, "UPLOAD_TEXT_MAX_LINE_BYTES", 1024)
    long_gz = _gz_bytes(b"A" * 4096)
    with pytest.raises(HTTPException) as exc_long:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(long_gz), filename="long.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_long.value, 400, "LINE_TOO_LONG", tmp_path)
    monkeypatch.setattr(settings, "UPLOAD_TEXT_MAX_LINE_BYTES", 65536)

    # 4. Nama .gz tetapi isi teks biasa -> 400 GZIP_INVALID
    with pytest.raises(HTTPException) as exc_not_gz:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="fake.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_not_gz.value, 400, "GZIP_INVALID", tmp_path)

    # 5. Zip di-rename .gz -> 400 UNSUPPORTED_COMPRESSION
    with pytest.raises(HTTPException) as exc_zip:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"PK\x03\x04zipdata"), filename="fake.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_zip.value, 400, "UNSUPPORTED_COMPRESSION", tmp_path)

    # 6. File biner NUL -> 400 BINARY_CONTENT
    with pytest.raises(HTTPException) as exc_bin:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"G1\x00GO:0006357\n"), filename="bin.tsv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_bin.value, 400, "BINARY_CONTENT", tmp_path)

    # 7. File BLAST diunggah sebagai GO -> 400 NO_VALID_ROWS
    blast_line = b"Asa7G00205\tAT3G05510.2\t29.07\t258\t141\t6\t1\t232\t115\t356\t2e-21\t99.4\n"
    with pytest.raises(HTTPException) as exc_blast:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(blast_line), filename="blast.tsv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_blast.value, 400, "NO_VALID_ROWS", tmp_path)

    # 8 & 9. Separuh lebih baris invalid (termasuk =HYPERLINK) -> 400 TOO_MANY_INVALID_LINES tanpa membocorkan isi baris
    mixed = b"G1\tGO:0006357\n=HYPERLINK(\"x\")\tGO:0006357\nBADLINE\tNO_GO\nBAD2\n"
    with pytest.raises(HTTPException) as exc_ratio:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(mixed), filename="mixed.tsv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_ratio.value, 400, "TOO_MANY_INVALID_LINES", tmp_path)
    assert "HYPERLINK" not in exc_ratio.value.detail["message"]
    assert "2" in exc_ratio.value.detail["message"]

    # 10. Gzip terpotong -> 400 CORRUPT_GZIP
    with pytest.raises(HTTPException) as exc_corrupt:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(good_raw[:-4]), filename="trunc.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_corrupt.value, 400, "CORRUPT_GZIP", tmp_path)

    # 11. Upload valid sebelumnya dan metadata tidak tersentuh setelah semua kegagalan
    assert ds.extra_metadata["go_annotation"]["sha256"] == good_sha
    assert good_path.is_file()
    ds_dir = tmp_path / "datasets" / str(ds.id)
    assert list(ds_dir.glob(".go_annotation*")) == []


def test_upload_go_active_job_returns_409_and_token_parsing(tmp_path: Path, monkeypatch):
    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch, active_job=True)
    with pytest.raises(HTTPException) as exc_409:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="go.tsv"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_409.value, 409, "ACTIVE_JOB", tmp_path)

    tsv = tmp_path / "tokens.tsv"
    tsv.write_bytes(
        b"\xef\xbb\xbfgene_id\tgo_id\n"
        b"G1\tGO:0000001,GO:0000002\n"
        b"G2\tgo:0000003\n"
        b'"G3"\t"GO:0000004"\n'
        b"G4\tGO:00001\n"
    )
    ann, summary = parse_go_annotation_file(tsv)
    assert summary["n_data_lines"] == 4
    assert summary["n_invalid_lines"] == 1
    assert summary["first_invalid_line"] == 5
    assert summary["n_mappings_raw"] == 4
    assert len(ann["G1"]) == 2
    assert ann["G2"][0]["go_id"] == "GO:0000003"
    assert ann["G3"][0]["go_id"] == "GO:0000004"


def test_resolve_dataset_go_mapping_path_single_resolver_and_txt_gz(tmp_path: Path, monkeypatch):
    from app.api.routes import results as results_routes
    from app.services.go_enrichment_service import resolve_dataset_go_mapping_path

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    ds_id = uuid.uuid4()
    ds_dir = storage_root / "datasets" / str(ds_id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    txt_gz = ds_dir / "go_annotation.txt.gz"
    txt_gz.write_bytes(_gz_bytes(b"G1\tGO:0006357\n"))

    outside = tmp_path / "outside_go.tsv"
    outside.write_text("G1\tGO:0006357\n", encoding="utf-8")

    ds = Dataset(
        id=ds_id,
        owner_id=uuid.uuid4(),
        original_filename="c.vcf",
        storage_path=str(storage_root / "c.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
        extra_metadata={"go_annotation": {"path": str(outside)}},
    )

    assert resolve_dataset_go_mapping_path(ds) == txt_gz

    class _FakeDB:
        def query(self, *args):
            class _Q:
                def filter(self, *a):
                    return self

                def first(self):
                    return ds

            return _Q()

    job = AnalysisJob(id=uuid.uuid4(), dataset_id=ds.id, triggered_by=ds.owner_id, overall_status=JobStatus.COMPLETED)
    assert results_routes._resolve_dataset_go_mapping_path(_FakeDB(), job) == txt_gz


def test_go_csv_export_neutralizes_formula_injection_and_matches_client_rules(tmp_path: Path, monkeypatch):
    import csv
    from app.services.go_enrichment_service import csv_safe_cell, run_go_enrichment_analysis

    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    assert csv_safe_cell("-") == "'-"
    assert csv_safe_cell("-0.42") == "-0.42"
    assert csv_safe_cell("-1.5e-8") == "-1.5e-8"
    assert csv_safe_cell("\t1") == "'\t1"
    assert csv_safe_cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert csv_safe_cell(None) == ""
    assert csv_safe_cell(True) == "True"
    assert csv_safe_cell(-0.42) == "-0.42"

    tsv = tmp_path / "go.tsv"
    tsv.write_text('G_REF\tGO:0006357\tBP\t@SUM(1)\nG_OK\tGO:0009651\tBP\t-0.42\n', encoding="utf-8")
    # Tambahkan explicit GO pada kandidat agar =HYPERLINK("x") yang tidak divalidasi GFF juga teruji di CSV
    out_dir = tmp_path / "out"
    res = run_go_enrichment_analysis(
        mapped_rows=[
            {
                "snp": "s1",
                "chr": "1",
                "pos": 100,
                "p": 1e-8,
                "nearest_gene": '=HYPERLINK("x")',
                "category": "Genik",
                "closest_gene": {"gene_id": '=HYPERLINK("x")', "go_ids": ["GO:0006357"]},
            },
            {
                "snp": "s2",
                "chr": "1",
                "pos": 200,
                "p": 1e-8,
                "nearest_gene": "G_OK",
                "category": "Genik",
            },
        ],
        candidate_genes_list=['=HYPERLINK("x")', "G_OK"],
        trait_name="T",
        output_dir=out_dir,
        go_mapping_path=tsv,
    )
    # JSON tidak disanitasi
    assert res["go_table"][0]["gene_id"] == '=HYPERLINK("x")'
    assert res["go_table"][0]["ortholog"] == "-"

    with (out_dir / "Hasil_GO_Enrichment.csv").open("r", encoding="utf-8", newline="") as ef:
        enrich_rows = list(csv.reader(ef))
    enrich_by_go = {r[0]: r for r in enrich_rows[1:]}
    assert enrich_by_go["GO:0006357"][1] == "'@SUM(1)"
    assert enrich_by_go["GO:0006357"][8] == '\'=HYPERLINK("x")'
    assert enrich_by_go["GO:0009651"][1] == "-0.42"

    with (out_dir / "Tabel_GO_Kandidat.csv").open("r", encoding="utf-8", newline="") as tf:
        tbl_rows = list(csv.reader(tf))
    assert tbl_rows[0] == [
        "Gene_ID",
        "Ortholog",
        "Biological_Process",
        "Cellular_Component",
        "Molecular_Function",
        "Unknown_Domain",
        "Source",
    ]
    tbl_by_gene = {r[0]: r for r in tbl_rows[1:]}
    assert '\'=HYPERLINK("x")' in tbl_by_gene
    assert tbl_by_gene['\'=HYPERLINK("x")'][1] == "'-"
    assert tbl_by_gene['\'=HYPERLINK("x")'][2].startswith("'@SUM(1)")
    assert tbl_by_gene['\'=HYPERLINK("x")'][5] == "'-"


def test_upload_go_parses_only_once_and_preserves_old_on_obo_error(tmp_path: Path, monkeypatch):
    from app.services import go_enrichment_service as go_svc
    from app.services.go_ontology_service import clear_go_ontology_cache

    owner, _other, ds, db = _make_fake_env(tmp_path, monkeypatch)

    parse_calls = 0
    orig_parse = go_svc.parse_go_annotation_file

    def _spy_parse(path):
        nonlocal parse_calls
        parse_calls += 1
        return orig_parse(path)

    monkeypatch.setattr(go_svc, "parse_go_annotation_file", _spy_parse)

    good_bytes = _gz_bytes(b"Asa7G00001.1\tGO:0006357\n")
    out = upload_routes.upload_go_annotation_file(
        file_id=ds.id,
        file=UploadFile(file=io.BytesIO(good_bytes), filename="initial.GO.gz"),
        db=db,
        user=owner,
    )
    assert parse_calls == 1
    assert out["n_genes_raw"] == 1
    assert out["n_mappings_raw"] == 1
    assert out["n_genes"] == 1
    assert out["n_mappings"] == 1

    saved_before = Path(ds.extra_metadata["go_annotation"]["path"]).read_bytes()
    meta_before = dict(ds.extra_metadata["go_annotation"])

    fixture_obo = Path(__file__).parent / "fixtures" / "go_mini.obo"
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(fixture_obo))
    clear_go_ontology_cache()
    monkeypatch.setattr(
        go_svc,
        "normalize_go_annotations_with_obo",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("obo normalization boom")),
    )

    with pytest.raises(HTTPException) as exc_err:
        upload_routes.upload_go_annotation_file(
            file_id=ds.id,
            file=UploadFile(file=io.BytesIO(_gz_bytes(b"Asa2G00002.1\tGO:0009651\n")), filename="second.GO.gz"),
            db=db,
            user=owner,
        )
    _assert_safe_error(exc_err.value, 400, "GO_PARSE_ERROR", tmp_path)
    assert Path(ds.extra_metadata["go_annotation"]["path"]).read_bytes() == saved_before
    assert ds.extra_metadata["go_annotation"] == meta_before
    ds_dir = tmp_path / "datasets" / str(ds.id)
    assert list(ds_dir.glob("*.part*")) == []
    assert list(ds_dir.glob(".go_annotation*")) == []
