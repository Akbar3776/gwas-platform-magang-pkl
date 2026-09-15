"""
tasks/retention_tasks.py

Data retention policy: hapus Dataset & AnalysisJob yang sudah lebih tua
dari RETENTION_DAYS hari (default 30), sekaligus direktori fisik
workspace-nya — mencegah disk penuh oleh file genomik raksasa yang sudah
tidak relevan lagi dipakai. Dijadwalkan via Celery Beat, lihat
celery_app.py (beat_schedule).

MODE:
    HARD_DELETE_DB_ROW = False (default, DISARANKAN untuk produksi)
        -> File fisik dihapus SEKARANG, baris DB dipertahankan sebagai
           audit trail dengan `deleted_at` terisi (soft delete). Berguna
           untuk histori "dataset X pernah diupload user Y, sudah di-purge
           tanggal Z" tanpa harus simpan file besarnya.

    HARD_DELETE_DB_ROW = True
        -> File fisik dihapus, baris DB ikut dihapus permanen. Cascade
           ditangani otomatis oleh relationship `cascade="all, delete-orphan"`
           yang SUDAH ADA di models.py:
               Dataset.analysis_jobs  -> cascade saat Dataset dihapus
               AnalysisJob.results    -> cascade saat AnalysisJob dihapus
           dan didukung juga di level DB lewat ForeignKey(..., ondelete="CASCADE").

Catatan: perubahan kolom `deleted_at` pada models.py tetap memerlukan migrasi
database sebelum task ini dijalankan terhadap database yang sudah ada.
"""

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ASUMSI: tambahkan RETENTION_DAYS & RETENTION_HARD_DELETE ke config.py/.env
# kalau belum ada. Nilai getattr() di bawah cuma fallback dev.
RETENTION_DAYS = getattr(settings, "RETENTION_DAYS", 30)
HARD_DELETE_DB_ROW = getattr(settings, "RETENTION_HARD_DELETE", False)

# Job yang masih berjalan tidak boleh ikut ter-purge walau created_at-nya
# sudah lewat 30 hari — itu tandanya job stuck/macet, bukan kandidat retensi normal.
TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.SUCCESS,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)


def _remove_path(path_str: str | None) -> int:
    """Hapus file ATAU direktori. Return estimasi byte yang dibebaskan."""
    if not path_str:
        return 0
    path = Path(path_str)
    if not path.exists():
        return 0
    try:
        if path.is_dir():
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path)
        else:
            size = path.stat().st_size
            path.unlink()
        return size
    except OSError as exc:
        logger.warning("Gagal menghapus %s: %s", path, exc)
        return 0


def _purge_job(db: Session, job: AnalysisJob) -> int:
    """Hapus seluruh direktori workspace job (staging DAN results — job sudah
    lewat retention window, jadi tidak ada lagi alasan mempertahankan apa pun)
    + (soft/hard) delete baris DB. Return byte yang dibebaskan."""
    freed = _remove_path(job.work_dir)

    if HARD_DELETE_DB_ROW:
        db.delete(job)  # cascade ke `results` otomatis lewat relationship
    else:
        job.deleted_at = datetime.now(timezone.utc)
        job.work_dir = None
        db.add(job)

    return freed


def _purge_dataset(db: Session, dataset: Dataset) -> int:
    """Hapus folder raw VCF dataset + (soft/hard) delete baris DB."""
    freed = 0
    if dataset.storage_path:
        # Hapus seluruh folder induk (biasanya .../{dataset_id}/raw/), bukan
        # cuma file VCF-nya, untuk menyapu file pendamping (.tbi, dsb) juga.
        freed += _remove_path(str(Path(dataset.storage_path).parent))

    if HARD_DELETE_DB_ROW:
        db.delete(dataset)  # cascade ke `analysis_jobs` -> `results` otomatis
    else:
        dataset.deleted_at = datetime.now(timezone.utc)
        dataset.storage_path = ""
        db.add(dataset)

    return freed


@celery_app.task(bind=True, name="maintenance.purge_expired_records", acks_late=True)
def purge_expired_records(self):
    """
    Entry point yang dipanggil Celery Beat setiap tengah malam (lihat
    beat_schedule di celery_app.py).

    Urutan SENGAJA: purge AnalysisJob dulu, baru Dataset — supaya dataset
    yang job-nya justru baru selesai dalam window retensi tidak ikut
    kehapus lebih dulu (dataset hanya di-purge kalau SEMUA job miliknya
    juga sudah dalam status terminal & di luar window retensi).
    """
    db = SessionLocal()
    cutoff = _cutoff()
    total_freed = 0
    job_count = 0
    dataset_count = 0

    try:
        job_filters = [
            AnalysisJob.overall_status.in_(TERMINAL_JOB_STATUSES),
            AnalysisJob.completed_at.isnot(None),
            AnalysisJob.completed_at < cutoff,
        ]
        if not HARD_DELETE_DB_ROW:
            job_filters.append(AnalysisJob.deleted_at.is_(None))

        expired_jobs = db.query(AnalysisJob).filter(*job_filters).all()
        for job in expired_jobs:
            total_freed += _purge_job(db, job)
            job_count += 1
        db.commit()

        dataset_filters = [Dataset.uploaded_at < cutoff]
        if not HARD_DELETE_DB_ROW:
            dataset_filters.append(Dataset.deleted_at.is_(None))

        expired_datasets = db.query(Dataset).filter(*dataset_filters).all()
        for dataset in expired_datasets:
            still_has_active_job = any(
                job.overall_status not in TERMINAL_JOB_STATUSES
                for job in dataset.analysis_jobs
            )
            if still_has_active_job:
                continue
            total_freed += _purge_dataset(db, dataset)
            dataset_count += 1
        db.commit()

    finally:
        db.close()

    logger.info(
        "Retention purge selesai: %d job, %d dataset dihapus, %.2f MB dibebaskan (cutoff=%s)",
        job_count, dataset_count, total_freed / (1024 * 1024), cutoff.isoformat(),
    )
    return {
        "jobs_purged": job_count,
        "datasets_purged": dataset_count,
        "freed_bytes": total_freed,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Setelah perubahan model ini, buat dan jalankan migrasi:
#
#     alembic revision --autogenerate -m "add deleted_at for retention soft delete"
#     alembic upgrade head
# ---------------------------------------------------------------------------
