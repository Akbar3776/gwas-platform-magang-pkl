"""Unit tests untuk G07: parser/resolver go-basic.obo, cache, dan batas ukuran."""

from __future__ import annotations

import gzip
import logging
import os
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.go_ontology_service import (
    clear_go_ontology_cache,
    load_go_ontology,
    load_go_ontology_safe,
)
from app.services.upload_validation import UploadValidationError

FIXTURE_OBO = Path(__file__).parent / "fixtures" / "go_mini.obo"


def test_go_ontology_resolution_cases_and_readonly_mappings():
    clear_go_ontology_cache()
    ont = load_go_ontology(FIXTURE_OBO)
    assert ont is not None
    assert ont.version == "releases/2099-01-01"
    assert len(ont.sha256) == 64
    assert "part_of" not in ont.terms

    with pytest.raises(TypeError):
        ont.terms["GO:9999999"] = None
    with pytest.raises(TypeError):
        ont.alt_to_primary["GO:9999999"] = "GO:0006357"

    r_ok = ont.resolve("GO:0008146")
    assert r_ok.status == "ok"
    assert r_ok.final_id == "GO:0008146"
    assert r_ok.aspect == "MF"
    assert r_ok.name == "sulfotransferase activity"
    assert r_ok.via_alt_id is False

    r_alt = ont.resolve("go:0016021")
    assert r_alt.status == "alt_remapped"
    assert r_alt.final_id == "GO:0016020"
    assert r_alt.aspect == "CC"
    assert r_alt.name == "membrane"
    assert r_alt.via_alt_id is True

    r_obs1 = ont.resolve("GO:0000275")
    assert r_obs1.status == "obsolete_remapped"
    assert r_obs1.final_id == "GO:0045259"

    r_chain = ont.resolve("GO:9000001")
    assert r_chain.status == "obsolete_remapped"
    assert r_chain.final_id == "GO:0006357"
    assert r_chain.aspect == "BP"

    r_to_alt = ont.resolve("GO:9000006")
    assert r_to_alt.status == "obsolete_remapped"
    assert r_to_alt.final_id == "GO:0016020"

    for dropped_id in ("GO:0055114", "GO:9000003", "GO:9000004"):
        r_drop = ont.resolve(dropped_id)
        assert r_drop.status == "obsolete_dropped"
        assert r_drop.final_id is None
        assert r_drop.aspect is None

    r_unk = ont.resolve("GO:7777777")
    assert r_unk.status == "unknown"
    assert r_unk.final_id == "GO:7777777"
    assert r_unk.aspect is None


def test_go_ontology_cache_mtime_missing_warn_and_gz(tmp_path: Path, monkeypatch, caplog):
    clear_go_ontology_cache()

    # Missing file -> None + tepat satu WARN
    missing = tmp_path / "missing.obo"
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(missing))
    with caplog.at_level(logging.WARNING):
        assert load_go_ontology() is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1

    # Cache hit & invalidation via mtime
    obo_copy = tmp_path / "local.obo"
    obo_copy.write_bytes(FIXTURE_OBO.read_bytes())
    o1 = load_go_ontology(obo_copy)
    o2 = load_go_ontology(obo_copy)
    assert o1 is o2

    st = obo_copy.stat()
    os.utime(obo_copy, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    o3 = load_go_ontology(obo_copy)
    assert o3 is not o1

    # GO_OBO_PATH="" -> default STORAGE_ROOT/reference/go/go-basic.obo
    default_obo = tmp_path / "reference" / "go" / "go-basic.obo"
    default_obo.parent.mkdir(parents=True, exist_ok=True)
    default_obo.write_bytes(FIXTURE_OBO.read_bytes())
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "GO_OBO_PATH", "")
    o_def = load_go_ontology()
    assert o_def is not None
    assert o_def.version == "releases/2099-01-01"

    # .obo.gz didukung + batas GO_OBO_MAX_BYTES diterapkan
    gz_obo = tmp_path / "mini.obo.gz"
    with gzip.open(gz_obo, "wb") as gz:
        gz.write(FIXTURE_OBO.read_bytes())
    o_gz = load_go_ontology(gz_obo)
    assert o_gz is not None
    assert o_gz.resolve("GO:0006357").status == "ok"

    clear_go_ontology_cache()
    monkeypatch.setattr(settings, "GO_OBO_MAX_BYTES", 128)
    with pytest.raises(UploadValidationError) as exc_info:
        load_go_ontology(gz_obo)
    assert exc_info.value.code == "DECOMPRESSED_TOO_LARGE"


def test_load_go_ontology_safe_handles_corrupt_obo_and_deduplicates_logs(tmp_path, monkeypatch, caplog):
    import io
    import uuid
    from starlette.datastructures import UploadFile
    from app.api.routes import upload as upload_routes
    from app.db.engine.models import Dataset, UploadStatus, User, UserRole
    from app.services.go_enrichment_service import run_go_enrichment_analysis

    # 1. OBO missing -> WARN dicatat tepat 1 kali meski dipanggil 5 kali
    clear_go_ontology_cache()
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            assert load_go_ontology_safe() is None
    missing_warns = [r for r in caplog.records if "Ontologi GO tidak ditemukan" in r.message]
    assert len(missing_warns) == 1

    # 2. OBO melebihi batas GO_OBO_MAX_BYTES -> load_go_ontology_safe return None (tidak raise) dan log 1 kali
    bad_obo = tmp_path / "oversized.obo"
    bad_obo.write_bytes(FIXTURE_OBO.read_bytes())
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(bad_obo))
    monkeypatch.setattr(settings, "GO_OBO_MAX_BYTES", 64)
    clear_go_ontology_cache()

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert load_go_ontology_safe() is None
        assert load_go_ontology_safe() is None
    err_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(err_logs) == 1

    # 3. run_go_enrichment_analysis dan upload_go_annotation_file tidak menghasilkan 500
    go_tsv = tmp_path / "go.tsv"
    go_tsv.write_text("G1\tGO:0006357\n", encoding="utf-8")
    res = run_go_enrichment_analysis(
        mapped_rows=[{"nearest_gene": "G1", "category": "Genik", "snp": "s1"}],
        candidate_genes_list=["G1"],
        go_mapping_path=go_tsv,
    )
    assert res["go_stats"]["obo"] == {"available": False}

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(id=uuid.uuid4(), email="o@e.com", hashed_password="x", role=UserRole.RESEARCHER)
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="a.vcf",
        storage_path=str(tmp_path / "a.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )

    class _DB:
        def get(self, m, oid):
            return ds if m is Dataset else None

        def query(self, *a):
            class _Q:
                def filter(self, *x):
                    return self

                def all(self):
                    return []

            return _Q()

        def commit(self):
            pass

    up = upload_routes.upload_go_annotation_file(
        file_id=ds.id,
        file=UploadFile(file=io.BytesIO(b"G1\tGO:0006357\n"), filename="mini.tsv"),
        db=_DB(),
        user=owner,
    )
    assert up["obo"] is None
