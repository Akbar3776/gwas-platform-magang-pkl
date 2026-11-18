import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import jobs as jobs_routes
from app.core.config import settings
from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import _common, celery_app as celery_mod, retention_tasks
from app.tasks._common import JobCancelledError, ensure_not_cancelled, guard_status_change, is_job_cancelled


def _make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'storage_cleanup.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_job(db, *, status=JobStatus.RUNNING_QC, deleted_at=None, completed_at=None, work_dir=None):
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
        storage_path="",
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.QC,
        overall_status=status,
        parameters={},
        deleted_at=deleted_at,
        completed_at=completed_at,
        work_dir=work_dir,
    )
    db.add_all([user, dataset, job])
    db.commit()
    return user, dataset, job


def test_delete_job_commits_before_revoke_and_removes_job_dir_safely(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "keep.txt"
    outside_file.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    db = Session()
    try:
        user, _, job = _seed_job(db, status=JobStatus.RUNNING_QC, work_dir=str(outside_dir))
        job.celery_task_id = "task-123"
        db.commit()

        job_root = storage_root / "jobs" / str(job.id)
        (job_root / "qc").mkdir(parents=True)
        (job_root / "qc" / "plink_qc.log").write_text("running", encoding="utf-8")

        events = []

        class _FakeControl:
            def revoke(self, task_id, terminate=False):
                check_db = Session()
                try:
                    persisted = check_db.get(AnalysisJob, job.id)
                    events.append(("revoke", persisted.deleted_at is not None, persisted.overall_status))
                finally:
                    check_db.close()

        monkeypatch.setattr(jobs_routes.celery_app, "control", _FakeControl(), raising=False)
        resp = jobs_routes.delete_job(job_id=job.id, db=db, user=user)

        assert resp["deleted"] is True
        assert not job_root.exists()
        assert outside_file.exists()
        assert events == [("revoke", True, JobStatus.CANCELLED)]
    finally:
        db.close()


def test_ensure_not_cancelled_rejects_deleted_job_and_cleans_storage(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    db = Session()
    try:
        _, _, job = _seed_job(
            db,
            status=JobStatus.RUNNING_GWAS,
            deleted_at=datetime.now(timezone.utc),
        )
        job_root = storage_root / "jobs" / str(job.id)
        (job_root / "qc").mkdir(parents=True)
        (job_root / "qc" / "plink_qc.log").write_text("late write", encoding="utf-8")

        with pytest.raises(JobCancelledError):
            ensure_not_cancelled(str(job.id), db)

        assert not job_root.exists()
    finally:
        db.close()


def test_is_job_cancelled_and_guard_status_change_detect_deleted_job(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    db = Session()
    try:
        _, _, job = _seed_job(
            db,
            status=JobStatus.RUNNING_QC,
            deleted_at=datetime.now(timezone.utc),
        )
        job_id = str(job.id)
        job_root = storage_root / "jobs" / job_id
        (job_root / "qc").mkdir(parents=True)
        (job_root / "qc" / "late.log").write_text("x", encoding="utf-8")
    finally:
        db.close()

    monkeypatch.setattr(_common, "SessionLocal", Session)
    assert is_job_cancelled(job_id) is True

    db2 = Session()
    try:
        assert guard_status_change(db2, job_id, JobStatus.FAILED) is False
        assert not job_root.exists()
        with pytest.raises(JobCancelledError):
            guard_status_change(db2, job_id, JobStatus.RUNNING_STRUCTURE)
    finally:
        db2.close()


def test_task_postrun_cleans_deleted_job_directory_after_cancellation(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    monkeypatch.setattr(_common, "SessionLocal", Session)

    db = Session()
    try:
        _, _, job = _seed_job(db, status=JobStatus.RUNNING_QC)
        job_id = str(job.id)
    finally:
        db.close()

    @celery_mod.celery_app.task(bind=True, name="tests.dummy_deleted_job_task")
    def _dummy_task(self, job_id: str):
        qc_dir = storage_root / "jobs" / job_id / "qc"
        qc_dir.mkdir(parents=True, exist_ok=True)
        (qc_dir / "plink_qc.log").write_text("locked during run", encoding="utf-8")

        s = Session()
        try:
            persisted = s.get(AnalysisJob, uuid.UUID(job_id))
            persisted.deleted_at = datetime.now(timezone.utc)
            persisted.overall_status = JobStatus.CANCELLED
            s.commit()
        finally:
            s.close()
        raise JobCancelledError("cancelled by delete")

    with pytest.raises(JobCancelledError):
        _dummy_task(job_id=job_id)

    assert not (storage_root / "jobs" / job_id).exists()


def test_purge_expired_records_removes_full_job_root_soft_deleted_and_old_orphans(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    monkeypatch.setattr(retention_tasks, "SessionLocal", Session)
    monkeypatch.setattr(retention_tasks, "HARD_DELETE_DB_ROW", False)

    old_time = datetime.now(timezone.utc) - timedelta(days=45)
    db = Session()
    try:
        _, ds_expired, job_expired = _seed_job(
            db,
            status=JobStatus.COMPLETED,
            completed_at=old_time,
        )
        ds_expired.uploaded_at = old_time
        expired_root = storage_root / "jobs" / str(job_expired.id)
        (expired_root / "annotation").mkdir(parents=True)
        (expired_root / "qc").mkdir(parents=True)
        (expired_root / "annotation" / "ann.log").write_text("ann", encoding="utf-8")
        (expired_root / "qc" / "plink.bed").write_text("bed", encoding="utf-8")
        job_expired.work_dir = str(expired_root / "annotation")

        _, _, job_soft_deleted = _seed_job(
            db,
            status=JobStatus.CANCELLED,
            deleted_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        soft_deleted_root = storage_root / "jobs" / str(job_soft_deleted.id)
        (soft_deleted_root / "qc").mkdir(parents=True)
        (soft_deleted_root / "qc" / "leftover.log").write_text("leftover", encoding="utf-8")

        _, _, job_active = _seed_job(db, status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        active_root = storage_root / "jobs" / str(job_active.id)
        (active_root / "gwas").mkdir(parents=True)
        (active_root / "gwas" / "assoc.txt").write_text("ok", encoding="utf-8")
        db.commit()
    finally:
        db.close()

    old_orphan = storage_root / "jobs" / "test-job-old-orphan"
    (old_orphan / "gwas").mkdir(parents=True)
    old_file = old_orphan / "gwas" / "gemma.log"
    old_file.write_text("stale", encoding="utf-8")
    stale_ts = time.time() - 7200
    os.utime(old_file, (stale_ts, stale_ts))
    os.utime(old_orphan / "gwas", (stale_ts, stale_ts))
    os.utime(old_orphan, (stale_ts, stale_ts))

    fresh_orphan = storage_root / "jobs" / "fresh-tmp-dir"
    fresh_orphan.mkdir(parents=True)
    (fresh_orphan / "tmp.log").write_text("new", encoding="utf-8")

    result = retention_tasks.purge_expired_records()

    assert result["jobs_purged"] >= 1
    assert not expired_root.exists()
    assert not soft_deleted_root.exists()
    assert not old_orphan.exists()
    assert active_root.exists()
    assert fresh_orphan.exists()
