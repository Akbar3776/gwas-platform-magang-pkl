from pathlib import Path

import numpy as np
import pytest

from app.schemas.pipeline import QCPipelineConfig
from app.services.qc_service import run_python_fallback_qc
from app.services.structure_service import (
    compute_centered_kinship,
    compute_king_robust_table,
    compute_ld_decay_curve,
    determine_model_recommendation,
    read_plink_binary,
    run_pca_decomposition,
)
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
    assert read_prefixes == [str(expected_pruned)]

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
