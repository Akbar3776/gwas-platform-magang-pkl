import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import _common, pipeline_orchestrator
from app.tasks._common import JobCancelledError, ensure_not_cancelled, is_job_cancelled, mark_job


def _setup_sqlite_job(tmp_path, initial_status: JobStatus):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/tmp/cohort.vcf.gz",
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.GWAS,
        overall_status=initial_status,
        parameters={},
    )
    db.add_all([user, dataset, job])
    db.commit()
    return engine, Session, db, job


def test_mark_job_raises_when_job_already_cancelled_and_preserves_status(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.CANCELLED)
    try:
        with pytest.raises(JobCancelledError):
            mark_job(db, job, overall_status=JobStatus.RUNNING_STRUCTURE)

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.CANCELLED
        finally:
            verify_db.close()
    finally:
        db.close()


def test_mark_job_does_not_overwrite_cancelled_with_failed(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.CANCELLED)
    try:
        mark_job(db, job, overall_status=JobStatus.FAILED, error_message="x")

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.CANCELLED
            assert refreshed.error_message == "x"
        finally:
            verify_db.close()
    finally:
        db.close()


def test_mark_job_updates_status_normally_when_not_cancelled(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_QC)
    try:
        mark_job(db, job, overall_status=JobStatus.RUNNING_IMPUTATION)

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.RUNNING_IMPUTATION
        finally:
            verify_db.close()
    finally:
        db.close()


def test_ensure_not_cancelled_sees_commit_from_other_session(tmp_path):
    _, Session, session1, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_GWAS)
    try:
        loaded_in_s1 = session1.get(AnalysisJob, job.id)
        assert loaded_in_s1.overall_status == JobStatus.RUNNING_GWAS

        session2 = Session()
        try:
            job_in_s2 = session2.get(AnalysisJob, job.id)
            job_in_s2.overall_status = JobStatus.CANCELLED
            session2.commit()
        finally:
            session2.close()

        with pytest.raises(JobCancelledError):
            ensure_not_cancelled(str(job.id), session1)
    finally:
        session1.close()


def test_ensure_not_cancelled_rejects_stale_run_id(tmp_path):
    _, _, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_IMPUTATION)
    try:
        job.parameters = {"run_id": "B"}
        db.commit()

        ensure_not_cancelled(str(job.id), db, run_id="B")
        ensure_not_cancelled(str(job.id), db, run_id=None)

        with pytest.raises(JobCancelledError):
            ensure_not_cancelled(str(job.id), db, run_id="A")
    finally:
        db.close()


def test_is_job_cancelled_detects_stale_run_id(tmp_path, monkeypatch):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_IMPUTATION)
    job_id = str(job.id)
    try:
        job.parameters = {"run_id": "B"}
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(_common, "SessionLocal", Session)
    assert is_job_cancelled(job_id, run_id="A") is True
    assert is_job_cancelled(job_id, run_id="B") is False


def test_retry_job_updates_run_id_and_passes_to_orchestrator(monkeypatch):
    from app.api.routes import jobs

    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/tmp/cohort.vcf.gz",
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.IMPUTATION,
        overall_status=JobStatus.CANCELLED,
        parameters={"run_id": "old-run-id"},
    )

    class _FakeDB:
        def __init__(self):
            self.committed_run_ids = []

        def get(self, model, object_id):
            if model is AnalysisJob and str(object_id) == str(job.id):
                return job
            if model is Dataset and str(object_id) == str(dataset.id):
                return dataset
            return None

        def commit(self):
            self.committed_run_ids.append((job.parameters or {}).get("run_id"))

    captured: dict[str, object] = {}

    def _fake_retry(**kwargs):
        captured["kwargs"] = kwargs
        captured["run_id_committed_before_trigger"] = list(fake_db.committed_run_ids)
        return "retry-celery-id", 3, "imputation"

    fake_db = _FakeDB()
    monkeypatch.setattr(pipeline_orchestrator, "trigger_retry_pipeline", _fake_retry)

    jobs.retry_job(job_id=job.id, from_scratch=False, db=fake_db, user=user)

    new_run_id = job.parameters["run_id"]
    assert new_run_id != "old-run-id"
    assert captured["kwargs"]["run_id"] == new_run_id
    assert new_run_id in captured["run_id_committed_before_trigger"]


def test_build_stage_signatures_attaches_run_id_to_all_stages():
    sigs = pipeline_orchestrator._build_stage_signatures(
        job_id=str(uuid.uuid4()),
        dataset_id=str(uuid.uuid4()),
        run_id="R",
    )
    assert len(sigs) == 10
    for sig in sigs:
        assert sig.kwargs["run_id"] == "R"
