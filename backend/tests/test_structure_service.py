from pathlib import Path

import numpy as np
import pytest

from app.schemas.pipeline import QCPipelineConfig, StructureConfig
from tests.fixtures.plink_fixture import run_python_fallback_qc
from app.services.structure_service import (
    compute_centered_kinship,
    compute_king_robust_table,
    compute_ld_decay_curve,
    determine_model_recommendation,
    read_plink_binary,
    run_pca_decomposition,
)
from app.tasks._common import which_tool
from tests.fixtures.make_fixtures import ensure_fixtures


@pytest.fixture
def toy_bfile(tmp_path):
    fixture_vcf = Path(__file__).parent / "fixtures" / "data" / "toy_gwas.vcf.gz"
    ensure_fixtures()

    out_prefix = tmp_path / "plink_qc"
    cfg = QCPipelineConfig(maf=0.01, geno=0.2, mind=0.2)
    run_python_fallback_qc(input_vcf=str(fixture_vcf), output_prefix=out_prefix, cfg=cfg)
    return out_prefix


def test_read_plink_binary_and_pca(toy_bfile):
    samples, variants, G = read_plink_binary(toy_bfile)
    assert len(samples) > 0
    assert len(variants) > 0
    assert G.shape == (len(samples), len(variants))
    assert set(np.unique(G)).issubset({-1, 0, 1, 2})

    pca_data = run_pca_decomposition(G, samples, n_pcs=5)
    assert "samples" in pca_data
    assert "eigenvalues" in pca_data
    assert "pve" in pca_data
    assert "cumulative_pve" in pca_data
    assert len(pca_data["samples"]) == len(samples)
    assert len(pca_data["samples"][0]["pcs"]) == 5

    # Check PVE is non-negative and cumulative is monotonic
    for p in pca_data["pve"]:
        assert p >= 0.0
    cum = pca_data["cumulative_pve"]
    for i in range(len(cum) - 1):
        assert cum[i] <= cum[i + 1] + 1e-6


def test_centered_kinship(toy_bfile):
    samples, _, G = read_plink_binary(toy_bfile)
    K = compute_centered_kinship(G)
    assert K.shape == (len(samples), len(samples))

    # K must be symmetric
    assert np.allclose(K, K.T, atol=1e-5)

    # Diagonal elements should be close to 1 on average
    assert abs(np.mean(np.diag(K)) - 1.0) < 0.1


def test_king_robust_table(toy_bfile):
    samples, _, G = read_plink_binary(toy_bfile)
    king = compute_king_robust_table(G, samples)
    assert king["estimator"] == "KING-robust"
    assert "pairs_summary" in king
    assert "duplicate" in king["pairs_summary"]
    assert "first_degree" in king["pairs_summary"]


def test_ld_decay_curve(toy_bfile):
    _, variants, G = read_plink_binary(toy_bfile)
    ld = compute_ld_decay_curve(variants, G, window_kb=500, bin_kb=20, cutoff_r2=0.2)
    assert "bins" in ld
    assert "chosen_kb" in ld
    assert "reason" in ld
    assert len(ld["bins"]) > 0
    for b in ld["bins"]:
        assert 0.0 <= b["mean_r2"] <= 1.0


def test_determine_model_recommendation():
    # 1. Structure + Kinship -> LMM
    rec1 = determine_model_recommendation(
        pca_data={"pve": [0.15, 0.08], "k_clusters": 2},
        king_data={"pairs_summary": {"duplicate": 1, "first_degree": 2, "unrelated": 10}},
        population_type="outcross",
    )
    assert rec1["recommended_model"] == "lmm"
    assert rec1["structure_detected"] is True
    assert rec1["kinship_detected"] is True

    # 2. Structure only -> GLM
    rec2 = determine_model_recommendation(
        pca_data={"pve": [0.12, 0.05], "k_clusters": 2},
        king_data={"pairs_summary": {"duplicate": 0, "first_degree": 0, "unrelated": 100}},
        population_type="outcross",
    )
    assert rec2["recommended_model"] == "glm"
    assert rec2["structure_detected"] is True
    assert rec2["kinship_detected"] is False

    # 3. Kinship only -> LMM
    rec3 = determine_model_recommendation(
        pca_data={"pve": [0.03, 0.02], "k_clusters": 1},
        king_data={"pairs_summary": {"duplicate": 2, "first_degree": 5, "unrelated": 20}},
        population_type="outcross",
    )
    assert rec3["recommended_model"] == "lmm"
    assert rec3["structure_detected"] is False
    assert rec3["kinship_detected"] is True

    # 4. Neither + inbred -> LMM
    rec4 = determine_model_recommendation(
        pca_data={"pve": [0.03, 0.02], "k_clusters": 1},
        king_data={"pairs_summary": {"duplicate": 0, "first_degree": 0, "unrelated": 50}},
        population_type="inbred",
    )
    assert rec4["recommended_model"] == "lmm"


def _seed_structure_job(tmp_path, monkeypatch, qc_bfile_prefix: Path):
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings
    from app.db.base import Base
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
    from app.tasks import _common as task_common, structure_tasks

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    engine = create_engine(f"sqlite:///{tmp_path / 'structure_test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(structure_tasks, "SessionLocal", Session)
    monkeypatch.setattr(task_common, "SessionLocal", Session)

    db = Session()
    try:
        user = User(
            id=uuid.uuid4(),
            email="struct@example.com",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        ds = Dataset(
            id=uuid.uuid4(),
            owner_id=user.id,
            original_filename="cohort.vcf.gz",
            storage_path=str(tmp_path / "cohort.vcf.gz"),
            upload_status=UploadStatus.UPLOADED,
        )
        job = AnalysisJob(
            id=uuid.uuid4(),
            dataset_id=ds.id,
            triggered_by=user.id,
            pipeline_type="full_pipeline",
            current_step=PipelineStep.STRUCTURE,
            overall_status=JobStatus.RUNNING_STRUCTURE,
            parameters={"qc": {"population_type": "inbred"}, "gwas": {"model": "auto"}},
        )
        db.add_all([user, ds, job])
        db.commit()
        return Session, str(job.id), str(ds.id), storage_root
    finally:
        db.close()


def test_run_structure_analysis_runs_ld_pruning_and_reads_pruned_bfile(tmp_path, monkeypatch, toy_bfile):
    from app.tasks import structure_tasks
    from app.utils.process import CLIExecutionResult

    Session, job_id, ds_id, storage_root = _seed_structure_job(tmp_path, monkeypatch, toy_bfile)

    captured_cmds = []
    read_prefixes = []

    def fake_run_cli(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        out_idx = cmd.index("--out") + 1
        out_prefix = Path(cmd[out_idx])
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        if "--indep-pairwise" in cmd:
            Path(f"{out_prefix}.prune.in").write_text("snp1\nsnp2\n", encoding="utf-8")
        elif "--make-bed" in cmd:
            for ext in (".bed", ".bim", ".fam"):
                Path(f"{out_prefix}{ext}").write_bytes(b"stub")
        return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=Path(kwargs["log_file"]))

    def fake_read_plink(prefix):
        read_prefixes.append(str(prefix))
        samples = [f"S{i}" for i in range(6)]
        variants = [
            {"chrom": "1", "rsid": "snp1", "pos": 100, "a1": "A", "a2": "G"},
            {"chrom": "1", "rsid": "snp2", "pos": 500, "a1": "C", "a2": "T"},
            {"chrom": "1", "rsid": "snp3", "pos": 900, "a1": "G", "a2": "A"},
        ]
        G = np.array(
            [
                [0, 1, 2],
                [1, 0, 1],
                [2, 2, 0],
                [0, 1, 1],
                [1, 2, 0],
                [2, 0, 2],
            ],
            dtype=np.int8,
        )
        return samples, variants, G

    monkeypatch.setattr(structure_tasks, "which_tool", lambda name: "/usr/local/bin/plink", raising=False)
    monkeypatch.setattr(structure_tasks, "run_cli_command", fake_run_cli, raising=False)
    monkeypatch.setattr(structure_tasks, "read_plink_binary", fake_read_plink)

    structure_tasks.run_structure_analysis(
        {"genotype_bfile_prefix": str(toy_bfile)},
        job_id=job_id,
        dataset_id=ds_id,
    )

    assert any(
        "--indep-pairwise" in cmd
        and "50" in cmd
        and "5" in cmd
        and "0.2" in cmd
        for cmd in captured_cmds
    )
    indep_cmd = next(cmd for cmd in captured_cmds if "--indep-pairwise" in cmd)
    idx = indep_cmd.index("--indep-pairwise")
    assert indep_cmd[idx:idx + 4] == ["--indep-pairwise", "50", "5", "0.2"]

    expected_pruned = storage_root / "jobs" / job_id / "structure" / "pruned"
    assert any("--extract" in cmd and "--make-bed" in cmd and str(expected_pruned) in cmd for cmd in captured_cmds)
    assert read_prefixes == [str(expected_pruned), str(toy_bfile)]

    import json
    ld_json = json.loads((storage_root / "jobs" / job_id / "structure" / "ld_decay.json").read_text(encoding="utf-8"))
    assert ld_json["method"] == "unphased r^2 (setara plink2 --r2-unphased)"
    assert ld_json["source_bfile"] == str(toy_bfile)
    assert ld_json["n_variants_used"] == 3

    log_text = (storage_root / "jobs" / job_id / "structure" / "structure.log").read_text(encoding="utf-8")
    assert "--indep-pairwise" in log_text


def test_run_structure_analysis_fails_clearly_when_plink_missing(tmp_path, monkeypatch, toy_bfile):
    import uuid
    from app.db.engine.models import AnalysisJob, JobStatus
    from app.tasks import structure_tasks

    Session, job_id, ds_id, _ = _seed_structure_job(tmp_path, monkeypatch, toy_bfile)
    monkeypatch.setattr(structure_tasks, "which_tool", lambda name: None, raising=False)

    with pytest.raises(Exception) as exc_info:
        structure_tasks.run_structure_analysis(
            {"genotype_bfile_prefix": str(toy_bfile)},
            job_id=job_id,
            dataset_id=ds_id,
        )
    assert "PLINK" in str(exc_info.value)

    db = Session()
    try:
        job = db.get(AnalysisJob, uuid.UUID(job_id))
        assert job.overall_status == JobStatus.FAILED
    finally:
        db.close()


def test_structure_matrices_use_float32(monkeypatch, toy_bfile):
    import scipy.linalg

    samples, _, G = read_plink_binary(toy_bfile)
    K = compute_centered_kinship(G)
    assert K.dtype == np.float32

    seen_dtypes = []
    real_svd = scipy.linalg.svd

    def spying_svd(mat, *args, **kwargs):
        seen_dtypes.append(mat.dtype)
        return real_svd(mat, *args, **kwargs)

    monkeypatch.setattr(scipy.linalg, "svd", spying_svd)
    run_pca_decomposition(G, samples, n_pcs=3)
    assert seen_dtypes and all(dt == np.float32 for dt in seen_dtypes)


def test_run_structure_analysis_respects_user_n_pc_covariates(tmp_path, monkeypatch, toy_bfile):
    import uuid
    from app.db.engine.models import AnalysisJob
    from app.tasks import structure_tasks
    from app.utils.process import CLIExecutionResult

    Session, job_id, ds_id, storage_root = _seed_structure_job(tmp_path, monkeypatch, toy_bfile)

    db = Session()
    try:
        job = db.get(AnalysisJob, uuid.UUID(job_id))
        job.parameters = {
            "qc": {"population_type": "inbred"},
            "gwas": {"model": "lmm", "n_pc_covariates": 3},
            "structure": {"n_pcs": 10},
        }
        db.commit()
    finally:
        db.close()

    def fake_run_cli(cmd, **kwargs):
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        if "--indep-pairwise" in cmd:
            Path(f"{out_prefix}.prune.in").write_text("snp1\nsnp2\n", encoding="utf-8")
        elif "--make-bed" in cmd:
            for ext in (".bed", ".bim", ".fam"):
                Path(f"{out_prefix}{ext}").write_bytes(b"stub")
        return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=Path(kwargs["log_file"]))

    def fake_read_plink(prefix):
        rng = np.random.default_rng(42)
        samples = [f"S{i}" for i in range(10)]
        variants = [
            {"chrom": "1", "rsid": f"snp{i}", "pos": 100 * (i + 1), "a1": "A", "a2": "G"}
            for i in range(12)
        ]
        G = rng.integers(0, 3, size=(10, 12), dtype=np.int8)
        return samples, variants, G

    monkeypatch.setattr(structure_tasks, "which_tool", lambda name: "/usr/local/bin/plink", raising=False)
    monkeypatch.setattr(structure_tasks, "run_cli_command", fake_run_cli, raising=False)
    monkeypatch.setattr(structure_tasks, "read_plink_binary", fake_read_plink)
    monkeypatch.setattr(
        structure_tasks,
        "determine_model_recommendation",
        lambda **kwargs: {
            "recommended_model": "lmm",
            "model_used": "lmm",
            "recommended_pc_covariates": 1,
            "structure_detected": False,
            "kinship_detected": True,
            "pca_finding": "ok",
            "kinship_finding": "ok",
            "rationale": [],
        },
    )

    out = structure_tasks.run_structure_analysis(
        {"genotype_bfile_prefix": str(toy_bfile)},
        job_id=job_id,
        dataset_id=ds_id,
    )

    gemma_cov_lines = Path(out["gemma_covariates_path"]).read_text(encoding="utf-8").splitlines()
    assert len(gemma_cov_lines) == 10
    # 1 intercept + 3 PC columns = 4 columns
    assert all(len(line.split("\t")) == 4 for line in gemma_cov_lines)
    assert all(line.startswith("1\t") for line in gemma_cov_lines)

    plink_cov_lines = Path(out["plink_covariates_path"]).read_text(encoding="utf-8").splitlines()
    assert plink_cov_lines[0].split("\t") == ["#FID", "IID", "PC1", "PC2", "PC3"]
    assert len(plink_cov_lines) == 11
    assert all(len(line.split("\t")) == 5 for line in plink_cov_lines[1:])

    # Explicit 0 overrides non-zero recommendation (e.g. 3 -> 0 PC columns, intercept only)
    db = Session()
    try:
        job = db.get(AnalysisJob, uuid.UUID(job_id))
        job.parameters = {
            "qc": {"population_type": "outcross"},
            "gwas": {"model": "glm", "n_pc_covariates": 0},
            "structure": {"n_pcs": 10},
        }
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        structure_tasks,
        "determine_model_recommendation",
        lambda **kwargs: {
            "recommended_model": "glm",
            "model_used": "glm",
            "recommended_pc_covariates": 3,
            "structure_detected": True,
            "kinship_detected": False,
            "pca_finding": "ok",
            "kinship_finding": "ok",
            "rationale": [],
        },
    )
    out0 = structure_tasks.run_structure_analysis(
        {"genotype_bfile_prefix": str(toy_bfile)},
        job_id=job_id,
        dataset_id=ds_id,
    )
    gemma_cov_zero = Path(out0["gemma_covariates_path"]).read_text(encoding="utf-8").splitlines()
    assert all(line == "1" for line in gemma_cov_zero)
    plink_cov_zero = Path(out0["plink_covariates_path"]).read_text(encoding="utf-8").splitlines()
    assert plink_cov_zero[0].split("\t") == ["#FID", "IID"]


def test_king_robust_formula_duplicate_sibling_and_unrelated():
    rng = np.random.default_rng(12345)
    n_snps = 4000
    maf = 0.45

    def _draw_ind():
        return rng.binomial(1, maf, size=n_snps) + rng.binomial(1, maf, size=n_snps)

    # Parents of full siblings
    mom = np.vstack([rng.binomial(1, maf, size=n_snps), rng.binomial(1, maf, size=n_snps)])
    dad = np.vstack([rng.binomial(1, maf, size=n_snps), rng.binomial(1, maf, size=n_snps)])

    def _meiosis():
        m_pick = rng.integers(0, 2, size=n_snps)
        d_pick = rng.integers(0, 2, size=n_snps)
        return mom[m_pick, np.arange(n_snps)] + dad[d_pick, np.arange(n_snps)]

    sib1 = _meiosis()
    sib2 = _meiosis()
    sib1_dup = sib1.copy()
    unrelated = _draw_ind()

    # Check pairwise subsets individually so unrelated != duplicate confusion
    res_dup = compute_king_robust_table(np.vstack([sib1, sib1_dup]), ["A", "A_dup"])
    assert res_dup["pairs_summary"]["duplicate"] == 1
    assert abs(res_dup["top_pairs"][0]["kinship"] - 0.5) < 1e-3

    res_sib = compute_king_robust_table(np.vstack([sib1, sib2]), ["Sib1", "Sib2"])
    assert res_sib["pairs_summary"]["first_degree"] == 1
    assert 0.19 <= res_sib["top_pairs"][0]["kinship"] <= 0.31

    res_unrel = compute_king_robust_table(np.vstack([sib1, unrelated]), ["Sib1", "Unrel"])
    assert res_unrel["pairs_summary"]["unrelated"] == 1
    assert res_unrel["top_pairs"] == []


def test_ld_decay_empty_bins_are_null_and_do_not_trigger_cutoff():
    g_col = np.array([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2], dtype=np.int8)
    G = np.column_stack([g_col, g_col])
    variants = [
        {"chrom": "1", "rsid": "s1", "pos": 1000, "a1": "A", "a2": "G"},
        {"chrom": "1", "rsid": "s2", "pos": 6000, "a1": "A", "a2": "G"},
    ]
    ld = compute_ld_decay_curve(
        variants, G, window_kb=100, bin_kb=10, cutoff_r2=0.2, fallback_kb=100
    )
    assert ld["bins"][0]["n_pairs"] == 1
    assert ld["bins"][0]["mean_r2"] >= 0.99
    for b in ld["bins"][1:]:
        assert b["n_pairs"] == 0
        assert b["mean_r2"] is None
    assert ld["cutoff_kb"] is None
    assert ld["chosen_kb"] == 100
    assert "tidak tercapai" in ld["reason"]
    assert "100 kb" in ld["reason"]


def test_ld_decay_observed_cutoff_ignores_empty_bins():
    g0 = np.array([0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2], dtype=np.int8)
    g2 = np.array([0, 1, 2, 1, 0, 1, 2, 1, 0, 1, 2, 1], dtype=np.int8)
    G = np.column_stack([g0, g0, g2])
    variants = [
        {"chrom": "1", "rsid": "s1", "pos": 1000, "a1": "A", "a2": "G"},
        {"chrom": "1", "rsid": "s2", "pos": 6000, "a1": "A", "a2": "G"},
        {"chrom": "1", "rsid": "s3", "pos": 32000, "a1": "C", "a2": "T"},
    ]
    ld = compute_ld_decay_curve(
        variants, G, window_kb=100, bin_kb=10, cutoff_r2=0.2, fallback_kb=100
    )
    bin_20 = ld["bins"][1]
    assert bin_20["bin_kb"] == 20
    assert bin_20["n_pairs"] == 0
    assert bin_20["mean_r2"] is None
    assert ld["cutoff_kb"] == 30
    assert ld["chosen_kb"] == 30


def test_ld_decay_all_empty_bins_reports_honestly():
    g0 = np.array([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2], dtype=np.int8)
    G = np.column_stack([g0, g0])
    variants = [
        {"chrom": "1", "rsid": "s1", "pos": 1000, "a1": "A", "a2": "G"},
        {"chrom": "2", "rsid": "s2", "pos": 1000, "a1": "A", "a2": "G"},
    ]
    ld = compute_ld_decay_curve(
        variants, G, window_kb=50, bin_kb=10, cutoff_r2=0.2, fallback_kb=100
    )
    assert all(b["n_pairs"] == 0 and b["mean_r2"] is None for b in ld["bins"])
    assert ld["cutoff_kb"] is None
    assert ld["chosen_kb"] == 100
    assert ld["min_r2_observed"] is None


def test_select_ld_decay_blocks_proportional_and_contiguous(tmp_path):
    from app.services.structure_service import select_ld_decay_blocks

    bim_path = tmp_path / "cohort.bim"
    lines = []
    for chrom, count in (("1", 500), ("2", 300), ("3", 200)):
        for i in range(count):
            pos = (i + 1) * 1000
            lines.append(f"{chrom}\t{chrom}:{pos}\t0\t{pos}\tA\tG")
    bim_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    res_sub = select_ld_decay_blocks(bim_path, max_snps=100)
    assert res_sub["needs_subset"] is True
    assert res_sub["total_snps"] == 1000
    blocks = res_sub["blocks"]
    assert sum(b["n_snps"] for b in blocks) <= 100
    assert sum(b["n_snps"] for b in blocks) == 100
    by_chr = {b["chrom"]: b for b in blocks}
    assert by_chr["1"]["n_snps"] > by_chr["2"]["n_snps"] > by_chr["3"]["n_snps"]
    for b in blocks:
        assert b["n_snps"] >= 2
        assert b["end_idx"] - b["start_idx"] + 1 == b["n_snps"]
        assert b["start_bp"] <= b["end_bp"]

    res_full = select_ld_decay_blocks(bim_path, max_snps=2000)
    assert res_full["needs_subset"] is False
    assert res_full["selected_snps"] == 1000


def test_run_structure_analysis_ld_decay_uses_non_pruned_bfile_and_subsets_over_limit(
    tmp_path, monkeypatch, toy_bfile
):
    import json
    import uuid
    from app.db.engine.models import AnalysisJob
    from app.tasks import structure_tasks
    from app.utils.process import CLIExecutionResult

    Session, job_id, ds_id, storage_root = _seed_structure_job(tmp_path, monkeypatch, toy_bfile)
    db = Session()
    try:
        job = db.get(AnalysisJob, uuid.UUID(job_id))
        job.parameters = {
            "qc": {"population_type": "inbred"},
            "gwas": {"model": "auto"},
            "structure": {"ld_decay_max_snps": 500},
        }
        db.commit()
    finally:
        db.close()

    captured_cmds = []
    read_prefixes = []

    def fake_run_cli(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        if "--indep-pairwise" in cmd:
            Path(f"{out_prefix}.prune.in").write_text("snp1\nsnp2\n", encoding="utf-8")
        elif "--make-bed" in cmd:
            for ext in (".bed", ".bim", ".fam"):
                Path(f"{out_prefix}{ext}").write_bytes(b"stub")
        return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=Path(kwargs["log_file"]))

    def fake_read_plink(prefix):
        read_prefixes.append(str(prefix))
        rng = np.random.default_rng(7)
        samples = [f"S{i}" for i in range(8)]
        variants = [
            {"chrom": "1", "rsid": f"v{i}", "pos": (i + 1) * 5000, "a1": "A", "a2": "G"}
            for i in range(6)
        ]
        G = rng.integers(0, 3, size=(8, 6), dtype=np.int8)
        return samples, variants, G

    monkeypatch.setattr(structure_tasks, "which_tool", lambda name: "/usr/local/bin/plink2", raising=False)
    monkeypatch.setattr(structure_tasks, "run_cli_command", fake_run_cli, raising=False)
    monkeypatch.setattr(structure_tasks, "read_plink_binary", fake_read_plink)

    structure_tasks.run_structure_analysis(
        {"genotype_bfile_prefix": str(toy_bfile)},
        job_id=job_id,
        dataset_id=ds_id,
    )

    expected_pruned = storage_root / "jobs" / job_id / "structure" / "pruned"
    expected_ld_subset = storage_root / "jobs" / job_id / "structure" / "ld_subset"
    assert read_prefixes == [str(expected_pruned), str(expected_ld_subset)]
    assert any(
        "--extract" in cmd and "bed1" in cmd and "ld_decay_ranges.txt" in " ".join(cmd) and str(expected_ld_subset) in cmd
        for cmd in captured_cmds
    )

    ld_json = json.loads((storage_root / "jobs" / job_id / "structure" / "ld_decay.json").read_text(encoding="utf-8"))
    assert ld_json["method"] == "unphased r^2 (setara plink2 --r2-unphased)"
    assert ld_json["source_bfile"] == str(toy_bfile)
    assert ld_json["n_variants_used"] == 6
    assert len(ld_json["blocks"]) > 0
    assert sum(b["n_snps"] for b in ld_json["blocks"]) <= 500


@pytest.mark.skipif(which_tool("plink2") is None, reason="plink2 tidak tersedia")
def test_ld_decay_matches_plink2_r2_unphased(tmp_path):
    import csv
    import subprocess
    from app.services.structure_service import select_ld_decay_blocks, write_ld_decay_range_file

    assert StructureConfig().ld_decay_max_snps == 20000
    plink2_bin = which_tool("plink2")
    assert plink2_bin is not None

    rng = np.random.default_rng(123)
    n_samples = 60
    n_snps = 40
    samples = [f"S{i + 1:02d}" for i in range(n_samples)]
    G_raw = np.empty((n_samples, n_snps), dtype=np.int8)
    for j in range(n_snps):
        if j > 0 and j % 5 != 0:
            col = G_raw[:, j - 1].copy()
            flip = rng.random(n_samples) < 0.15
            col[flip] = rng.integers(0, 3, size=int(np.sum(flip)), dtype=np.int8)
            G_raw[:, j] = col
        else:
            G_raw[:, j] = rng.integers(0, 3, size=n_samples, dtype=np.int8)

    gt_map = {0: "0/0", 1: "0/1", 2: "1/1"}
    vcf_path = tmp_path / "ld_cmp.vcf"
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=1,length=2000000>",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples),
    ]
    for j in range(n_snps):
        pos = 10_000 + j * 15_000
        gts = [gt_map[int(G_raw[i, j])] for i in range(n_samples)]
        lines.append(f"1\t{pos}\t1:{pos}\tA\tG\t100\tPASS\t.\tGT\t" + "\t".join(gts))
    vcf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    bfile_prefix = tmp_path / "ld_bfile"
    subprocess.run(
        [plink2_bin, "--vcf", str(vcf_path), "--make-bed", "--out", str(bfile_prefix)],
        check=True,
        capture_output=True,
        text=True,
    )
    r2_prefix = tmp_path / "ld_out"
    subprocess.run(
        [
            plink2_bin,
            "--bfile", str(bfile_prefix),
            "--r2-unphased",
            "--ld-window", "99999",
            "--ld-window-kb", "500",
            "--ld-window-r2", "0",
            "--out", str(r2_prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    vcor_path = Path(f"{r2_prefix}.vcor")
    bin_sums: dict[int, float] = {}
    bin_counts: dict[int, int] = {}
    with vcor_path.open("r", encoding="utf-8") as vf:
        reader = csv.DictReader(vf, delimiter="\t")
        for row in reader:
            pos_a = int(row["POS_A"])
            pos_b = int(row["POS_B"])
            r2_val = float(row.get("UNPHASED_R2") or row.get("R2"))
            dist_kb = abs(pos_b - pos_a) / 1000.0
            b_idx = int(dist_kb // 20)
            if 0 <= b_idx < 25:
                bin_sums[b_idx] = bin_sums.get(b_idx, 0.0) + r2_val
                bin_counts[b_idx] = bin_counts.get(b_idx, 0) + 1

    _, variants, G = read_plink_binary(bfile_prefix)
    py_ld = compute_ld_decay_curve(variants, G, window_kb=500, bin_kb=20, cutoff_r2=0.2)
    assert len(bin_counts) > 0
    for b_idx, cnt in bin_counts.items():
        plink_mean = bin_sums[b_idx] / cnt
        py_bin = py_ld["bins"][b_idx]
        assert py_bin["n_pairs"] == cnt
        assert abs(py_bin["mean_r2"] - plink_mean) <= 1e-3

    # Verifikasi range file kompatibel dengan plink2 --extract bed1
    block_info = select_ld_decay_blocks(bfile_prefix.with_suffix(".bim"), max_snps=10)
    assert block_info["needs_subset"] is True
    range_file = write_ld_decay_range_file(block_info["blocks"], tmp_path / "ld_ranges.txt")
    subset_prefix = tmp_path / "ld_subset_real"
    subprocess.run(
        [
            plink2_bin,
            "--bfile", str(bfile_prefix),
            "--allow-extra-chr",
            "--extract", "bed1", str(range_file),
            "--make-bed",
            "--out", str(subset_prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert len(subset_prefix.with_suffix(".bim").read_text(encoding="utf-8").splitlines()) == 10


def _reference_king_robust_loop(G: np.ndarray, sample_ids: list[str]) -> dict:
    """Reference double-loop implementation retained in tests to verify vectorized KING-robust output."""
    n_samples, n_variants = G.shape
    pairs_summary = {
        "duplicate": 0,
        "first_degree": 0,
        "second_degree": 0,
        "third_degree": 0,
        "unrelated": 0,
    }
    top_pairs = []
    if n_samples < 2 or n_variants < 5:
        return {"estimator": "KING-robust", "pairs_summary": pairs_summary, "top_pairs": top_pairs}

    for i in range(n_samples):
        for j in range(i + 1, n_samples):
            g_i = G[i, :]
            g_j = G[j, :]
            valid = (g_i >= 0) & (g_j >= 0)
            if int(np.sum(valid)) < 10:
                continue
            vi = g_i[valid]
            vj = g_j[valid]
            n_het_i = int(np.sum(vi == 1))
            n_het_j = int(np.sum(vj == 1))
            n_het_het = int(np.sum((vi == 1) & (vj == 1)))
            n_ibs0 = int(np.sum((vi == 0) & (vj == 2)) + np.sum((vi == 2) & (vj == 0)))
            denom = n_het_i + n_het_j
            phi = float((n_het_het - 2 * n_ibs0) / denom) if denom > 0 else 0.0
            if phi > 0.354:
                pairs_summary["duplicate"] += 1
                deg = "Duplikat / MZ"
            elif phi >= 0.177:
                pairs_summary["first_degree"] += 1
                deg = "Derajat-1"
            elif phi >= 0.0884:
                pairs_summary["second_degree"] += 1
                deg = "Derajat-2"
            elif phi >= 0.0442:
                pairs_summary["third_degree"] += 1
                deg = "Derajat-3"
            else:
                pairs_summary["unrelated"] += 1
                deg = "Tidak Berkerabat"
            if phi >= 0.0442:
                top_pairs.append({
                    "id1": sample_ids[i],
                    "id2": sample_ids[j],
                    "kinship": round(phi, 4),
                    "relationship": deg,
                })
    top_pairs.sort(key=lambda p: p["kinship"], reverse=True)
    return {"estimator": "KING-robust", "pairs_summary": pairs_summary, "top_pairs": top_pairs[:50]}


def test_king_robust_vectorized_matches_loop_reference_exact():
    rng = np.random.default_rng(20261121)
    n_samples, n_snps = 30, 250
    sample_ids = [f"S_{i:02d}" for i in range(n_samples)]
    G = rng.choice([0, 1, 2], size=(n_samples, n_snps), p=[0.4, 0.4, 0.2]).astype(np.int8)

    # Duplicate pair (0, 1)
    G[1] = G[0]
    # 1st-degree related pair (2, 3)
    share_1st = rng.random(n_snps) < 0.70
    G[3, share_1st] = G[2, share_1st]
    # 2nd/3rd degree pairs (4, 5) and (6, 7)
    share_2nd = rng.random(n_snps) < 0.48
    G[5, share_2nd] = G[4, share_2nd]
    # All-homozygous pair (8, 9) -> denom == 0 edge case
    G[8, :] = rng.choice([0, 2], size=n_snps).astype(np.int8)
    G[9, :] = rng.choice([0, 2], size=n_snps).astype(np.int8)
    # Random ~8% missingness
    miss = rng.random(G.shape) < 0.08
    G[miss] = -1
    # Sparse pair with < 10 valid overlapping SNPs (sample 10 mostly missing)
    G[10, 5:] = -1

    vec_res = compute_king_robust_table(G, sample_ids)
    ref_res = _reference_king_robust_loop(G, sample_ids)

    assert vec_res["pairs_summary"] == ref_res["pairs_summary"]
    assert len(vec_res["top_pairs"]) == len(ref_res["top_pairs"])
    for v_pair, r_pair in zip(vec_res["top_pairs"], ref_res["top_pairs"]):
        assert v_pair["id1"] == r_pair["id1"]
        assert v_pair["id2"] == r_pair["id2"]
        assert v_pair["relationship"] == r_pair["relationship"]
        assert abs(v_pair["kinship"] - r_pair["kinship"]) <= 1e-9

    # Also exercise multi-chunk variant summation (> 8192 SNPs)
    G_chunk = rng.choice([0, 1, 2], size=(6, 8250), p=[0.45, 0.35, 0.2]).astype(np.int8)
    G_chunk[1] = G_chunk[0]
    G_chunk[rng.random(G_chunk.shape) < 0.05] = -1
    chunk_ids = [f"C_{i}" for i in range(6)]
    assert compute_king_robust_table(G_chunk, chunk_ids) == _reference_king_robust_loop(G_chunk, chunk_ids)

    # Verify cancel_check callback is honored
    calls = []

    def _cancel():
        calls.append(1)
        raise RuntimeError("cancelled-king")

    with pytest.raises(RuntimeError, match="cancelled-king"):
        compute_king_robust_table(G, sample_ids, cancel_check=_cancel)
    assert len(calls) == 1
