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
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus
from app.tasks._common import remove_job_storage, remove_tree_under_storage
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ASUMSI: tambahkan RETENTION_DAYS & RETENTION_HARD_DELETE ke config.py/.env
# kalau belum ada. Nilai getattr() di bawah cuma fallback dev.
RETENTION_DAYS = getattr(settings, "RETENTION_DAYS", 30)
HARD_DELETE_DB_ROW = getattr(settings, "RETENTION_HARD_DELETE", False)
ORPHAN_GRACE_SECONDS = 3600

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


def _dir_latest_mtime(path: Path) -> float:
    """Return latest mtime across a directory and its descendants."""
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _normalize_db_scheme(name: str) -> str:
    base = name.split(":", 1)[0].split("+", 1)[0].strip().lower()
    return "postgresql" if base == "postgres" else base


def _db_dialect_matches_config(db: Session) -> tuple[bool, str, str]:
    configured = _normalize_db_scheme(settings.DATABASE_URL or "")
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    actual = _normalize_db_scheme(getattr(getattr(bind, "dialect", None), "name", "") or "")
    if not configured or not actual:
        return True, configured, actual
    return configured == actual, configured, actual


def purge_orphan_storage_dirs(
    db: Session,
    *,
    orphan_grace_seconds: int = ORPHAN_GRACE_SECONDS,
) -> tuple[int, int]:
    """Sweep deleted job/dataset directories left on disk and stale unreferenced folders."""
    storage_root = Path(settings.STORAGE_ROOT)
    now_ts = time.time()
    purged_count = 0
    freed_bytes = 0
    dialect_ok, configured_scheme, actual_dialect = _db_dialect_matches_config(db)
    if not dialect_ok:
        logger.warning(
            "Sweep folder yatim dilewati: dialect DB aktif (%s) berbeda dengan DATABASE_URL (%s).",
            actual_dialect,
            configured_scheme,
        )

    for bucket_name, model in (("jobs", AnalysisJob), ("datasets", Dataset), ("staging", Dataset)):
        bucket_dir = storage_root / bucket_name
        if not bucket_dir.is_dir():
            continue
        entries = [entry for entry in bucket_dir.iterdir() if entry.is_dir()]
        if not entries:
            continue

        unreferenced: list[Path] = []
        for entry in entries:
            record = None
            raw_uid = (
                entry.name[:-4]
                if (bucket_name == "staging" and entry.name.endswith("_ref"))
                else entry.name
            )
            try:
                uid = uuid.UUID(raw_uid)
            except (ValueError, TypeError):
                uid = None
            if uid is not None:
                record = db.get(model, uid)
            if record is not None:
                if getattr(record, "deleted_at", None) is not None:
                    freed_bytes += remove_tree_under_storage(entry)
                    purged_count += 1
                continue
            unreferenced.append(entry)

        if not unreferenced or not dialect_ok:
            continue
        if db.query(model.id).first() is None:
            logger.warning(
                "Sweep folder yatim di '%s' dilewati: tabel %s kosong padahal terdapat %d folder.",
                bucket_name,
                model.__tablename__,
                len(entries),
            )
            continue
        if len(unreferenced) * 2 > len(entries):
            logger.warning(
                "Sweep folder yatim di '%s' dilewati: %d dari %d folder (>50%%) tampak yatim.",
                bucket_name,
                len(unreferenced),
                len(entries),
            )
            continue

        for entry in unreferenced:
            try:
                age_seconds = now_ts - _dir_latest_mtime(entry)
            except OSError:
                continue
            if age_seconds >= orphan_grace_seconds:
                freed_bytes += remove_tree_under_storage(entry)
                purged_count += 1

    return purged_count, freed_bytes


def _purge_job(db: Session, job: AnalysisJob) -> int:
    """Hapus seluruh direktori workspace job (staging DAN results — job sudah
    lewat retention window, jadi tidak ada lagi alasan mempertahankan apa pun)
    + (soft/hard) delete baris DB. Return byte yang dibebaskan."""
    freed = remove_job_storage(job.id, job.work_dir)

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

    staging_root = Path(settings.STORAGE_ROOT) / "staging"
    for staging_name in (str(dataset.id), f"{dataset.id}_ref"):
        staging_dir = staging_root / staging_name
        if staging_dir.is_dir():
            freed += remove_tree_under_storage(staging_dir)

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
    orphan_count = 0

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
                job.deleted_at is None and job.overall_status not in TERMINAL_JOB_STATUSES
                for job in dataset.analysis_jobs
            )
            if still_has_active_job:
                continue
            total_freed += _purge_dataset(db, dataset)
            dataset_count += 1
        db.commit()

        orphan_count, orphan_freed = purge_orphan_storage_dirs(db)
        total_freed += orphan_freed

    finally:
        db.close()

    logger.info(
        "Retention purge selesai: %d job, %d dataset, %d folder sisa dihapus, %.2f MB dibebaskan (cutoff=%s)",
        job_count, dataset_count, orphan_count, total_freed / (1024 * 1024), cutoff.isoformat(),
    )
    return {
        "jobs_purged": job_count,
        "datasets_purged": dataset_count,
        "orphans_purged": orphan_count,
        "freed_bytes": total_freed,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Setelah perubahan model ini, buat dan jalankan migrasi:
#
#     alembic revision --autogenerate -m "add deleted_at for retention soft delete"
#     alembic upgrade head
# ---------------------------------------------------------------------------
