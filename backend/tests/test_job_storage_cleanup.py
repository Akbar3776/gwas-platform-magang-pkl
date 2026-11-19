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
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'storage_cleanup.db'}")
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


def _make_stale_orphan(bucket_dir, name: str):
    orphan = bucket_dir / name
    (orphan / "qc").mkdir(parents=True)
    log_file = orphan / "qc" / "plink.log"
    log_file.write_text("stale", encoding="utf-8")
    stale_ts = time.time() - 7200
    os.utime(log_file, (stale_ts, stale_ts))
    os.utime(orphan / "qc", (stale_ts, stale_ts))
    os.utime(orphan, (stale_ts, stale_ts))
    return orphan


def test_orphan_sweep_skips_when_table_empty(tmp_path, monkeypatch, caplog):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    db_path = tmp_path / "storage_cleanup.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    Session = _make_session(tmp_path)

    jobs_dir = storage_root / "jobs"
    orphans = [_make_stale_orphan(jobs_dir, str(uuid.uuid4())) for _ in range(3)]

    db = Session()
    try:
        with caplog.at_level("WARNING"):
            purged, freed = retention_tasks.purge_orphan_storage_dirs(db)
    finally:
        db.close()

    assert purged == 0
    assert freed == 0
    assert all(p.exists() for p in orphans)
    assert any("WARNING" == rec.levelname for rec in caplog.records)


def test_orphan_sweep_skips_on_dialect_mismatch(tmp_path, monkeypatch, caplog):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg2://gwas_user:pw@db:5432/gwas_db")
    Session = _make_session(tmp_path)

    db = Session()
    try:
        for _ in range(10):
            _, _, job = _seed_job(db, status=JobStatus.COMPLETED)
            d = storage_root / "jobs" / str(job.id)
            d.mkdir(parents=True)
            (d / "ok.txt").write_text("1", encoding="utf-8")
        orphan = _make_stale_orphan(storage_root / "jobs", "mismatch-orphan")
        with caplog.at_level("WARNING"):
            purged, freed = retention_tasks.purge_orphan_storage_dirs(db)
    finally:
        db.close()

    assert purged == 0
    assert freed == 0
    assert orphan.exists()
    assert any("WARNING" == rec.levelname for rec in caplog.records)


def test_orphan_sweep_skips_when_more_than_half_unreferenced(tmp_path, monkeypatch, caplog):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    db_path = tmp_path / "storage_cleanup.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    Session = _make_session(tmp_path)

    db = Session()
    try:
        active_dirs = []
        for _ in range(2):
            _, _, job = _seed_job(db, status=JobStatus.COMPLETED)
            d = storage_root / "jobs" / str(job.id)
            d.mkdir(parents=True)
            (d / "ok.txt").write_text("1", encoding="utf-8")
            active_dirs.append(d)
        orphans = [_make_stale_orphan(storage_root / "jobs", f"orphan-{i}") for i in range(3)]
        with caplog.at_level("WARNING"):
            purged, _ = retention_tasks.purge_orphan_storage_dirs(db)
    finally:
        db.close()

    assert purged == 0
    assert all(p.exists() for p in orphans)
    assert all(p.exists() for p in active_dirs)
    assert any("WARNING" == rec.levelname for rec in caplog.records)


def test_orphan_sweep_removes_single_stale_orphan_with_10_valid_jobs(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    db_path = tmp_path / "storage_cleanup.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    Session = _make_session(tmp_path)

    db = Session()
    try:
        active_dirs = []
        for _ in range(10):
            _, _, job = _seed_job(db, status=JobStatus.COMPLETED)
            d = storage_root / "jobs" / str(job.id)
            d.mkdir(parents=True)
            (d / "ok.txt").write_text("1", encoding="utf-8")
            active_dirs.append(d)
        orphan = _make_stale_orphan(storage_root / "jobs", "single-old-orphan")
        purged, freed = retention_tasks.purge_orphan_storage_dirs(db)
    finally:
        db.close()

    assert purged == 1
    assert freed > 0
    assert not orphan.exists()
    assert all(p.exists() for p in active_dirs)


def test_deleted_dataset_and_job_routes_return_404(tmp_path, monkeypatch):
    import io
    from fastapi import HTTPException, UploadFile
    import pytest
    from app.api.routes.jobs import (
        get_job_file_content,
        get_job_logs,
        get_job_results_summary,
        get_job_status,
        list_job_files,
        trigger_qc_job,
    )
    from app.api.routes.upload import (
        download_uploaded_dataset,
        finalize_upload,
        get_upload_status,
        upload_chunk,
    )
    from app.db.engine.models import UploadStatus, User, UserRole
    from app.schemas.jobs import QCJobRequest

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    db = Session()
    try:
        owner = User(
            id=uuid.uuid4(),
            email="a10@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        db.add(owner)
        db.flush()
        user_obj, ds, job = _seed_job(
            db,
            status=JobStatus.FAILED,
            deleted_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        ds.owner_id = owner.id
        ds.upload_status = UploadStatus.UPLOADING
        ds.deleted_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        job.triggered_by = owner.id
        db.commit()

        chunk = UploadFile(filename="x.vcf", file=io.BytesIO(b"##fileformat=VCFv4.2\n"))
        with pytest.raises(HTTPException) as exc1:
            upload_chunk(file_id=ds.id, chunk_index=0, total_chunks=1, chunk=chunk, db=db, user=owner)
        assert exc1.value.status_code == 404

        with pytest.raises(HTTPException) as exc2:
            finalize_upload(file_id=ds.id, total_chunks=1, expected_sha256=None, db=db, user=owner)
        assert exc2.value.status_code == 404

        with pytest.raises(HTTPException) as exc3:
            get_upload_status(file_id=ds.id, db=db, user=owner)
        assert exc3.value.status_code == 404

        with pytest.raises(HTTPException) as exc4:
            download_uploaded_dataset(file_id=ds.id, preview=False, db=db, user=owner)
        assert exc4.value.status_code == 404

        with pytest.raises(HTTPException) as exc5:
            trigger_qc_job(payload=QCJobRequest(dataset_id=ds.id), db=db, user=owner)
        assert exc5.value.status_code == 404

        for fn in (
            lambda: get_job_status(job_id=job.id, db=db, user=owner),
            lambda: get_job_logs(job_id=job.id, db=db, user=owner),
            lambda: list_job_files(job_id=job.id, db=db, user=owner),
            lambda: get_job_file_content(job_id=job.id, file_key="dataset_input", preview=False, db=db, user=owner),
            lambda: get_job_results_summary(job_id=job.id, db=db, user=owner),
        ):
            with pytest.raises(HTTPException) as exc:
                fn()
            assert exc.value.status_code == 404
    finally:
        db.close()


def test_staging_sweep_and_purge_dataset_remove_staging_dirs(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    db_path = tmp_path / "storage_cleanup.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    Session = _make_session(tmp_path)
    monkeypatch.setattr(retention_tasks, "SessionLocal", Session)

    old_time = datetime.now(timezone.utc) - timedelta(days=retention_tasks.RETENTION_DAYS + 5)
    db = Session()
    try:
        _, ds_expired, job_expired = _seed_job(db, status=JobStatus.COMPLETED, completed_at=old_time)
        ds_expired.uploaded_at = old_time
        expired_staging = storage_root / "staging" / str(ds_expired.id)
        expired_staging.mkdir(parents=True)
        (expired_staging / "chunk_000000.part").write_text("expired", encoding="utf-8")

        _, ds_soft_deleted, _ = _seed_job(
            db,
            status=JobStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
            deleted_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        ds_soft_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        deleted_staging = storage_root / "staging" / str(ds_soft_deleted.id)
        deleted_staging.mkdir(parents=True)
        part = deleted_staging / "chunk_000000.part"
        part.write_text("deleted", encoding="utf-8")
        stale_ts = time.time() - 7200
        os.utime(part, (stale_ts, stale_ts))
        os.utime(deleted_staging, (stale_ts, stale_ts))

        _, ds_active, _ = _seed_job(db, status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        active_staging = storage_root / "staging" / str(ds_active.id)
        active_staging.mkdir(parents=True)
        (active_staging / "chunk_000000.part").write_text("active", encoding="utf-8")
        db.commit()
    finally:
        db.close()

    res = retention_tasks.purge_expired_records()
    assert res["datasets_purged"] >= 1
    assert not expired_staging.exists()
    assert not deleted_staging.exists()
    assert active_staging.exists()


def test_deleted_or_unauthorized_export_report_and_pipeline_return_404(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.api.routes.export import export_job_results
    from app.api.routes.pipeline import start_pipeline_endpoint
    from app.api.routes.report import download_summary_report
    from app.db.engine.models import UploadStatus, User, UserRole
    from app.schemas.pipeline import FullPipelineRequest

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    Session = _make_session(tmp_path)
    db = Session()
    try:
        owner = User(
            id=uuid.uuid4(),
            email="owner-a60@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        other_user = User(
            id=uuid.uuid4(),
            email="other-a60@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        db.add_all([owner, other_user])
        db.flush()

        assoc_file = tmp_path / "gwas.assoc.txt"
        assoc_file.write_text(
            "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tp_wald\n"
            "1\trs1\t100\t0\tA\tG\t0.25\t0.5\t0.1\t1e-8\n",
            encoding="utf-8",
        )
        pheno_file = tmp_path / "pheno.txt"
        pheno_file.write_text("IID\tTrait1\ns1\t1.0\n", encoding="utf-8")

        _, ds_deleted, job_deleted = _seed_job(
            db,
            status=JobStatus.COMPLETED,
            deleted_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        ds_deleted.owner_id = owner.id
        ds_deleted.upload_status = UploadStatus.UPLOADED
        ds_deleted.storage_path = str(tmp_path / "missing_or_deleted.vcf.gz")
        ds_deleted.phenotype_file_path = str(pheno_file)
        ds_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        job_deleted.triggered_by = owner.id
        job_deleted.assoc_output_path = str(assoc_file)
        job_deleted.completed_at = datetime.now(timezone.utc)

        _, ds_active, job_active = _seed_job(
            db,
            status=JobStatus.COMPLETED,
        )
        ds_active.owner_id = owner.id
        job_active.triggered_by = owner.id
        job_active.assoc_output_path = str(assoc_file)
        job_active.completed_at = datetime.now(timezone.utc)
        db.commit()

        with pytest.raises(HTTPException) as exc_export_del:
            export_job_results(job_id=job_deleted.id, db=db, user=owner)
        assert exc_export_del.value.status_code == 404
        assert "Job tidak ditemukan" in exc_export_del.value.detail

        with pytest.raises(HTTPException) as exc_report_del:
            download_summary_report(job_id=job_deleted.id, db=db, user=owner)
        assert exc_report_del.value.status_code == 404
        assert "Job tidak ditemukan" in exc_report_del.value.detail

        with pytest.raises(HTTPException) as exc_export_other:
            export_job_results(job_id=job_active.id, db=db, user=other_user)
        assert exc_export_other.value.status_code == 404
        assert "Job tidak ditemukan" in exc_export_other.value.detail

        with pytest.raises(HTTPException) as exc_report_other:
            download_summary_report(job_id=job_active.id, db=db, user=other_user)
        assert exc_report_other.value.status_code == 404
        assert "Job tidak ditemukan" in exc_report_other.value.detail

        with pytest.raises(HTTPException) as exc_pipe_del:
            start_pipeline_endpoint(
                dataset_id=ds_deleted.id,
                payload=FullPipelineRequest(),
                db=db,
                user=owner,
            )
        assert exc_pipe_del.value.status_code == 404
        assert "Dataset tidak ditemukan" in exc_pipe_del.value.detail
    finally:
        db.close()


def test_delete_and_purge_dataset_remove_ref_staging_dir(tmp_path, monkeypatch):
    from app.api.routes.upload import delete_uploaded_dataset
    from app.db.engine.models import UploadStatus, User, UserRole

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))
    db_path = tmp_path / "storage_cleanup.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")
    Session = _make_session(tmp_path)
    monkeypatch.setattr(retention_tasks, "SessionLocal", Session)

    old_time = datetime.now(timezone.utc) - timedelta(days=retention_tasks.RETENTION_DAYS + 5)
    db = Session()
    try:
        owner = User(
            id=uuid.uuid4(),
            email="owner-ref@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        db.add(owner)
        db.flush()

        # 1. Dataset deleted explicitly via upload route -> both staging/<id> and staging/<id>_ref removed
        ds_route = Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="route.vcf",
            storage_path="",
            upload_status=UploadStatus.UPLOADED,
        )
        # 2. Dataset expired via retention _purge_dataset -> both staging/<id> and staging/<id>_ref removed
        ds_purge = Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="purge.vcf",
            storage_path="",
            upload_status=UploadStatus.UPLOADED,
            uploaded_at=old_time,
        )
        # 3. Soft-deleted dataset with lingering staging/<id>_ref swept by purge_orphan_storage_dirs
        ds_soft = Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="soft.vcf",
            storage_path="",
            upload_status=UploadStatus.UPLOADED,
            deleted_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        # 4. Active dataset -> staging/<id>_ref MUST stay untouched
        ds_active = Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="active.vcf",
            storage_path="",
            upload_status=UploadStatus.UPLOADED,
        )
        db.add_all([ds_route, ds_purge, ds_soft, ds_active])
        db.commit()

        staging_dirs = {}
        for key, ds_obj in (("route", ds_route), ("purge", ds_purge), ("soft", ds_soft), ("active", ds_active)):
            d = storage_root / "staging" / str(ds_obj.id)
            d_ref = storage_root / "staging" / f"{ds_obj.id}_ref"
            d.mkdir(parents=True)
            d_ref.mkdir(parents=True)
            (d / "chunk_000000.part").write_text("x", encoding="utf-8")
            (d_ref / "chunk_000000.part").write_text("ref", encoding="utf-8")
            staging_dirs[key] = (d, d_ref)

        delete_uploaded_dataset(file_id=ds_route.id, db=db, user=owner)
        assert not staging_dirs["route"][0].exists()
        assert not staging_dirs["route"][1].exists()
    finally:
        db.close()

    res = retention_tasks.purge_expired_records()
    assert res["datasets_purged"] >= 1
    assert not staging_dirs["purge"][0].exists()
    assert not staging_dirs["purge"][1].exists()
    assert not staging_dirs["soft"][0].exists()
    assert not staging_dirs["soft"][1].exists()
    assert staging_dirs["active"][0].exists()
    assert staging_dirs["active"][1].exists()


def test_trigger_qc_job_passes_job_id_kwarg_and_single_cleanup_invocation(tmp_path, monkeypatch):
    from app.db.engine.models import UploadStatus, User, UserRole
    from app.schemas.jobs import QCJobRequest

    Session = _make_session(tmp_path)
    db = Session()
    try:
        owner = User(
            id=uuid.uuid4(),
            email="qc-a42@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        ds = Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="qc.vcf.gz",
            storage_path=str(tmp_path / "qc.vcf.gz"),
            upload_status=UploadStatus.UPLOADED,
        )
        db.add_all([owner, ds])
        db.commit()

        captured = {}

        class _FakeAsync:
            id = "qc-task-a42"

        def fake_delay(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _FakeAsync()

        monkeypatch.setattr(jobs_routes.run_qc_pipeline, "delay", fake_delay)
        resp = jobs_routes.trigger_qc_job(
            payload=QCJobRequest(dataset_id=ds.id),
            db=db,
            user=owner,
        )
        assert captured["kwargs"].get("job_id") == str(resp.job_id)
    finally:
        db.close()

    # Verify cleanup_if_job_deleted executes at most once per task invocation
    calls = []
    monkeypatch.setattr(_common, "cleanup_if_job_deleted", lambda jid, **kw: calls.append(jid) or 0)

    @celery_mod.celery_app.task(bind=True, name="tests.a42_single_cleanup")
    def _a42_task(self, *args, job_id=None, **kwargs):
        return "ok"

    _a42_task(None, job_id="job-a42-once")
    assert calls == ["job-a42-once"]
