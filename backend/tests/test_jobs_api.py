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
    assert task_call["args"][1] == body["job_id"]


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
        raw_vcf_path="",
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
