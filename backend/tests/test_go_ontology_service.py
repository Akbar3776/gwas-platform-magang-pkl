"""Unit tests untuk G07: parser/resolver go-basic.obo, cache, dan batas ukuran."""

from __future__ import annotations

import gzip
import logging
import os
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.go_ontology_service import clear_go_ontology_cache, load_go_ontology
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
