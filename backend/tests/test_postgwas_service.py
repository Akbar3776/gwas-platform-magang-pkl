import numpy as np
import pytest

from app.services.postgwas_service import (
    calculate_unclamped_lambda_gc,
    determine_thresholds,
    run_greedy_clumping,
)


def test_calculate_unclamped_lambda_gc():
    # 1. Null distribution (p-values uniformly distributed U(0,1))
    np.random.seed(42)
    uniform_p = np.random.uniform(0.001, 1.0, size=1000)
    lambda_null = calculate_unclamped_lambda_gc(uniform_p)
    assert 0.85 <= lambda_null <= 1.15

    # 2. Deflated / non-clamped test (e.g. median chi2 is low)
    # Ensure it doesn't clamp to 1.0 if lambda is < 1.0
    high_p = np.random.uniform(0.4, 0.99, size=500)
    lambda_low = calculate_unclamped_lambda_gc(high_p)
    assert lambda_low < 1.0


def test_determine_thresholds():
    p_vals = [1e-8, 2e-7, 1e-5, 0.01, 0.05, 0.1, 0.5]
    n_tests = 1000

    # 1. Bonferroni
    thresh_bonf = determine_thresholds(p_vals, n_tests, method="bonferroni", alpha=0.05)
    assert thresh_bonf["bonferroni"] == pytest.approx(0.05 / 1000)
    assert thresh_bonf["selected_method"] == "bonferroni"
    assert thresh_bonf["selected_p"] == pytest.approx(0.05 / 1000)

    # 2. LOD score
    thresh_lod = determine_thresholds(p_vals, n_tests, method="lod", lod_score=6.0)
    assert thresh_lod["lod_p"] == pytest.approx(1e-6)
    assert thresh_lod["selected_p"] == pytest.approx(1e-6)

    # 3. FDR with significant values
    thresh_fdr = determine_thresholds(p_vals, n_tests, method="fdr", alpha=0.05)
    assert thresh_fdr["fdr"] is not None
    assert thresh_fdr["selected_p"] == thresh_fdr["fdr"]

    # 4. FDR when no values pass (e.g. all p = 0.9)
    thresh_fdr_none = determine_thresholds([0.9, 0.95], 100, method="fdr", alpha=0.05)
    assert thresh_fdr_none["fdr"] is None
    assert thresh_fdr_none["selected_method"] == "bonferroni"
    assert thresh_fdr_none["warning"] is not None


def test_run_greedy_clumping(tmp_path):
    assoc_file = tmp_path / "gwas_results.normalized.tsv"
    # Create test association data on chr 1 with two clusters:
    # Cluster A: pos 1000 (p=1e-8), pos 1050 (p=1e-4), pos 1100 (p=1e-5)
    # Cluster B: pos 500000 (p=5e-9), pos 500200 (p=2e-5)
    # Non-significant: pos 900000 (p=0.5)
    rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "1\t1000\trs_lead1\tA\tG\t0.3\t0.5\t0.08\t1e-8\t100",
        "1\t1050\trs_near1\tA\tG\t0.3\t0.4\t0.08\t1e-4\t100",
        "1\t1100\trs_near2\tT\tC\t0.2\t0.3\t0.08\t1e-5\t100",
        "1\t500000\trs_lead2\tC\tG\t0.4\t-0.6\t0.09\t5e-9\t100",
        "1\t500200\trs_near3\tA\tT\t0.4\t-0.4\t0.09\t2e-5\t100",
        "1\t900000\trs_nonsig\tA\tT\t0.5\t0.01\t0.05\t0.5\t100",
    ]
    assoc_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    lead_snps = run_greedy_clumping(
        assoc_file=assoc_file,
        selected_p=1e-6,
        clump_p2=1e-3,
        clump_kb=250,
    )

    # There should be 2 lead SNPs: rs_lead2 (lowest p 5e-9) and rs_lead1 (1e-8)
    assert len(lead_snps) == 2
    assert lead_snps[0]["snp"] == "rs_lead2"
    assert lead_snps[0]["n_clumped"] == 1
    assert "rs_near3" in lead_snps[0]["clumped_snps"]

    assert lead_snps[1]["snp"] == "rs_lead1"
    assert lead_snps[1]["n_clumped"] == 2


def test_run_greedy_clumping_candidate_peaks(tmp_path):
    from app.services.postgwas_service import extract_suggestive_peaks

    assoc_file = tmp_path / "gwas_results.normalized.tsv"
    # Q3/A45: when no variants meet selected_p (5e-8), run_greedy_clumping returns []
    # and sub-threshold peaks are separated into suggestive peaks.
    rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "1\t1000\trs_cand1\tA\tG\t0.3\t0.5\t0.08\t0.00015\t100",
        "1\t1050\trs_cand1_near\tA\tG\t0.3\t0.4\t0.08\t0.0003\t100",
        "2\t500000\trs_cand2\tC\tG\t0.4\t-0.6\t0.09\t0.00045\t100",
        "3\t900000\trs_cand3\tA\tT\t0.5\t0.01\t0.05\t0.0012\t100",
    ]
    assoc_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    lead_snps = run_greedy_clumping(
        assoc_file=assoc_file,
        selected_p=5e-8,
        clump_p2=1e-3,
        clump_kb=100,
    )
    assert lead_snps == []

    suggestive = extract_suggestive_peaks(
        assoc_file=assoc_file,
        selected_p=5e-8,
        suggestive_p=1e-5,
        clump_p2=1e-3,
        clump_kb=100,
    )
    assert len(suggestive) == 3
    assert suggestive[0]["snp"] == "rs_cand1"
    assert suggestive[0]["is_candidate"] is True
    assert suggestive[0]["is_suggestive"] is True
    assert suggestive[0]["n_clumped"] == 1
    assert suggestive[1]["snp"] == "rs_cand2"
    assert suggestive[2]["snp"] == "rs_cand3"


def test_lead_snps_http_endpoint_wraps_json_array(tmp_path, monkeypatch):
    import json
    import uuid
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.core.security import require_job_owner
    from app.db.engine.database import get_db
    from app.main import app

    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class _EmptyDB:
        def query(self, *args, **kwargs):
            return _EmptyQuery()

    job_id = uuid.uuid4()
    postgwas_dir = tmp_path / "jobs" / str(job_id) / "postgwas"
    postgwas_dir.mkdir(parents=True, exist_ok=True)
    sample_array = [
        {
            "snp": "6:896078569",
            "chr": "6",
            "pos": 896078569,
            "p": 1.65e-6,
            "beta": 4.28,
            "se": 0.89,
            "n_clumped": 2,
            "clumped_snps": ["."],
            "is_candidate": True,
            "a1": "G",
            "a0": "A",
        }
    ]
    (postgwas_dir / "lead_snps.json").write_text(json.dumps(sample_array), encoding="utf-8")
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))

    app.dependency_overrides[require_job_owner] = lambda: None
    app.dependency_overrides[get_db] = lambda: _EmptyDB()
    try:
        client = TestClient(app)
        resp = client.get(f"/api/v1/results/{job_id}/lead-snps")
    finally:
        app.dependency_overrides.pop(require_job_owner, None)
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert body["count"] == 1
    assert len(body["snps"]) == 1
    assert body["snps"][0]["snp"] == "6:896078569"


def test_run_greedy_clumping_preserves_ca_and_contig_chr_labels(tmp_path):
    assoc_file = tmp_path / "gwas_results.normalized.tsv"
    rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "Ca1\t1000\trs_ca\tA\tG\t0.3\t0.5\t0.08\t1e-8\t100",
        "chrC\t2000\trs_c\tC\tT\t0.2\t0.4\t0.09\t2e-8\t100",
        "contig_12\t3000\trs_ctg\tG\tA\t0.25\t0.3\t0.07\t3e-8\t100",
    ]
    assoc_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    lead_snps = run_greedy_clumping(
        assoc_file=assoc_file,
        selected_p=1e-6,
        clump_p2=1e-3,
        clump_kb=250,
    )
    by_snp = {s["snp"]: s["chr"] for s in lead_snps}
    assert by_snp == {
        "rs_ca": "Ca1",
        "rs_c": "C",
        "rs_ctg": "contig_12",
    }

    from app.tasks._common import chr_label

    assert chr_label("Ca1") == "Ca1"
    assert chr_label("contig_12") == "contig_12"
    assert chr_label("chrC") == "C"
    assert chr_label("Chr01") == "01"
    assert chr_label("chrchr1") == "chr1"


def test_multilocus_leads_peak_comparison_and_primers_preserve_ca_contig(tmp_path):
    from app.services.primer_service import generate_flanking_and_primers
    from app.tasks.multilocus_tasks import (
        extract_multilocus_leads_from_tsv,
        generate_peak_comparison,
    )

    ml_tsv = tmp_path / "farmcpu_results.tsv"
    ml_tsv.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "Ca1\t1000\trs_ca\tA\tG\t0.25\t0.5\t0.1\t1e-8\t100\n"
        "contig_12\t2000\trs_ctg\tC\tT\t0.30\t0.4\t0.1\t2e-7\t100\n",
        encoding="utf-8",
    )
    ml_leads = extract_multilocus_leads_from_tsv(ml_tsv, method_name="FarmCPU", trait_name="trait")
    assert [m["chr"] for m in ml_leads] == ["Ca1", "contig_12"]

    single_leads = [
        {"snp": "rs_ca", "chr": "Ca1", "pos": 1000, "p": 1e-8, "beta": 0.5, "a1": "A"},
        {"snp": "rs_ctg", "chr": "contig_12", "pos": 2000, "p": 2e-7, "beta": 0.4, "a1": "C"},
    ]
    peak = generate_peak_comparison(
        single_lead_snps=single_leads,
        multi_lead_snps=ml_leads,
        output_csv=tmp_path / "peak.csv",
        output_json=tmp_path / "peak.json",
        window_kb=50,
    )
    assert len(peak["consistent"]) == 2
    assert [r["chr"] for r in peak["consistent"]] == ["Ca1", "contig_12"]

    primer_payload = generate_flanking_and_primers(
        lead_snps=single_leads,
        trait_name="trait",
        output_dir=None,
    )
    assert [m["chr"] for m in primer_payload["markers"]] == ["Ca1", "contig_12"]
    assert primer_payload["markers"][0]["marker_id"] == "trait_Ca1_1000"


def test_get_lead_snps_is_strictly_read_only_on_disk(tmp_path, monkeypatch):
    import json
    import uuid
    from app.api.routes.results import get_lead_snps_data
    from app.core.config import settings

    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class _EmptyDB:
        def query(self, *args, **kwargs):
            return _EmptyQuery()

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))

    # Case 1: legacy JSON list on disk must remain untouched (no disk rewrite)
    job_a = uuid.uuid4()
    pg_a = tmp_path / "jobs" / str(job_a) / "postgwas"
    pg_a.mkdir(parents=True)
    lead_a = pg_a / "lead_snps.json"
    orig_list_raw = json.dumps([{"snp": "rs1", "chr": "1", "pos": 100, "p": 1e-8}])
    lead_a.write_text(orig_list_raw, encoding="utf-8")
    out_a = get_lead_snps_data(job_id=job_a, db=_EmptyDB())
    assert out_a["count"] == 1
    assert lead_a.read_text(encoding="utf-8") == orig_list_raw

    # Case 2: empty lead_snps.json + fallback clumping from normalized TSV must not overwrite lead_snps.json
    job_b = uuid.uuid4()
    job_root_b = tmp_path / "jobs" / str(job_b)
    pg_b = job_root_b / "postgwas"
    gwas_b = job_root_b / "gwas"
    pg_b.mkdir(parents=True)
    gwas_b.mkdir(parents=True)
    orig_empty_raw = json.dumps({"count": 0, "snps": []})
    lead_b = pg_b / "lead_snps.json"
    lead_b.write_text(orig_empty_raw, encoding="utf-8")
    (pg_b / "thresholds.json").write_text(json.dumps({"selected_p": 1e-6}), encoding="utf-8")
    (gwas_b / "gwas_results.normalized.tsv").write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "1\t500\trs_regen\tA\tG\t0.3\t0.5\t0.08\t1e-8\t100\n",
        encoding="utf-8",
    )

    out_b = get_lead_snps_data(job_id=job_b, db=_EmptyDB())
    assert out_b["count"] == 1
    assert out_b["snps"][0]["snp"] == "rs_regen"
    assert lead_b.read_text(encoding="utf-8") == orig_empty_raw


def test_postgwas_task_uses_4e_p1_for_plink_and_splits_suggestive_in_json(tmp_path, monkeypatch):
    import json
    import uuid
    from unittest.mock import MagicMock
    from app.api.routes.results import get_lead_snps_data
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
    from app.tasks import postgwas_tasks

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = str(uuid.uuid4())
    gwas_dir = tmp_path / "jobs" / job_id / "gwas"
    gwas_dir.mkdir(parents=True)
    norm_assoc = gwas_dir / "gwas_results.normalized.tsv"
    norm_assoc.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "1\t1000\trs_sig\tA\tG\t0.3\t0.5\t0.08\t1e-8\t100\n"
        "2\t2000\trs_sug\tC\tT\t0.25\t0.3\t0.09\t5e-5\t100\n",
        encoding="utf-8",
    )
    bfile = gwas_dir / "genotypes"
    bfile.with_suffix(".bed").touch()

    mock_job = AnalysisJob(
        id=uuid.UUID(job_id),
        dataset_id=uuid.uuid4(),
        pipeline_type="full_pipeline",
        current_step=PipelineStep.POSTGWAS,
        overall_status=JobStatus.RUNNING_POSTGWAS,
        parameters={},
    )
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_db.query.return_value.filter.return_value.first.return_value = None

    monkeypatch.setattr(postgwas_tasks, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(postgwas_tasks, "ensure_not_cancelled", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "guard_status_change", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "mark_job", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "upsert_result", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "which_tool", lambda name: "/usr/local/bin/plink2" if name == "plink2" else None)

    captured_cmds = []

    def fake_run_cli(cmd, **kwargs):
        captured_cmds.append(cmd)
        # Simulate plink2 honoring --clump-p1 (writes rs_sig when p1=0.025 and empty if only subthreshold)
        p1_idx = cmd.index("--clump-p1") + 1
        p1_val = float(cmd[p1_idx])
        clump_out = tmp_path / "jobs" / job_id / "postgwas" / "clumped.clumps"
        rows = ["#CHROM\tPOS\tID\tP\tTOTAL\tSP2\n"]
        if 1e-8 <= p1_val:
            rows.append("1\t1000\trs_sig\t1e-8\t0\tNONE\n")
        if 5e-5 <= p1_val:
            rows.append("2\t2000\trs_sug\t5e-5\t0\tNONE\n")
        clump_out.write_text("".join(rows), encoding="utf-8")

    monkeypatch.setattr(postgwas_tasks, "run_cli_command", fake_run_cli)

    res = postgwas_tasks.run_postgwas_analysis(
        {"normalized_assoc_path": str(norm_assoc), "genotype_bfile_prefix": str(bfile)},
        job_id=job_id,
        dataset_id=str(mock_job.dataset_id),
        threshold_params={"method": "bonferroni", "alpha": 0.05, "suggestive_p": 1e-4},
    )
    assert res["status"] == "completed"
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    p1_used = float(cmd[cmd.index("--clump-p1") + 1])
    assert p1_used == pytest.approx(0.025)

    # Re-run with tight bonferroni/lod threshold (lod_score=6 -> selected_p=1e-6 < rs_sug 5e-5)
    captured_cmds.clear()
    postgwas_tasks.run_postgwas_analysis(
        {"normalized_assoc_path": str(norm_assoc), "genotype_bfile_prefix": str(bfile)},
        job_id=job_id,
        dataset_id=str(mock_job.dataset_id),
        threshold_params={"method": "lod", "lod_score": 6.0, "suggestive_p": 1e-4},
    )
    p1_lod = float(captured_cmds[0][captured_cmds[0].index("--clump-p1") + 1])
    assert p1_lod == pytest.approx(1e-6)

    lead_payload = json.loads((tmp_path / "jobs" / job_id / "postgwas" / "lead_snps.json").read_text(encoding="utf-8"))
    assert [s["snp"] for s in lead_payload["snps"]] == ["rs_sig"]
    assert [s["snp"] for s in lead_payload.get("suggestive", [])] == ["rs_sug"]

    api_out = get_lead_snps_data(job_id=uuid.UUID(job_id), db=mock_db)
    assert [s["snp"] for s in api_out["snps"]] == ["rs_sig"]
    assert [s["snp"] for s in api_out.get("suggestive", [])] == ["rs_sug"]


def _seed_postgwas_job(tmp_path, monkeypatch):
    import uuid
    from unittest.mock import MagicMock
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
    from app.tasks import postgwas_tasks

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = str(uuid.uuid4())
    job_root = tmp_path / "jobs" / job_id
    gwas_dir = job_root / "gwas"
    ml_dir = job_root / "multilocus"
    gwas_dir.mkdir(parents=True)
    ml_dir.mkdir(parents=True)
    norm_assoc = gwas_dir / "gwas_results.normalized.tsv"
    norm_assoc.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "1\t1000\trs_ns1\tA\tG\t0.3\t0.05\t0.08\t0.25\t100\n"
        "1\t2000\trs_ns2\tC\tT\t0.25\t0.02\t0.09\t0.65\t100\n",
        encoding="utf-8",
    )
    mock_job = AnalysisJob(
        id=uuid.UUID(job_id),
        dataset_id=uuid.uuid4(),
        pipeline_type="full_pipeline",
        current_step=PipelineStep.POSTGWAS,
        overall_status=JobStatus.RUNNING_POSTGWAS,
        parameters={},
    )
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_db.query.return_value.filter.return_value.first.return_value = None

    monkeypatch.setattr(postgwas_tasks, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(postgwas_tasks, "ensure_not_cancelled", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "guard_status_change", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "mark_job", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "upsert_result", lambda *a, **kw: None)
    monkeypatch.setattr(postgwas_tasks, "which_tool", lambda name: None)
    return job_id, job_root, norm_assoc, mock_job


def test_postgwas_peak_comparison_excludes_nonsignificant_multilocus_tsv(tmp_path, monkeypatch):
    import json
    from app.tasks import postgwas_tasks

    job_id, job_root, norm_assoc, mock_job = _seed_postgwas_job(tmp_path, monkeypatch)
    ml_rows = ["chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn"]
    for i in range(10):
        p_val = 0.02 + i * 0.05
        ml_rows.append(f"1\t{(i + 1) * 1000}\trs_ml_{i}\tA\tG\t0.3\t0.1\t0.05\t{p_val}\t100")
    (job_root / "multilocus" / "farmcpu_results.tsv").write_text("\n".join(ml_rows) + "\n", encoding="utf-8")

    postgwas_tasks.run_postgwas_analysis(
        {"normalized_assoc_path": str(norm_assoc)},
        job_id=job_id,
        dataset_id=str(mock_job.dataset_id),
    )

    peak = json.loads((job_root / "postgwas" / "peak_comparison.json").read_text(encoding="utf-8"))
    assert peak["multi_only"] == []
    assert peak["summary"]["n_multi_only"] == 0
    assert peak["summary"]["reason"] == "Tidak ada SNP multi-locus yang lolos Bonferroni"


def test_postgwas_peak_comparison_includes_only_bonferroni_significant_multilocus_snp(tmp_path, monkeypatch):
    import json
    from app.tasks import postgwas_tasks

    job_id, job_root, norm_assoc, mock_job = _seed_postgwas_job(tmp_path, monkeypatch)
    ml_rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "2\t50000\trs_sig_ml\tA\tG\t0.3\t0.8\t0.1\t1e-4\t100",
    ]
    for i in range(9):
        ml_rows.append(f"2\t{(i + 1) * 1000}\trs_bg_{i}\tC\tT\t0.25\t0.05\t0.1\t{0.1 + i * 0.05}\t100")
    (job_root / "multilocus" / "blink_results.tsv").write_text("\n".join(ml_rows) + "\n", encoding="utf-8")

    postgwas_tasks.run_postgwas_analysis(
        {"normalized_assoc_path": str(norm_assoc)},
        job_id=job_id,
        dataset_id=str(mock_job.dataset_id),
    )

    peak = json.loads((job_root / "postgwas" / "peak_comparison.json").read_text(encoding="utf-8"))
    assert len(peak["multi_only"]) == 1
    assert peak["multi_only"][0]["snp"] == "rs_sig_ml"
    assert peak["multi_only"][0]["status"] == "Minor-Effect (Hanya Multi-Locus)"


def test_postgwas_restores_orig_coords_and_preserves_pseudo_matching(tmp_path, monkeypatch):
    import csv
    import json
    from app.services.chromosome_service import save_split_map
    from app.tasks import postgwas_tasks

    job_id, job_root, norm_assoc, mock_job = _seed_postgwas_job(tmp_path, monkeypatch)
    split_map = {
        "threshold_bp": 5_000,
        "unit_bp": 2_000,
        "split_chroms": {"chr7": {"max_orig_pos": 6500, "num_parts": 4, "parts": ["chr7_p0", "chr7_p1", "chr7_p2", "chr7_p3"]}},
        "parts": {
            "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4_000},
            "chr7_p3": {"orig_chr": "chr7", "part": 3, "offset_bp": 6_000},
        },
    }
    save_split_map(job_id, split_map)

    norm_assoc.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "chr7_p2\t1500\tchr7:5500\tA\tG\t0.3\t0.9\t0.1\t1e-10\t100\n"
        "chr7_p3\t500\tchr7:6500\tC\tT\t0.25\t0.6\t0.1\t1e-9\t100\n"
        "1\t1000\trs_bg\tA\tG\t0.3\t0.02\t0.08\t0.5\t100\n",
        encoding="utf-8",
    )
    ml_rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "7_p2\t1520\tchr7:5520\tA\tG\t0.3\t0.85\t0.1\t1e-6\t100",
    ]
    for i in range(9):
        ml_rows.append(f"1\t{(i + 1) * 1000}\trs_ml_bg_{i}\tC\tT\t0.25\t0.05\t0.1\t{0.2 + i * 0.05}\t100")
    (job_root / "multilocus" / "farmcpu_results.tsv").write_text("\n".join(ml_rows) + "\n", encoding="utf-8")

    postgwas_tasks.run_postgwas_analysis(
        {"normalized_assoc_path": str(norm_assoc)},
        job_id=job_id,
        dataset_id=str(mock_job.dataset_id),
    )

    lead_payload = json.loads((job_root / "postgwas" / "lead_snps.json").read_text(encoding="utf-8"))
    by_snp = {s["snp"]: s for s in lead_payload["snps"]}
    assert by_snp["chr7:5500"]["chr"] == "7_p2"
    assert by_snp["chr7:5500"]["pos"] == 1500
    assert by_snp["chr7:5500"]["orig_chr"] == "chr7"
    assert by_snp["chr7:5500"]["orig_pos"] == 5500
    assert by_snp["chr7:6500"]["orig_chr"] == "chr7"
    assert by_snp["chr7:6500"]["orig_pos"] == 6500

    with (job_root / "postgwas" / "lead_snps.tsv").open("r", encoding="utf-8") as lf:
        tsv_rows = list(csv.DictReader(lf, delimiter="\t"))
    assert list(tsv_rows[0].keys())[-2:] == ["orig_chr", "orig_pos"]
    tsv_by_snp = {r["snp"]: r for r in tsv_rows}
    assert tsv_by_snp["chr7:5500"]["orig_chr"] == "chr7"
    assert tsv_by_snp["chr7:5500"]["orig_pos"] == "5500"

    peak = json.loads((job_root / "postgwas" / "peak_comparison.json").read_text(encoding="utf-8"))
    assert len(peak["consistent"]) == 1
    assert peak["consistent"][0]["chr"] == "7_p2"
    assert peak["consistent"][0]["orig_chr"] == "chr7"
    assert peak["consistent"][0]["orig_pos"] == 5500
    assert len(peak["single_only"]) == 1
    assert peak["single_only"][0]["orig_chr"] == "chr7"
    assert peak["single_only"][0]["orig_pos"] == 6500

    with (job_root / "postgwas" / "Ringkasan_Puncak_GWAS.csv").open("r", encoding="utf-8") as cf:
        csv_rows = list(csv.DictReader(cf))
    assert list(csv_rows[0].keys())[-2:] == ["Orig_Chr", "Orig_Pos"]
    csv_by_snp = {r["SNP"]: r for r in csv_rows}
    assert csv_by_snp["chr7:5500"]["Orig_Chr"] == "chr7"
    assert csv_by_snp["chr7:5500"]["Orig_Pos"] == "5500"
