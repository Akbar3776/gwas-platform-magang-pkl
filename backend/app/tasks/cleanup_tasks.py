"""
tasks/cleanup_tasks.py

Task terakhir dalam pipeline chain (lihat pipeline_orchestrator.py).

Mengikuti pola `JobAwareTask` dari pipeline_tasks.py untuk konsistensi
(time_limit/soft_time_limit, registrasi lewat `celery_app.task(base=...)`,
naming "pipeline.*") -- TAPI dengan satu perbedaan yang SENGAJA:

    Task ini TIDAK PERNAH membiarkan exception propagate ke Celery.

Alasan: `task_parse_results` di pipeline_tasks.py adalah SATU-SATUNYA
tempat job ditandai SUCCESS (lihat docstringnya). Kalau cleanup --
housekeeping, bukan bagian inti analisis -- gagal dan exception-nya
dibiarkan naik, `JobAwareTask.on_failure` akan menimpa status job itu jadi
FAILED, padahal hasil analisisnya sendiri valid dan sudah tersimpan di
`assoc_output_path` / `manhattan_cache_path`. Jadi semua exception di sini
SENGAJA ditangkap & di-log saja; kegagalan cleanup paling buruk menyisakan
file staging yang nanti tetap akan disapu oleh `retention_tasks.py`
(`purge_expired_records`) setelah melewati window retensi.
"""

import logging

from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob
from app.services.storage_cleanup_service import cleanup_intermediate_files
from app.tasks.celery_app import celery_app
from app.tasks.pipeline_tasks import JobAwareTask

logger = logging.getLogger(__name__)


@celery_app.task(base=JobAwareTask, bind=True, name="pipeline.cleanup_job_intermediate_files")
def cleanup_job_intermediate_files(self, previous_result, job_id: str):
    """
    `previous_result` adalah dict yang dikembalikan `task_parse_results`
    (job_id, manhattan_cache_path, dst.) -- diterima otomatis lewat
    mekanisme `celery.chain`; isinya tidak dipakai di sini, cuma
    ditampung supaya chaining tidak error.
    """
    db = SessionLocal()
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None:
            logger.warning("cleanup_job_intermediate_files: job %s tidak ditemukan", job_id)
            return {"skipped": True, "reason": "job_not_found"}
        return cleanup_intermediate_files(db, job)
    except Exception as exc:  # noqa: BLE001 -- sengaja tidak di-raise ulang, lihat docstring modul
        logger.error("Cleanup job %s gagal: %s", job_id, exc)
        return {"skipped": True, "reason": str(exc)}
    finally:
        db.close()
