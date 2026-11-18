import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks._common import JobCancelledError, ensure_not_cancelled, mark_job


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
