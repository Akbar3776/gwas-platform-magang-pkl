from datetime import datetime, timedelta, timezone
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.routes import jobs
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import pipeline_orchestrator


class FakeSession:
    def __init__(self, dataset, job=None):
        self.dataset = dataset
        self.job = job
        self.added = []

    def get(self, model, object_id):
        if model is Dataset and str(object_id) == str(self.dataset.id):
            return self.dataset
        if model is AnalysisJob and self.job is not None and str(object_id) == str(self.job.id):
            return self.job
        return next((item for item in self.added if isinstance(item, model)), None)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()


def test_trigger_qc_creates_pending_job_and_enqueues_task(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    session = FakeSession(dataset)
    task_call = {}

    class FakeTask:
        id = "celery-task-123"

    def fake_delay(*args, **kwargs):
        task_call["args"] = args
        task_call["kwargs"] = kwargs
        return FakeTask()

    monkeypatch.setattr(jobs.run_qc_pipeline, "delay", fake_delay)
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/v1")
    app.dependency_overrides[jobs.get_db] = lambda: session
    app.dependency_overrides[jobs.get_current_user] = lambda: user

    response = TestClient(app).post(
        "/api/v1/jobs/qc",
        json={"dataset_id": str(dataset.id), "maf": 0.01, "geno": 0.05, "mind": 0.1, "hwe": 1e-6},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["overall_status"] == "pending"
    assert body["celery_task_id"] == "celery-task-123"
    assert task_call["kwargs"]["job_id"] == body["job_id"]


def test_retry_job_resumes_from_failed_stage(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_gwas",
        overall_status=JobStatus.FAILED,
        current_step=PipelineStep.STRUCTURE,
        failed_step="gwas",
        error_message="model harus bernilai 'lmm' atau 'lm'",
        parameters={"gwas": {"model": "auto"}},
    )
    session = FakeSession(dataset, job=job)
    retry_calls = {}

    def fake_trigger_retry(**kwargs):
        retry_calls.update(kwargs)
        return ("retry-task-999", 5, "gwas")

    import app.tasks.pipeline_orchestrator as orch

    monkeypatch.setattr(orch, "trigger_retry_pipeline", fake_trigger_retry)
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/v1")
    app.dependency_overrides[jobs.get_db] = lambda: session
    app.dependency_overrides[jobs.get_current_user] = lambda: user

    response = TestClient(app).post(f"/api/v1/jobs/{job.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["retried"] is True
    assert body["resumed_from_step"] == "gwas"
    assert body["resumed_from_stage_number"] == 6
    assert body["overall_status"] == "running_gwas"
    assert job.error_message is None
    assert job.failed_step is None


def test_retry_job_rejects_running_or_completed_status():
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    for bad_status in (JobStatus.RUNNING_GWAS, JobStatus.COMPLETED):
        job = AnalysisJob(
            id=uuid.uuid4(),
            dataset_id=dataset.id,
            triggered_by=user.id,
            pipeline_type="full_gwas",
            overall_status=bad_status,
            current_step=PipelineStep.GWAS,
        )
        session = FakeSession(dataset, job=job)
        with pytest.raises(HTTPException) as exc:
            jobs.retry_job(job_id=job.id, from_scratch=False, db=session, user=user)
        assert exc.value.status_code == 409


def test_trigger_retry_pipeline_strips_legacy_client_paths(monkeypatch):
    captured = {}

    class FakeChain:
        def __init__(self, *sigs):
            captured["sigs"] = sigs

        def apply_async(self):
            class Res:
                id = "retry-task-id"

            return Res()

    monkeypatch.setattr(pipeline_orchestrator, "chain", lambda *sigs: FakeChain(*sigs))

    pipeline_orchestrator.trigger_retry_pipeline(
        job_id=str(uuid.uuid4()),
        dataset_id=str(uuid.uuid4()),
        job_parameters={
            "gwas": {"phenotype_path": "/etc/passwd"},
            "imputation": {
                "mode": "reference",
                "reference_vcf_path": "/x",
                "map_path": "/y",
            },
        },
        failed_step="qc",
        from_scratch=True,
    )

    sigs_by_task = {
        getattr(sig, "task", None) or sig.task_wrapper.name: sig
        for sig in captured["sigs"]
    }
    gemma_sig = sigs_by_task["app.tasks.gemma_tasks.run_gemma_gwas"]
    imputation_sig = sigs_by_task["app.tasks.imputation_tasks.run_imputation"]
    assert gemma_sig.kwargs["phenotype_path"] == ""
    assert "/etc/passwd" not in str(gemma_sig.kwargs)
    assert imputation_sig.kwargs["reference_vcf_path"] == ""
    assert "/x" not in str(imputation_sig.kwargs)
    assert "/y" not in str(imputation_sig.kwargs)


def test_retry_job_sets_pending_and_new_run_id_before_trigger(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.CANCELLED,
        current_step=PipelineStep.IMPUTATION,
        parameters={"run_id": "old-run"},
        completed_at=datetime.now(timezone.utc) - timedelta(seconds=60),
    )
    session = FakeSession(dataset, job=job)
    observed = {}

    def _fake_retry(**kwargs):
        observed["status_during_trigger"] = job.overall_status
        observed["run_id_during_trigger"] = (job.parameters or {}).get("run_id")
        return "new-celery-id", 3, "imputation"

    monkeypatch.setattr(pipeline_orchestrator, "trigger_retry_pipeline", _fake_retry)
    res = jobs.retry_job(job_id=job.id, from_scratch=False, db=session, user=user)

    assert observed["status_during_trigger"] == JobStatus.PENDING
    assert observed["run_id_during_trigger"] != "old-run"
    assert res["overall_status"] == "running_imputation"


def test_retry_job_marks_failed_and_returns_500_when_enqueue_fails(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.FAILED,
        current_step=PipelineStep.GWAS,
    )
    session = FakeSession(dataset, job=job)

    def _boom(**kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(pipeline_orchestrator, "trigger_retry_pipeline", _boom)
    with pytest.raises(HTTPException) as exc:
        jobs.retry_job(job_id=job.id, from_scratch=False, db=session, user=user)

    assert exc.value.status_code == 500
    assert job.overall_status == JobStatus.FAILED
    assert "Gagal enqueue retry" in (job.error_message or "")


def test_retry_job_blocks_recent_cancellation_and_allows_after_10s(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    recent_job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.CANCELLED,
        current_step=PipelineStep.GWAS,
        completed_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    session_recent = FakeSession(dataset, job=recent_job)
    with pytest.raises(HTTPException) as exc:
        jobs.retry_job(job_id=recent_job.id, from_scratch=False, db=session_recent, user=user)
    assert exc.value.status_code == 409

    older_job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.CANCELLED,
        current_step=PipelineStep.GWAS,
        completed_at=datetime.now(timezone.utc) - timedelta(seconds=60),
    )
    session_older = FakeSession(dataset, job=older_job)
    monkeypatch.setattr(
        pipeline_orchestrator,
        "trigger_retry_pipeline",
        lambda **kwargs: ("celery-ok", 5, "gwas"),
    )
    res = jobs.retry_job(job_id=older_job.id, from_scratch=False, db=session_older, user=user)
    assert res["retried"] is True


def test_read_log_tail_reads_only_last_64kb_of_large_file(tmp_path):
    log_path = tmp_path / "huge.log"
    lines = [f"log-entry-{i:05d} " + ("x" * 50) for i in range(20000)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log_path.stat().st_size > 1_000_000

    tail, line_count = jobs._read_log_tail([log_path])
    assert "log-entry-19999" in tail
    assert "log-entry-00000" not in tail
    assert line_count < 20000
    assert len(tail.splitlines()) <= 2000


class _JobListQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def all(self):
        return list(self.rows)


class _JobListSession(FakeSession):
    def __init__(self, ds, j):
        super().__init__(ds, job=j)
        self.commits = 0

    def query(self, model):
        return _JobListQuery([self.job] if model is AnalysisJob else [])

    def commit(self):
        self.commits += 1


def test_list_user_jobs_skips_full_log_scan_and_caches_terminal_file_count(tmp_path, monkeypatch):
    jobs._FILE_COUNT_CACHE.clear()
    monkeypatch.setattr(jobs.settings, "STORAGE_ROOT", str(tmp_path))
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    vcf = tmp_path / "cohort.vcf.gz"
    vcf.write_bytes(b"vcf")
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(vcf),
        file_size_bytes=3,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.ANNOTATION,
        parameters={"last_log_line": "Done annotation"},
        completed_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
    )
    stage_folder = tmp_path / "jobs" / str(job.id) / "qc"
    stage_folder.mkdir(parents=True)
    (stage_folder / "plink_qc.log").write_text("ok", encoding="utf-8")

    db = _JobListSession(dataset, job)
    orig_count = jobs._count_job_files
    count_calls = []
    monkeypatch.setattr(
        jobs,
        "_count_job_files",
        lambda j, ds=None: (count_calls.append(1), orig_count(j, ds))[1],
    )
    monkeypatch.setattr(jobs, "_discover_job_files", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not scan")))
    monkeypatch.setattr(jobs, "_collect_step_logs", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not collect logs")))

    first = jobs.list_user_jobs(db=db, user=user)
    assert first[0]["last_log_line"] == "Done annotation"
    assert first[0]["file_count"] == 2
    assert "file_count" not in (job.parameters or {})
    assert db.commits == 0
    assert len(count_calls) == 1

    # Next call should reuse in-memory cache without re-walking disk or writing to DB
    (stage_folder / "extra_after.log").write_text("extra", encoding="utf-8")
    second = jobs.list_user_jobs(db=db, user=user)
    assert second[0]["file_count"] == 2
    assert db.commits == 0
    assert len(count_calls) == 1


def test_list_user_jobs_recomputes_when_completed_at_changes_and_ignores_stale_param(tmp_path, monkeypatch):
    jobs._FILE_COUNT_CACHE.clear()
    monkeypatch.setattr(jobs.settings, "STORAGE_ROOT", str(tmp_path))
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(tmp_path / "missing.vcf.gz"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    completed_a = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    completed_b = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.ANNOTATION,
        parameters={"file_count": 999},
        completed_at=completed_a,
    )
    stage_folder = tmp_path / "jobs" / str(job.id) / "qc"
    stage_folder.mkdir(parents=True)
    (stage_folder / "one.log").write_text("1", encoding="utf-8")

    db = _JobListSession(dataset, job)
    orig_count = jobs._count_job_files
    count_calls = []
    monkeypatch.setattr(
        jobs,
        "_count_job_files",
        lambda j, ds=None: (count_calls.append(1), orig_count(j, ds))[1],
    )

    res_a1 = jobs.list_user_jobs(db=db, user=user)
    assert res_a1[0]["file_count"] == 1
    assert len(count_calls) == 1
    assert job.parameters == {"file_count": 999}
    assert db.commits == 0

    res_a2 = jobs.list_user_jobs(db=db, user=user)
    assert res_a2[0]["file_count"] == 1
    assert len(count_calls) == 1

    # New completion timestamp (e.g. after retry) must recompute _count_job_files
    (stage_folder / "two.log").write_text("2", encoding="utf-8")
    job.completed_at = completed_b
    res_b = jobs.list_user_jobs(db=db, user=user)
    assert res_b[0]["file_count"] == 2
    assert len(count_calls) == 2

    # Terminal job with completed_at=None must not read/write cache
    job.completed_at = None
    (stage_folder / "three.log").write_text("3", encoding="utf-8")
    res_none = jobs.list_user_jobs(db=db, user=user)
    assert res_none[0]["file_count"] == 3
    assert len(count_calls) == 3

    # Cache clears before insert when exceeding 5000 entries
    job.completed_at = completed_a
    jobs._FILE_COUNT_CACHE.clear()
    for i in range(5001):
        jobs._FILE_COUNT_CACHE[(f"dummy-{i}", "2026-01-01T00:00:00+00:00")] = i
    jobs.list_user_jobs(db=db, user=user)
    assert len(jobs._FILE_COUNT_CACHE) == 1


def test_get_job_file_content_requires_exact_file_key(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.settings, "STORAGE_ROOT", str(tmp_path))
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(tmp_path / "missing.vcf.gz"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="qc_only",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.QC,
    )
    log_file = tmp_path / "jobs" / str(job.id) / "qc" / "plink_qc.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("qc-log-content\n", encoding="utf-8")
    session = FakeSession(dataset, job=job)

    listed = jobs.list_job_files(job_id=job.id, db=session, user=user)
    assert listed["files"][0]["file_key"] == "job:qc/plink_qc.log"

    for non_exact in ("log", "plink_qc.log", "JOB:QC/PLINK_QC.LOG"):
        with pytest.raises(HTTPException) as exc:
            jobs.get_job_file_content(
                job_id=job.id,
                file_key=non_exact,
                preview=True,
                db=session,
                user=user,
            )
        assert exc.value.status_code == 404

    resp = jobs.get_job_file_content(
        job_id=job.id,
        file_key="job:qc/plink_qc.log",
        preview=True,
        db=session,
        user=user,
    )
    assert resp.status_code == 200
    assert resp.body.decode("utf-8") == "qc-log-content"


def test_build_job_steps_reflects_skipped_stages_and_glm_model(tmp_path):
    from app.api.routes import report as report_routes

    job_id = uuid.uuid4()
    job_root = tmp_path / "jobs" / str(job_id)
    (job_root / "normalization").mkdir(parents=True)
    (job_root / "normalization" / "normalized.vcf.gz").write_bytes(b"vcf")
    (job_root / "qc").mkdir(parents=True)
    (job_root / "qc" / "plink_qc.bed").write_bytes(b"bed")
    (job_root / "imputation").mkdir(parents=True)
    (job_root / "imputation" / "skipped.json").write_text('{"status": "skipped"}', encoding="utf-8")
    (job_root / "gwas").mkdir(parents=True)
    (job_root / "gwas" / "gwas_results.normalized.tsv").write_text("chr\tps\n", encoding="utf-8")
    (job_root / "multilocus").mkdir(parents=True)
    (job_root / "multilocus" / "skipped.json").write_text('{"status": "skipped"}', encoding="utf-8")

    job = AnalysisJob(
        id=job_id,
        dataset_id=uuid.uuid4(),
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.ANNOTATION,
        parameters={"gwas": {"model": "glm"}},
        completed_at=datetime.now(timezone.utc),
    )

    steps = report_routes.build_job_steps(job, job_root=job_root)
    by_name = {s["step_name"]: s for s in steps}

    assert by_name["Normalisasi VCF (bcftools)"]["status"] == "COMPLETED"
    assert by_name["Quality Control (PLINK)"]["status"] == "COMPLETED"
    assert by_name["Imputasi Genotipe (Beagle)"]["status"] == "SKIPPED"
    assert by_name["Analisis Multi-Locus (FarmCPU / BLINK / MLMM)"]["status"] == "SKIPPED"
    assert any(
        "GEMMA -lm" in s["step_name"] and s["status"] == "COMPLETED"
        for s in steps
    )
    assert by_name["Visualisasi & Cache Plot"]["status"] == "NOT_RUN"


def test_get_primers_data_forwards_regions_and_nearby_variants(tmp_path, monkeypatch):
    import json
    from app.api.routes import results
    from app.core.security import get_current_user
    from app.db.engine.database import get_db

    monkeypatch.setattr(results.settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    other_user = User(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(tmp_path / "cohort.vcf.gz"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=owner.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.ANNOTATION,
    )
    anno_dir = tmp_path / "jobs" / str(job.id) / "annotation"
    anno_dir.mkdir(parents=True)
    (anno_dir / "primers.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "flank_bp": 100,
                "regions_status": "completed",
                "candidate_regions": {
                    "csv": "/abs/path/candidate_regions.csv",
                    "bed": "candidate_regions.bed",
                    "fasta": "/abs/candidate_regions.fa",
                    "n_regions": 2,
                    "written_regions": 2,
                    "total_bp": 400002,
                    "max_total_bp": 50000000,
                    "window_bp": 200000,
                },
                "total_markers": 1,
                "markers_with_primers": 1,
                "markers": [
                    {
                        "rank": 1,
                        "marker_id": "trait_1_1000",
                        "snp": "rs1",
                        "chr": "1",
                        "pos": 1000,
                        "nearby_variants": [{"pos": 1050, "ref": "A", "alt": "G", "offset": 50}],
                        "nearby_variants_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    session = FakeSession(dataset, job=job)
    active_user = {"user": owner}
    app = FastAPI()
    app.include_router(results.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: active_user["user"]

    client = TestClient(app)
    resp = client.get(f"/api/v1/results/{job.id}/primers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["regions_status"] == "completed"
    assert body["candidate_regions"]["csv"] == "candidate_regions.csv"
    assert body["candidate_regions"]["bed"] == "candidate_regions.bed"
    assert body["candidate_regions"]["fasta"] == "candidate_regions.fa"
    assert body["markers"][0]["nearby_variants"] == [{"pos": 1050, "ref": "A", "alt": "G", "offset": 50}]
    assert body["markers"][0]["nearby_variants_count"] == 1

    active_user["user"] = other_user
    denied = client.get(f"/api/v1/results/{job.id}/primers")
    assert denied.status_code == 404


def test_preview_bed_and_fasta_files_capped_at_150_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    other_user = User(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(tmp_path / "missing.vcf.gz"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=owner.id,
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        current_step=PipelineStep.ANNOTATION,
    )
    anno_dir = tmp_path / "jobs" / str(job.id) / "annotation"
    qc_dir = tmp_path / "jobs" / str(job.id) / "qc"
    anno_dir.mkdir(parents=True)
    qc_dir.mkdir(parents=True)

    bed_lines = [f"chr1\t100\t200\tSNP_{i}" for i in range(200)]
    (anno_dir / "candidate_regions.bed").write_text("\n".join(bed_lines) + "\n", encoding="utf-8")
    (anno_dir / "candidate_regions.fa").write_text(">SNP_0::chr1:100-200\nACGTACGT\n", encoding="utf-8")
    (anno_dir / "kasp_primers_no_tail.fa").write_text(">SNP_0_X\nACGT\n", encoding="utf-8")
    (anno_dir / "extra.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (qc_dir / "plink_qc.bed").write_bytes(b"\x6c\x1b\x01\x00\xff")

    session = FakeSession(dataset, job=job)
    listed = jobs.list_job_files(job_id=job.id, db=session, user=owner)
    by_key = {item["file_key"]: item for item in listed["files"]}
    for key in (
        "job:annotation/candidate_regions.bed",
        "job:annotation/candidate_regions.fa",
        "job:annotation/kasp_primers_no_tail.fa",
        "job:annotation/extra.fasta",
    ):
        assert by_key[key]["previewable"] is True

    bed_resp = jobs.get_job_file_content(
        job_id=job.id,
        file_key="job:annotation/candidate_regions.bed",
        preview=True,
        db=session,
        user=owner,
    )
    assert bed_resp.status_code == 200
    preview_lines = bed_resp.body.decode("utf-8").splitlines()
    assert len(preview_lines) == 150
    assert preview_lines[0] == "chr1\t100\t200\tSNP_0"
    assert preview_lines[-1] == "chr1\t100\t200\tSNP_149"

    fa_resp = jobs.get_job_file_content(
        job_id=job.id,
        file_key="job:annotation/candidate_regions.fa",
        preview=True,
        db=session,
        user=owner,
    )
    assert fa_resp.status_code == 200
    assert ">SNP_0::chr1:100-200" in fa_resp.body.decode("utf-8")

    with pytest.raises(HTTPException) as bin_exc:
        jobs.get_job_file_content(
            job_id=job.id,
            file_key="job:qc/plink_qc.bed",
            preview=True,
            db=session,
            user=owner,
        )
    assert bin_exc.value.status_code == 400

    with pytest.raises(HTTPException) as denied_exc:
        jobs.get_job_file_content(
            job_id=job.id,
            file_key="job:annotation/candidate_regions.bed",
            preview=True,
            db=session,
            user=other_user,
        )
    assert denied_exc.value.status_code == 404
