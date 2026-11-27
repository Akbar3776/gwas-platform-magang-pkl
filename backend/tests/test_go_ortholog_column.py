"""Unit tests untuk G15: kolom Ortholog, detail tooltip, CSV aman, go_stats, dan refresh."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import uuid

import pytest

from app.api.routes import results as results_routes
from app.core.config import settings
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus
from app.services.go_enrichment_service import run_go_enrichment_analysis
from app.services.ortholog_service import build_besthit_index, clear_ortholog_cache


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_ortholog_cache()
    yield
    clear_ortholog_cache()


def _write_mini_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    gff = tmp_path / "mini.gff3"
    gff.write_text(
        "\n".join(
            [
                "##gff-version 3",
                "chr1\t.\tgene\t1\t100\t.\t+\t.\tID=Asa7G00205",
                "chr1\t.\tgene\t200\t300\t.\t+\t.\tID=Asa2G00002",
                "chr1\t.\tgene\t400\t500\t.\t+\t.\tID=Asa9G00009",
                "chr1\t.\tgene\t600\t700\t.\t+\t.\tID=AsaNoHit",
                "chr1\t.\tgene\t800\t900\t.\t+\t.\tID=AsaHitNoGo",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    go_tsv = tmp_path / "mini.go.tsv"
    go_tsv.write_text(
        "\n".join(
            [
                "Asa7G00205.1\tGO:0006357",
                "Asa2G00002\tGO:0009651",
                "Asa9G00009\tGO:0005524",
                "AsaNoHit\tGO:0006357",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    blast_tsv = tmp_path / "mini.blast.tsv"
    blast_tsv.write_text(
        "\n".join(
            [
                "Query_id\tSubject_id\talignment_length\tmismatches\tgap_openings\tqstart\tqend\tsstart\tsend\t%identity\te-value\tbit_score",
                "Asa7G00205\tAT3G05510.2\t29.07\t258\t141\t6\t1\t232\t115\t356\t2e-21\t99.4",
                "Asa2G00002\tAT2G00020.1\t50.0\t120\t40\t0\t1\t120\t1\t120\t1e-25\t110.0",
                "Asa2G00002\tAT2G00010.1\t50.0\t120\t40\t0\t1\t120\t1\t120\t1e-25\t110.0",
                "Asa9G00009.1\tAT4G00004.1\t42.0\t100\t30\t0\t1\t100\t1\t100\t1e-30\t125.0",
                "AsaHitNoGo\tAT5G00005.1\t33.0\t90\t20\t0\t1\t90\t1\t90\t1e-10\t65.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    orth_idx = tmp_path / "ortholog_besthit.tsv.gz"
    build_besthit_index(blast_tsv, orth_idx)
    return gff, go_tsv, orth_idx


def test_run_go_populates_ortholog_column_detail_csv_and_stats(tmp_path: Path):
    gff, go_tsv, orth_idx = _write_mini_inputs(tmp_path)
    cand = ["Asa7G00205.1", "Asa2G00002", "Asa9G00009", "AsaNoHit", "AsaHitNoGo"]
    rows = [{"nearest_gene": g, "category": "Genik", "snp": f"S{i}"} for i, g in enumerate(cand)]

    res = run_go_enrichment_analysis(
        mapped_rows=rows,
        candidate_genes_list=cand,
        output_dir=tmp_path / "out",
        gff_path=gff,
        go_mapping_path=go_tsv,
        ortholog_index_path=orth_idx,
    )
    by_gene = {r["gene_id"]: r for r in res["go_table"]}

    # Isoform fallback candidate -> index (Asa7G00205.1 -> Asa7G00205)
    assert by_gene["Asa7G00205.1"]["ortholog"] == "AT3G05510"
    assert by_gene["Asa7G00205.1"]["ortholog_detail"] == {
        "subject_id": "AT3G05510.2",
        "pident": pytest.approx(29.07),
        "evalue": pytest.approx(2e-21),
        "bitscore": pytest.approx(99.4),
        "n_loci_at_best": 1,
    }

    # Exact match with tie (n_loci_at_best=2)
    assert by_gene["Asa2G00002"]["ortholog"] == "AT2G00010"
    assert by_gene["Asa2G00002"]["ortholog_detail"]["n_loci_at_best"] == 2

    # Base candidate -> isoform index query (Asa9G00009 -> Asa9G00009.1)
    assert by_gene["Asa9G00009"]["ortholog"] == "AT4G00004"

    # Candidate without GO terms still receives ortholog column
    assert by_gene["AsaHitNoGo"]["source"] == "unavailable"
    assert by_gene["AsaHitNoGo"]["ortholog"] == "AT5G00005"
    assert by_gene["AsaHitNoGo"]["ortholog_detail"]["subject_id"] == "AT5G00005.1"

    # Unhit candidate
    assert by_gene["AsaNoHit"]["ortholog"] == "-"
    assert by_gene["AsaNoHit"]["ortholog_detail"] is None

    # go_stats.ortholog
    orth_stats = res["go_stats"]["ortholog"]
    assert orth_stats["available"] is True
    assert orth_stats["sha256"]
    assert orth_stats["n_queries"] == 4
    assert orth_stats["n_candidates_with_ortholog"] == 4

    # CSV exports Ortholog column
    with (tmp_path / "out" / "Tabel_GO_Kandidat.csv").open("r", encoding="utf-8", newline="") as fh:
        csv_rows = list(csv.DictReader(fh))
    csv_by_gene = {r["Gene_ID"]: r for r in csv_rows}
    assert csv_by_gene["Asa7G00205.1"]["Ortholog"] == "AT3G05510"
    assert csv_by_gene["AsaNoHit"]["Ortholog"] == "'-"

    # Without ortholog index -> available False, "-" & None
    res_no_orth = run_go_enrichment_analysis(
        mapped_rows=rows,
        candidate_genes_list=cand,
        gff_path=gff,
        go_mapping_path=go_tsv,
    )
    assert res_no_orth["go_stats"]["ortholog"] == {"available": False}
    assert all(r["ortholog"] == "-" and r["ortholog_detail"] is None for r in res_no_orth["go_table"])


def test_csv_ortholog_formula_cell_neutralized(tmp_path: Path):
    import gzip

    gff, go_tsv, _ = _write_mini_inputs(tmp_path)
    idx = tmp_path / "custom_orth.tsv.gz"
    with gzip.open(idx, "wt", encoding="utf-8") as fh:
        fh.write("query_id\tsubject_locus\tsubject_id\tpident\tevalue\tbitscore\tn_hits\tn_loci_at_best\n")
        fh.write("Asa7G00205\t=CMD()\t=CMD().1\t50.0\t1e-10\t80.0\t1\t1\n")

    rows = [{"nearest_gene": "Asa7G00205", "category": "Genik", "snp": "S1"}]
    run_go_enrichment_analysis(
        mapped_rows=rows,
        candidate_genes_list=["Asa7G00205"],
        output_dir=tmp_path / "out_csv",
        gff_path=gff,
        go_mapping_path=go_tsv,
        ortholog_index_path=idx,
    )
    with (tmp_path / "out_csv" / "Tabel_GO_Kandidat.csv").open("r", encoding="utf-8", newline="") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert csv_rows[0]["Ortholog"] == "'=CMD()"


def test_refresh_go_if_stale_tracks_ortholog_add_replace_and_delete(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    gff, go_tsv, orth_idx = _write_mini_inputs(tmp_path)

    ds_id = uuid.uuid4()
    ds = Dataset(
        id=ds_id,
        owner_id=uuid.uuid4(),
        original_filename="c.vcf",
        storage_path=str(tmp_path / "c.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
        annotation_file_path=str(gff),
        extra_metadata={"go_annotation": {"path": str(go_tsv)}},
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds_id,
        triggered_by=ds.owner_id,
        overall_status=JobStatus.COMPLETED,
        parameters={"annotation": {"feature_type": "gene"}, "contig_filter": {"pattern": ""}},
    )

    class _DB:
        def query(self, *args):
            class _Q:
                def filter(self, *a):
                    return self

                def first(self):
                    return ds

            return _Q()

    db = _DB()
    anno_dir = tmp_path / "jobs" / str(job.id) / "annotation"
    anno_dir.mkdir(parents=True, exist_ok=True)
    cand_payload = {
        "rows": [{"nearest_gene": "Asa7G00205", "category": "Genik", "snp": "S1"}],
        "candidate_genes_list": ["Asa7G00205"],
    }

    # Awal tanpa ortolog (cocokkan pola kontig job)
    initial = run_go_enrichment_analysis(
        mapped_rows=cand_payload["rows"],
        candidate_genes_list=cand_payload["candidate_genes_list"],
        output_dir=anno_dir,
        gff_path=gff,
        go_mapping_path=go_tsv,
        contig_filter_pattern="",
        contig_pattern_source="job",
    )
    (anno_dir / "go_enrichment.json").write_text(json.dumps(initial), encoding="utf-8")

    # Tidak berubah -> tidak refresh
    res0, refreshed0 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    assert refreshed0 is False
    assert res0["go_table"][0]["ortholog"] == "-"

    # Tambah indeks ortolog -> refresh
    ds.extra_metadata["ortholog"] = {"path": str(orth_idx)}
    res1, refreshed1 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    assert refreshed1 is True
    assert res1["go_table"][0]["ortholog"] == "AT3G05510"
    assert (anno_dir / "go_enrichment.previous.json").is_file()

    # Ganti isi indeks ortolog (SHA baru) -> refresh lagi
    alt_blast = tmp_path / "alt.blast.tsv"
    alt_blast.write_text(
        "Asa7G00205\tAT1G99999.1\t60.0\t200\t10\t0\t1\t200\t1\t200\t1e-40\t180.0\n",
        encoding="utf-8",
    )
    build_besthit_index(alt_blast, orth_idx)
    res2, refreshed2 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    assert refreshed2 is True
    assert res2["go_table"][0]["ortholog"] == "AT1G99999"

    # Hapus indeks ortolog -> refresh kembali ke '-'
    orth_idx.unlink()
    ds.extra_metadata.pop("ortholog", None)
    res3, refreshed3 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    assert refreshed3 is True
    assert res3["go_table"][0]["ortholog"] == "-"
    assert res3["go_table"][0]["ortholog_detail"] is None


def test_corrupt_ortholog_index_does_not_recompute_on_every_get(tmp_path: Path, monkeypatch):
    from app.services import go_enrichment_service as go_svc

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    gff, go_tsv, _orth_idx = _write_mini_inputs(tmp_path)

    corrupt_idx = tmp_path / "corrupt_ortholog.tsv.gz"
    corrupt_idx.write_bytes(b"\x1f\x8b\x08\x00truncated_gzip")

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="c.vcf",
        storage_path=str(tmp_path / "c.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
        annotation_file_path=str(gff),
        extra_metadata={
            "go_annotation": {"path": str(go_tsv)},
            "ortholog": {"path": str(corrupt_idx)},
        },
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=ds.owner_id,
        overall_status=JobStatus.COMPLETED,
        parameters={"annotation": {"feature_type": "gene"}, "contig_filter": {"pattern": ""}},
    )

    class _DB:
        def query(self, *args):
            class _Q:
                def filter(self, *a):
                    return self

                def first(self):
                    return ds

            return _Q()

    db = _DB()
    anno_dir = tmp_path / "jobs" / str(job.id) / "annotation"
    anno_dir.mkdir(parents=True, exist_ok=True)
    cand_payload = {
        "rows": [{"nearest_gene": "Asa7G00205", "category": "Genik", "snp": "S1"}],
        "candidate_genes_list": ["Asa7G00205"],
    }

    calls = 0
    orig_run = go_svc.run_go_enrichment_analysis

    def _spy(**kwargs):
        nonlocal calls
        calls += 1
        return orig_run(**kwargs)

    monkeypatch.setattr(go_svc, "run_go_enrichment_analysis", _spy)

    first, r1 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    second, r2 = results_routes.refresh_go_if_stale(db, job, cand_payload, anno_dir)
    assert calls == 1
    assert r1 is True
    assert r2 is False
    assert first["go_stats"]["ortholog"] == {"available": False}
    assert second["go_stats"]["ortholog"] == {"available": False}
