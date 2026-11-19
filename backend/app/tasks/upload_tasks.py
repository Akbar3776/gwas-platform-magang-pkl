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

import gzip
from pathlib import Path
import shutil

from app.db.engine.database import SessionLocal
from app.db.engine.models import Dataset, UploadStatus
from app.services import upload_service
from app.services.primer_service import build_fasta_index, load_fasta_index
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


class ChecksumMismatchError(ValueError):
    """Dilempar saat checksum SHA-256 berkas rakitan tidak cocok dengan kiriman klien."""


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
            if expected_sha256 and checksum.lower() != expected_sha256.strip().lower():
                final_path.unlink(missing_ok=True)
                raise ChecksumMismatchError(
                    f"Checksum tidak cocok: expected={expected_sha256.strip().lower()}, actual={checksum}"
                )

            # Ini adalah status "completed" yang dimaksud pada requirement —
            # model `Dataset` sudah mendefinisikan nilai ini sebagai UPLOADED
            # (lihat app/db/models.py), menandakan file fisik sudah utuh dan
            # tersimpan permanen, siap masuk tahap validasi/normalisasi berikutnya.
            meta = dict(dataset.extra_metadata or {})
            meta.pop("upload_error", None)
            dataset.extra_metadata = meta
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

        except (FileNotFoundError, ChecksumMismatchError) as exc:
            # Chunk hilang atau checksum tidak cocok — bukan error transient, jangan retry.
            logger.warning("Assembly gagal untuk dataset %s: %s", dataset_id, exc)
            try:
                shutil.rmtree(upload_service.get_staging_dir(str(dataset.id)), ignore_errors=True)
            except Exception:
                pass
            meta = dict(dataset.extra_metadata or {})
            meta["upload_error"] = str(exc)
            dataset.extra_metadata = meta
            dataset.storage_path = ""
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


def _reference_staging_id(dataset_id: str) -> str:
    return f"{dataset_id}_ref"


@celery_app.task(
    bind=True,
    name="app.tasks.upload_tasks.assemble_reference_fasta_task",
    max_retries=1,
)
def assemble_reference_fasta_task(
    self,
    dataset_id: str,
    total_chunks: int,
    original_filename: str,
    expected_sha256: str | None = None,
) -> dict:
    """
    Merakit chunk FASTA referensi, memverifikasi SHA-256, mendekompresi `.gz` sekali
    secara streaming bila perlu, lalu membangun indeks `.fai` satu kali.
    """
    db = SessionLocal()
    staging_id = _reference_staging_id(dataset_id)
    try:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None:
            return {"status": "error", "reason": "dataset_not_found"}

        try:
            assembled_path, total_size = upload_service.assemble_file(
                file_id=staging_id,
                total_chunks=total_chunks,
                original_filename=f"ref_upload_{Path(original_filename).name}",
                dest_id=str(dataset.id),
            )
            checksum = upload_service.compute_sha256(assembled_path)
            if expected_sha256 and checksum.lower() != expected_sha256.strip().lower():
                assembled_path.unlink(missing_ok=True)
                raise ChecksumMismatchError(
                    f"Checksum FASTA tidak cocok: expected={expected_sha256.strip().lower()}, actual={checksum}"
                )

            target_fa = assembled_path.parent / "reference.fa"
            old_fai = Path(f"{target_fa}.fai")
            old_fai.unlink(missing_ok=True)

            if assembled_path.name.lower().endswith(".gz"):
                tmp_plain = target_fa.with_suffix(".fa.unpacking")
                try:
                    with gzip.open(assembled_path, "rb") as src, tmp_plain.open("wb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                    tmp_plain.replace(target_fa)
                finally:
                    tmp_plain.unlink(missing_ok=True)
                    assembled_path.unlink(missing_ok=True)
            else:
                assembled_path.replace(target_fa)

            fai_path = build_fasta_index(target_fa)
            idx = load_fasta_index(target_fa)
            contigs = idx.get("contigs", [])
            if not contigs:
                target_fa.unlink(missing_ok=True)
                fai_path.unlink(missing_ok=True)
                raise ChecksumMismatchError("File FASTA tidak memuat contig (>chr) yang valid.")

            meta = dict(dataset.extra_metadata or {})
            meta["reference_fasta"] = {
                "status": UploadStatus.UPLOADED.value,
                "filename": original_filename,
                "path": str(target_fa),
                "fai_path": str(fai_path),
                "file_size_bytes": target_fa.stat().st_size,
                "checksum_sha256": checksum,
                "n_contigs": len(contigs),
                "contigs": contigs[:50],
            }
            dataset.extra_metadata = meta
            db.commit()
            return {
                "status": "success",
                "dataset_id": str(dataset.id),
                "fasta_path": str(target_fa),
                "fai_path": str(fai_path),
                "n_contigs": len(contigs),
                "contigs": contigs[:50],
            }

        except (FileNotFoundError, ChecksumMismatchError, ValueError, OSError) as exc:
            logger.warning("Assembly FASTA referensi gagal untuk dataset %s: %s", dataset_id, exc)
            try:
                shutil.rmtree(upload_service.get_staging_dir(staging_id), ignore_errors=True)
            except Exception:
                pass
            meta = dict(dataset.extra_metadata or {})
            meta["reference_fasta"] = {
                **(meta.get("reference_fasta") or {}),
                "status": UploadStatus.FAILED.value,
                "filename": original_filename,
                "error_message": str(exc),
            }
            dataset.extra_metadata = meta
            db.commit()
            return {"status": "error", "reason": str(exc)}

    finally:
        db.close()
