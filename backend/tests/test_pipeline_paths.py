import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.api.routes import pipeline as pipeline_routes
from app.db.engine.models import Dataset, ReferencePanel, UploadStatus, User, UserRole
from app.schemas.pipeline import FullPipelineRequest, ImputationPipelineConfig


class FakeSession:
    def __init__(self, dataset: Dataset, panels: list[ReferencePanel] | None = None):
        self.dataset = dataset
        self.panels = {str(p.id): p for p in (panels or [])}
        self.added = []

    def get(self, model, object_id):
        if model is Dataset and str(object_id) == str(self.dataset.id):
            return self.dataset
        if model is ReferencePanel:
            return self.panels.get(str(object_id))
        return None

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()


def test_gwas_config_ignores_client_phenotype_path():
    req = FullPipelineRequest.model_validate({"gwas": {"phenotype_path": "/etc/passwd"}})
    assert "phenotype_path" not in req.model_dump()["gwas"]


def test_imputation_config_requires_reference_panel_id_not_raw_paths():
    assert "reference_vcf_path" not in ImputationPipelineConfig.model_fields
    assert "map_path" not in ImputationPipelineConfig.model_fields
    assert "reference_panel_id" in ImputationPipelineConfig.model_fields

    with pytest.raises(ValidationError):
        ImputationPipelineConfig(mode="reference")

    panel_id = uuid.uuid4()
    cfg = ImputationPipelineConfig(reference_panel_id=panel_id)
    assert cfg.mode == "reference"
    assert cfg.reference_panel_id == panel_id


def test_start_pipeline_rejects_dataset_without_phenotype_even_with_payload_path(tmp_path, monkeypatch):
    other_pheno = tmp_path / "other_user_pheno.txt"
    other_pheno.write_text("FID\tIID\tPHENO1\nS1\tS1\t1.0\n", encoding="utf-8")
    vcf_file = tmp_path / "cohort.vcf.gz"
    vcf_file.write_bytes(b"vcf")

    owner = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(vcf_file),
        phenotype_file_path=None,
        file_size_bytes=3,
        upload_status=UploadStatus.UPLOADED,
    )
    db = FakeSession(dataset)

    monkeypatch.setattr(pipeline_routes, "trigger_full_pipeline", lambda **kwargs: "task-1")
    payload = FullPipelineRequest.model_validate({"gwas": {"phenotype_path": str(other_pheno)}})

    with pytest.raises(HTTPException) as exc:
        pipeline_routes.start_pipeline_endpoint(
            dataset_id=dataset.id,
            payload=payload,
            db=db,
            user=owner,
        )
    assert exc.value.status_code == 409


def test_start_pipeline_resolves_reference_panel_server_side_and_rejects_missing(tmp_path, monkeypatch):
    vcf_file = tmp_path / "cohort.vcf.gz"
    vcf_file.write_bytes(b"vcf")
    pheno_file = tmp_path / "pheno.txt"
    pheno_file.write_text("FID\tIID\tPHENO1\nS1\tS1\t1.0\n", encoding="utf-8")

    owner = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(vcf_file),
        phenotype_file_path=str(pheno_file),
        file_size_bytes=3,
        upload_status=UploadStatus.UPLOADED,
    )

    inactive_panel = ReferencePanel(
        id=uuid.uuid4(),
        name="Inactive",
        population="ALL",
        genome_build="GRCh38",
        vcf_paths={"ALL": "/data/ref/inactive.vcf.gz"},
        genetic_map_paths={},
        is_active=False,
    )
    active_panel = ReferencePanel(
        id=uuid.uuid4(),
        name="Active",
        population="ALL",
        genome_build="GRCh38",
        vcf_paths={"ALL": "/data/ref/active.vcf.gz"},
        genetic_map_paths={"ALL": "/data/ref/active.map"},
        is_active=True,
    )
    db = FakeSession(dataset, panels=[inactive_panel, active_panel])

    captured = {}

    def fake_trigger(**kwargs):
        captured.update(kwargs)
        return "task-ok"

    monkeypatch.setattr(pipeline_routes, "trigger_full_pipeline", fake_trigger)

    missing_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(uuid.uuid4())}}
    )
    with pytest.raises(HTTPException) as exc:
        pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=missing_payload, db=db, user=owner)
    assert exc.value.status_code == 422

    inactive_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(inactive_panel.id)}}
    )
    with pytest.raises(HTTPException) as exc2:
        pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=inactive_payload, db=db, user=owner)
    assert exc2.value.status_code == 422

    valid_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(active_panel.id)}}
    )
    resp = pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=valid_payload, db=db, user=owner)
    assert resp.celery_task_id == "task-ok"
    assert captured["imputation_params"]["reference_vcf_path"] == "/data/ref/active.vcf.gz"
    assert captured["imputation_params"]["map_path"] == "/data/ref/active.map"
    assert captured["imputation_params"]["reference_panel_id"] == str(active_panel.id)


def test_discover_resume_state_requires_multilocus_summary_or_skipped(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.tasks.pipeline_orchestrator import discover_resume_state

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    job_root = tmp_path / "jobs" / job_id

    for rel in (
        "inspection/pheno_plink.txt",
        "inspection/keep_ids.txt",
        "normalization/normalized.vcf.gz",
        "qc/plink_qc.bed",
        "qc/plink_qc.vcf.gz",
        "imputation/skipped.json",
        "structure/kinship.cXX.txt",
        "structure/model_recommendation.json",
        "gwas/gwas_results.normalized.tsv",
        "gwas/output/association.assoc.txt",
        "multilocus/multilocus.log",
    ):
        target = job_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")

    _, stage, _ = discover_resume_state(job_id=job_id, dataset_id=dataset_id)
    assert stage == "multilocus"

    (job_root / "multilocus" / "multilocus_summary.json").write_text("{}", encoding="utf-8")
    _, stage_after_summary, _ = discover_resume_state(job_id=job_id, dataset_id=dataset_id)
    assert stage_after_summary == "postgwas"

    (job_root / "multilocus" / "multilocus_summary.json").unlink()
    (job_root / "multilocus" / "skipped.json").write_text("{}", encoding="utf-8")
    _, stage_after_skip, _ = discover_resume_state(job_id=job_id, dataset_id=dataset_id)
    assert stage_after_skip == "postgwas"


def test_normalization_norm_uses_tmp_and_removes_final_on_cancel(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
    from app.tasks import normalization_tasks
    from app.utils.process import CLICancelledError
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "cohort.vcf"
    vcf_file.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\trs1\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="cohort.vcf",
        storage_path=str(vcf_file),
        file_size_bytes=vcf_file.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=ds.owner_id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.NORMALIZATION,
        overall_status=JobStatus.RUNNING_NORMALIZATION,
        parameters={},
    )
    db.add(ds)
    db.add(job)

    norm_dir = tmp_path / "jobs" / str(job.id) / "normalization"
    norm_dir.mkdir(parents=True)
    stale_final = norm_dir / "normalized.vcf.gz"
    stale_final.write_bytes(b"stale")
    seen_norm_out = []

    def mock_run_cli(cmd, **kwargs):
        if "sort" in cmd:
            out_path = cmd[cmd.index("-o") + 1]
            with open(out_path, "wb") as f:
                f.write(b"sorted")
            return MagicMock(returncode=0)
        if "norm" in cmd:
            out_path = cmd[cmd.index("-o") + 1]
            seen_norm_out.append(out_path)
            with open(out_path, "wb") as f:
                f.write(b"partial-norm")
            raise CLICancelledError(cmd, kwargs.get("log_file"), ())
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.which_tool", lambda name: "bcftools")
    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    with pytest.raises(CLICancelledError):
        normalization_tasks.run_vcf_normalization(
            job_id=str(job.id),
            dataset_id=str(ds.id),
            params={"reference_fasta": "/ref.fa"},
        )

    assert len(seen_norm_out) == 1
    assert seen_norm_out[0].endswith("normalized.vcf.gz.tmp")
    assert not (norm_dir / "normalized.vcf.gz.tmp").exists()
    assert not stale_final.exists()


def test_discover_resume_state_returns_both_keep_ids_and_keep_samples_after_inspection(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.tasks import pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w13-keep"
    insp_dir = tmp_path / "jobs" / job_id / "inspection"
    insp_dir.mkdir(parents=True)
    (insp_dir / "pheno_plink.txt").write_text("FID\tIID\tT\nS1\tS1\t1.0\n", encoding="utf-8")
    (insp_dir / "keep_ids.txt").write_text("S1 S1\n", encoding="utf-8")

    _, start_stage, ctx = pipeline_orchestrator.discover_resume_state(
        job_id=job_id,
        dataset_id="ds-w13",
        job_parameters={},
    )
    assert start_stage == "normalization"
    expected_keep = str((insp_dir / "keep_ids.txt").resolve())
    assert ctx["keep_ids_path"] == expected_keep
    assert ctx["keep_samples_path"] == expected_keep


def test_discover_resume_state_requires_split_map_when_chrom_split_recorded(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.tasks import pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w13-split"
    job_root = tmp_path / "jobs" / job_id
    (job_root / "inspection").mkdir(parents=True)
    (job_root / "inspection" / "pheno_plink.txt").write_text("FID\tIID\tT\n", encoding="utf-8")
    (job_root / "inspection" / "keep_ids.txt").write_text("S1 S1\n", encoding="utf-8")
    (job_root / "normalization").mkdir(parents=True)
    (job_root / "normalization" / "normalized.vcf.gz").write_bytes(b"gz")

    params = {"chrom_split": {"applied": True, "overflow_chromosomes": ["chr7"]}}

    # Missing chrom_split_map.json -> must restart at normalization
    _, start_stage, ctx = pipeline_orchestrator.discover_resume_state(
        job_id=job_id,
        dataset_id="ds-w13",
        job_parameters=params,
    )
    assert start_stage == "normalization"
    assert "split_map_path" not in ctx

    # With chrom_split_map.json present -> advances to qc and exposes split_map_path
    map_file = job_root / "normalization" / "chrom_split_map.json"
    map_file.write_text('{"parts": {}}', encoding="utf-8")
    _, start_stage2, ctx2 = pipeline_orchestrator.discover_resume_state(
        job_id=job_id,
        dataset_id="ds-w13",
        job_parameters=params,
    )
    assert start_stage2 == "qc"
    assert ctx2["split_map_path"] == str(map_file.resolve())


def test_discover_resume_state_legacy_without_chrom_split_advances_past_normalization(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.tasks import pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w13-legacy"
    job_root = tmp_path / "jobs" / job_id
    (job_root / "inspection").mkdir(parents=True)
    (job_root / "inspection" / "pheno_plink.txt").write_text("FID\tIID\tT\n", encoding="utf-8")
    (job_root / "inspection" / "keep_ids.txt").write_text("S1 S1\n", encoding="utf-8")
    (job_root / "normalization").mkdir(parents=True)
    (job_root / "normalization" / "normalized.vcf.gz").write_bytes(b"gz")

    _, start_stage, ctx = pipeline_orchestrator.discover_resume_state(
        job_id=job_id,
        dataset_id="ds-w13",
        job_parameters={},
    )
    assert start_stage == "qc"
    assert "split_map_path" not in ctx


def _patch_fake_retry_chain(monkeypatch, pipeline_orchestrator):
    captured: dict[str, object] = {}

    class _FakeNode:
        id = "retry-chain-root"
        parent = None

    class _FakeChain:
        def __init__(self, *sigs):
            captured["sigs"] = list(sigs)

        def apply_async(self):
            return _FakeNode()

    monkeypatch.setattr(pipeline_orchestrator, "chain", _FakeChain)
    monkeypatch.setattr(pipeline_orchestrator, "_persist_chain_task_ids", lambda job_id, task_ids: None)
    return captured


def test_resume_after_structure_failure_preserves_and_reuses_split_map(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.chromosome_service import load_split_map, restore_coord, save_split_map
    from app.tasks import pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w26-resume"
    dataset_id = "ds-w26-resume"
    job_root = tmp_path / "jobs" / job_id

    for rel in (
        "inspection/pheno_plink.txt",
        "inspection/keep_ids.txt",
        "normalization/normalized.vcf.gz",
        "qc/plink_qc.bed",
        "qc/plink_qc.vcf.gz",
        "imputation/skipped.json",
        "structure/stale_partial.tmp",
    ):
        target = job_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")

    map_path = save_split_map(
        job_id,
        {
            "version": 1,
            "threshold_bp": 5000,
            "unit_bp": 2000,
            "parts": {
                "chr7_p0": {"orig_chr": "chr7", "part": 0, "offset_bp": 0},
                "chr7_p1": {"orig_chr": "chr7", "part": 1, "offset_bp": 2000},
                "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4000},
            },
        },
    )
    captured = _patch_fake_retry_chain(monkeypatch, pipeline_orchestrator)

    task_id, start_idx, start_stage = pipeline_orchestrator.trigger_retry_pipeline(
        job_id=job_id,
        dataset_id=dataset_id,
        job_parameters={"chrom_split": {"applied": True, "overflow_chromosomes": ["chr7"]}, "imputation": {"mode": "none"}},
        failed_step="structure",
        from_scratch=False,
        run_id="resume-run",
    )

    assert task_id == "retry-chain-root"
    assert start_idx == 4
    assert start_stage == "structure"
    sigs = captured["sigs"]
    assert len(sigs) == 6
    first_task_name = getattr(sigs[0], "task", None) or sigs[0].task_wrapper.name
    assert first_task_name == "app.tasks.structure_tasks.run_structure_analysis"
    initial_ctx = sigs[0].args[0]
    assert initial_ctx["split_map_path"] == str(map_path.resolve())
    assert map_path.is_file()
    assert not (job_root / "structure" / "stale_partial.tmp").exists()
    reloaded = load_split_map(job_id)
    assert reloaded is not None
    assert restore_coord("chr7_p2", 1500, reloaded) == ("chr7", 5500)


def test_retry_from_scratch_deletes_stale_split_map_and_regenerates(tmp_path, monkeypatch, overflow_dataset):
    from app.core.config import settings
    from app.services.chromosome_service import (
        load_split_map,
        restore_coord,
        save_split_map,
        split_overflow_chromosomes,
    )
    from app.tasks import pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w26-scratch"
    dataset_id = "ds-w26-scratch"
    job_root = tmp_path / "jobs" / job_id

    for rel in (
        "inspection/stale.txt",
        "normalization/normalized.vcf.gz",
        "qc/stale.bed",
    ):
        target = job_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stale", encoding="utf-8")

    stale_map_file = save_split_map(
        job_id,
        {
            "version": 1,
            "threshold_bp": 999,
            "unit_bp": 999,
            "parts": {"chr99_p0": {"orig_chr": "chr99", "part": 0, "offset_bp": 0}},
        },
    )
    captured = _patch_fake_retry_chain(monkeypatch, pipeline_orchestrator)

    _, start_idx, start_stage = pipeline_orchestrator.trigger_retry_pipeline(
        job_id=job_id,
        dataset_id=dataset_id,
        job_parameters={},
        from_scratch=True,
        run_id="scratch-run",
    )

    assert start_idx == 0
    assert start_stage == "inspection"
    assert len(captured["sigs"]) == 10
    assert not stale_map_file.exists()
    assert not (job_root / "normalization" / "normalized.vcf.gz").exists()
    assert not (job_root / "qc" / "stale.bed").exists()
    assert not (job_root / "inspection" / "stale.txt").exists()
    assert load_split_map(job_id) is None

    split_out = split_overflow_chromosomes(
        overflow_dataset["vcf_path"],
        job_root / "normalization" / "split_input.vcf",
        threshold_bp=overflow_dataset["split_threshold_bp"],
        unit_bp=overflow_dataset["split_unit_bp"],
    )
    regen_map = save_split_map(job_id, split_out["split_map"])
    assert regen_map.is_file()
    reloaded = load_split_map(job_id)
    assert reloaded is not None
    assert "chr99_p0" not in (reloaded.get("parts") or {})
    assert restore_coord("chr7_p2", 1500, reloaded) == ("chr7", 5500)


def test_legacy_job_without_split_map_resumes_and_restores_identically(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.chromosome_service import load_split_map, restore_coord
    from app.services.flanking_service import collect_significant_snps
    from app.tasks import multilocus_tasks, pipeline_orchestrator

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    job_id = "job-w26-legacy"
    job_root = tmp_path / "jobs" / job_id

    for rel in (
        "inspection/pheno_plink.txt",
        "inspection/keep_ids.txt",
        "normalization/normalized.vcf.gz",
        "qc/plink_qc.bed",
        "qc/plink_qc.vcf.gz",
    ):
        target = job_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("legacy", encoding="utf-8")

    _, start_stage, ctx = pipeline_orchestrator.discover_resume_state(
        job_id=job_id,
        dataset_id="ds-w26-legacy",
        job_parameters={},
    )
    assert start_stage == "imputation"
    assert "split_map_path" not in ctx
    assert load_split_map(job_id) is None
    assert restore_coord("chr1", 500, None) == ("chr1", 500)

    single = [{"snp": "chr1:500", "chr": "1", "pos": 500, "p": 1e-8, "beta": 1.2, "source": "GEMMA"}]
    multi = [{"snp": "chr1:500", "chr": "1", "pos": 500, "p": 5e-9, "method": "BLINK", "is_significant": True}]

    collected_omitted = collect_significant_snps(single, multi, p_threshold=1e-5)
    collected_none = collect_significant_snps(single, multi, p_threshold=1e-5, split_map=None)
    assert collected_omitted == collected_none
    assert collected_none[0]["orig_chr"] == "1"
    assert collected_none[0]["orig_pos"] == 500

    res_omitted = multilocus_tasks.generate_peak_comparison(
        single,
        multi,
        tmp_path / "omitted.csv",
        tmp_path / "omitted.json",
    )
    res_none = multilocus_tasks.generate_peak_comparison(
        single,
        multi,
        tmp_path / "none.csv",
        tmp_path / "none.json",
        split_map=None,
    )
    assert res_omitted == res_none
    assert (tmp_path / "omitted.csv").read_text(encoding="utf-8") == (tmp_path / "none.csv").read_text(encoding="utf-8")
    assert (tmp_path / "omitted.json").read_text(encoding="utf-8") == (tmp_path / "none.json").read_text(encoding="utf-8")
