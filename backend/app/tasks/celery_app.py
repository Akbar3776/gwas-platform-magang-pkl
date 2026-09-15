"""
Instance Celery app terpusat.

Semua task (upload assembly, normalisasi, QC, imputasi, GWAS) didaftarkan
lewat parameter `include` di sini, agar worker (`celery -A app.tasks.celery_app worker`)
otomatis menemukan seluruh task tanpa import manual di tempat lain.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "gwas_pipeline",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.upload_tasks",
        "app.tasks.normalization_tasks",
        "app.tasks.qc_tasks",
        "app.tasks.imputation_tasks",
        "app.tasks.gemma_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.retention_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # acks_late + reject_on_worker_lost: task yang belum selesai saat worker
    # crash akan otomatis di-requeue ke worker lain, bukan hilang begitu saja.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "purge-expired-datasets-and-jobs": {
        "task": "maintenance.purge_expired_records",  # <-- nama task, BUKAN "app.tasks.retention_tasks.purge_expired_records"
        "schedule": crontab(hour=0, minute=0),
    },
}