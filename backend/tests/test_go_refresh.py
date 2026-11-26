"""Unit tests untuk G12: fingerprint input GO dan penyegaran non-destruktif job COMPLETED."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from app.api.routes import results as results_routes
from app.core.config import settings
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus
from app.services import go_enrichment_service


def _setup_job_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)

    ds_id = uuid.uuid4()
    ds_dir = tmp_path / "datasets" / str(ds_id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    gff = ds_dir / "annotation.gff3"
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G1\n"
        "chr1\tEVM\tgene\t300\t400\t.\t+\t.\tID=G2\n",
        encoding="utf-8",
    )
    go_map = ds_dir / "go_annotation.tsv"
    go_map.write_text("G1.1\tGO:0006357\nG2.1\tGO:0009651\n", encoding="utf-8")

    ds = Dataset(
        id=ds_id,
        owner_id=uuid.uuid4(),
        original_filename="c.vcf",
        storage_path=str(ds_dir / "c.vcf"),
        annotation_file_path=str(gff),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=ds.owner_id,
        overall_status=JobStatus.COMPLETED,
        parameters={
            "phenotype": {"trait": "Weight"},
            "annotation": {"feature_type": "gene"},
            "contig_filter": {"pattern": settings.NON_PRIMARY_CONTIG_REGEX},
        },
    )

    class _FakeDB:
        def query(self, model):
            class _Q:
                def filter(self, *a):
                    return self

                def first(self):
                    if model is Dataset:
                        return ds
                    if model is AnalysisJob:
                        return job
                    return None

            return _Q()

    anno_dir = tmp_path / "jobs" / str(job.id) / "annotation"
    anno_dir.mkdir(parents=True, exist_ok=True)
    orig_rows = [
        {
            "snp": "s1",
            "chr": "1",
            "pos": 150,
            "p": 1e-8,
            "nearest_gene": "G1",
            "category": "Genik",
            "genes_in_window": [],
        }
    ]
    cand_payload = {
        "status": "completed",
        "window_bp": 100000,
        "window_kb": 100,
        "counts": {"Genik": 1, "Near-gen": 0, "Intergenik": 0},
        "candidate_genes_list": ["G1"],
        "rows": orig_rows,
        "mappings": orig_rows,
        "go_enrichment": {"status": "unavailable", "go_table": [{"gene_id": "G1", "go_terms": []}], "enrichment_terms": []},
    }
    legacy_go = {
        "status": "unavailable",
        "trait": "Weight",
        "total_genes": 1,
        "background_total": 0,
        "go_table": [{"gene_id": "G1", "go_terms": [], "source": "unavailable"}],
        "enrichment_terms": [],
    }
    (anno_dir / "candidate_genes.json").write_text(json.dumps(cand_payload), encoding="utf-8")
    (anno_dir / "go_enrichment.json").write_text(json.dumps(legacy_go), encoding="utf-8")

    return _FakeDB(), ds, job, anno_dir, gff, go_map, legacy_go


def test_refresh_recomputes_legacy_cache_once_backs_up_and_preserves_rows(tmp_path: Path, monkeypatch):
    db, _ds, job, anno_dir, gff, go_map, legacy_go = _setup_job_env(tmp_path, monkeypatch)

    calls = 0
    orig_run = go_enrichment_service.run_go_enrichment_analysis

    def _spy_run(**kwargs):
        nonlocal calls
        calls += 1
        return orig_run(**kwargs)

    monkeypatch.setattr(go_enrichment_service, "run_go_enrichment_analysis", _spy_run)

    # 1. GET /go-enrichment pada payload usang -> recompute tepat 1 kali + backup + tulis atomik
    out = results_routes.get_go_enrichment_data(job.id, db=db)
    assert calls == 1
    assert out["status"] == "completed"
    assert out["go_stats"]["fingerprint"]
    assert (anno_dir / "go_enrichment.previous.json").is_file()
    assert json.loads((anno_dir / "go_enrichment.previous.json").read_text(encoding="utf-8")) == legacy_go
    assert (anno_dir / "Hasil_GO_Enrichment.csv").is_file()
    assert (anno_dir / "Tabel_GO_Kandidat.csv").is_file()
    assert list(anno_dir.glob("*.tmp*")) == []

    saved_cand = json.loads((anno_dir / "candidate_genes.json").read_text(encoding="utf-8"))
    assert saved_cand["go_enrichment"]["go_stats"]["fingerprint"] == out["go_stats"]["fingerprint"]
    assert saved_cand["rows"][0]["snp"] == "s1"

    # 2. Fingerprint sama -> 0 panggilan recompute baik lewat /go-enrichment maupun /candidate-genes
    calls = 0
    out2 = results_routes.get_go_enrichment_data(job.id, db=db)
    cand2 = results_routes.get_candidate_genes_data(job.id, db=db)
    assert calls == 0
    assert out2["go_stats"]["fingerprint"] == cand2["go_enrichment"]["go_stats"]["fingerprint"]

    # 3. Mapping diganti -> recompute tepat 1 kali lewat /candidate-genes; rows identik
    go_map.write_text("G1.1\tGO:0006357\nG2.1\tGO:0009651\nG3.1\tGO:0008146\n", encoding="utf-8")
    calls = 0
    cand3 = results_routes.get_candidate_genes_data(job.id, db=db)
    assert calls == 1
    assert cand3["rows"][0]["snp"] == "s1"
    assert cand3["go_enrichment"]["background_total"] == 2  # G3 tidak di GFF

    # 4. GFF diubah -> recompute tepat 1 kali
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G1\n"
        "chr1\tEVM\tgene\t300\t400\t.\t+\t.\tID=G2\n"
        "chr1\tEVM\tgene\t500\t600\t.\t+\t.\tID=G3\n",
        encoding="utf-8",
    )
    calls = 0
    out4 = results_routes.get_go_enrichment_data(job.id, db=db)
    assert calls == 1
    assert out4["background_total"] == 3


def test_refresh_non_destructive_when_gff_missing_or_recompute_raises(tmp_path: Path, monkeypatch, caplog):
    db, _ds, job, anno_dir, gff, _go_map, _legacy_go = _setup_job_env(tmp_path, monkeypatch)

    # Hangatkan cache valid dengan fingerprint
    first = results_routes.get_go_enrichment_data(job.id, db=db)
    assert first["status"] == "completed"
    snapshot_before = (anno_dir / "go_enrichment.json").read_text(encoding="utf-8")

    # 1. GFF dihapus -> tidak recompute, respons stale=True & stale_reason="gff_unavailable", file utuh
    gff.unlink()
    calls = 0
    orig_run = go_enrichment_service.run_go_enrichment_analysis

    def _spy(**kwargs):
        nonlocal calls
        calls += 1
        return orig_run(**kwargs)

    monkeypatch.setattr(go_enrichment_service, "run_go_enrichment_analysis", _spy)
    stale_gff = results_routes.get_go_enrichment_data(job.id, db=db)
    assert calls == 0
    assert stale_gff["stale"] is True
    assert stale_gff["stale_reason"] == "gff_unavailable"
    assert (anno_dir / "go_enrichment.json").read_text(encoding="utf-8") == snapshot_before

    # 2. GFF kembali, ubah agar fingerprint beda, lalu recompute raise -> cache lama + stale_reason="recompute_failed" + log
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G1\n",
        encoding="utf-8",
    )

    def _boom(**kwargs):
        raise RuntimeError("boom recompute")

    monkeypatch.setattr(go_enrichment_service, "run_go_enrichment_analysis", _boom)
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        stale_err = results_routes.get_go_enrichment_data(job.id, db=db)
    assert stale_err["stale"] is True
    assert stale_err["stale_reason"] == "recompute_failed"
    assert stale_err["status"] == "completed"
    assert (anno_dir / "go_enrichment.json").read_text(encoding="utf-8") == snapshot_before
    assert any("boom recompute" in r.exc_text or "boom recompute" in r.message for r in caplog.records if r.exc_text or r.message)


def test_results_router_retains_require_job_owner_dependency():
    dep_calls = [getattr(dep.dependency, "__name__", "") for dep in results_routes.router.dependencies]
    assert "require_job_owner" in dep_calls


def test_refresh_parallel_requests_compute_once_and_keep_original_backup(tmp_path: Path, monkeypatch):
    import threading
    import time

    db, _ds, job, anno_dir, _gff, _go_map, legacy_go = _setup_job_env(tmp_path, monkeypatch)

    calls = 0
    calls_lock = threading.Lock()
    orig_run = go_enrichment_service.run_go_enrichment_analysis

    def _slow_run(**kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        out = orig_run(**kwargs)
        time.sleep(0.5)
        return out

    monkeypatch.setattr(go_enrichment_service, "run_go_enrichment_analysis", _slow_run)

    start = threading.Barrier(2, timeout=20)
    results: dict[str, dict] = {}
    errors: list[BaseException] = []

    def _call(name, fn):
        try:
            start.wait()
            results[name] = fn(job.id, db=db)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_call, args=("go", results_routes.get_go_enrichment_data)),
        threading.Thread(target=_call, args=("cand", results_routes.get_candidate_genes_data)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    backup_path = anno_dir / "go_enrichment.previous.json"
    backup = json.loads(backup_path.read_text(encoding="utf-8")) if backup_path.is_file() else None
    assert not errors, errors
    assert calls == 1
    assert backup == legacy_go
    assert results["go"]["go_stats"]["fingerprint"] == results["cand"]["go_enrichment"]["go_stats"]["fingerprint"]
    assert list(anno_dir.glob("*.tmp*")) == []


def test_refresh_skips_write_when_another_worker_already_refreshed(tmp_path: Path, monkeypatch):
    db, _ds, job, anno_dir, _gff, _go_map, _legacy_go = _setup_job_env(tmp_path, monkeypatch)
    orig_run = go_enrichment_service.run_go_enrichment_analysis
    go_file = anno_dir / "go_enrichment.json"

    def _other_worker_run(**kwargs):
        out = orig_run(**kwargs)
        go_file.write_text(json.dumps({**out, "_writer": "other_worker"}), encoding="utf-8")
        return out

    monkeypatch.setattr(go_enrichment_service, "run_go_enrichment_analysis", _other_worker_run)

    out = results_routes.get_go_enrichment_data(job.id, db=db)
    assert out["_writer"] == "other_worker"
    assert json.loads(go_file.read_text(encoding="utf-8"))["_writer"] == "other_worker"
    assert not (anno_dir / "go_enrichment.previous.json").exists()


def test_refresh_write_oserror_returns_200_and_keeps_old_file(tmp_path: Path, monkeypatch, caplog):
    import os

    db, _ds, job, anno_dir, _gff, _go_map, legacy_go = _setup_job_env(tmp_path, monkeypatch)
    orig_replace = os.replace

    def _locked_replace(src, dst):
        if Path(dst).name == "go_enrichment.json":
            raise PermissionError("locked by concurrent reader")
        return orig_replace(src, dst)

    monkeypatch.setattr(results_routes.os, "replace", _locked_replace)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        out = results_routes.get_go_enrichment_data(job.id, db=db)

    assert out["status"] == "completed"
    assert out["go_stats"]["fingerprint"]
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    assert json.loads((anno_dir / "go_enrichment.json").read_text(encoding="utf-8")) == legacy_go
    assert list(anno_dir.glob("*.tmp*")) == []
