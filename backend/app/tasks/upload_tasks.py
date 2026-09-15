"""
Celery task untuk proses upload: merakit chunk menjadi file final dan
memperbarui record `Dataset` di database.

Dijalankan sebagai task terpisah (bukan inline di route FastAPI) sesuai
Development Rule #1 — merakit file berukuran GB butuh waktu, dan tidak
boleh memblokir request HTTP / menyebabkan timeout.

Catatan: task Celery tidak punya akses ke dependency FastAPI (`Depends(get_db)`),
sehingga session database dibuat manual dari `SessionLocal` dan WAJIB ditutup
di blok `finally`.
"""

from celery.utils.log import get_task_logger

from app.db.engine.database import SessionLocal
from app.db.engine.models import Dataset, UploadStatus
from app.services import upload_service
from app.tasks.celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.upload_tasks.assemble_dataset_task",
    max_retries=1,
)
def assemble_dataset_task(self, dataset_id: str, total_chunks: int, expected_sha256: str | None = None) -> dict:
    """
    Merakit seluruh chunk milik `dataset_id` menjadi satu file VCF utuh,
    lalu memperbarui `Dataset.upload_status`, `storage_path`,
    `file_size_bytes`, dan `checksum_sha256`.

    Alur status:
        UPLOADING -> (assembly berjalan) -> UPLOADED (sukses)
                                          -> FAILED   (jika ada error)
    """
    db = SessionLocal()
    try:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None:
            logger.error("Dataset %s tidak ditemukan, membatalkan assembly.", dataset_id)
            return {"status": "error", "reason": "dataset_not_found"}

        try:
            final_path, total_size = upload_service.assemble_file(
                file_id=str(dataset.id),
                total_chunks=total_chunks,
                original_filename=dataset.original_filename,
            )

            checksum = upload_service.compute_sha256(final_path)
            if expected_sha256 and checksum.lower() != expected_sha256.lower():
                final_path.unlink(missing_ok=True)
                raise ValueError(
                    f"Checksum tidak cocok: expected={expected_sha256}, actual={checksum}"
                )

            # Ini adalah status "completed" yang dimaksud pada requirement —
            # model `Dataset` sudah mendefinisikan nilai ini sebagai UPLOADED
            # (lihat app/db/models.py), menandakan file fisik sudah utuh dan
            # tersimpan permanen, siap masuk tahap validasi/normalisasi berikutnya.
            dataset.storage_path = str(final_path)
            dataset.file_size_bytes = total_size
            dataset.checksum_sha256 = checksum
            dataset.upload_status = UploadStatus.UPLOADED
            db.commit()

            logger.info(
                "Dataset %s berhasil dirakit: %s (%d bytes)",
                dataset_id, final_path, total_size,
            )
            return {
                "status": "success",
                "dataset_id": str(dataset.id),
                "storage_path": str(final_path),
                "file_size_bytes": total_size,
                "checksum_sha256": checksum,
            }

        except FileNotFoundError as exc:
            # Chunk hilang / upload tidak lengkap — bukan error transient,
            # tidak perlu di-retry otomatis, cukup tandai FAILED.
            logger.warning("Assembly gagal untuk dataset %s: %s", dataset_id, exc)
            dataset.upload_status = UploadStatus.FAILED
            db.commit()
            return {"status": "error", "reason": str(exc)}

    except Exception as exc:
        db.rollback()
        logger.exception("Error tak terduga saat assembly dataset %s", dataset_id)
        try:
            dataset = db.get(Dataset, dataset_id)
            if dataset is not None:
                dataset.upload_status = UploadStatus.FAILED
                db.commit()
        except Exception:
            db.rollback()
        raise self.retry(exc=exc, countdown=10)

    finally:
        db.close()
