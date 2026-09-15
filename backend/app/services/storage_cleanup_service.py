"""
services/storage_cleanup_service.py

Utilitas cleanup file INTERMEDIATE pipeline GWAS (bukan retention policy —
lihat tasks/retention_tasks.py untuk itu). Dipanggil sebagai task TERAKHIR
dalam Celery chain, setelah tahap GWAS + parsing hasil (generate_plot_data)
selesai, supaya file besar yang cuma dipakai sebagai staging antar-tahap
tidak menumpuk di disk padahal job-nya sendiri masih aktif/baru selesai.

PRINSIP "CONSERVATIVE DELETE":
    DIHAPUS      -> file yang BISA di-generate ulang dari tahap sebelumnya
                    kalau perlu (PLINK .bed/.bim/.fam, output Beagle mentah,
                    sisa chunk upload).
    DIPERTAHANKAN -> file yang TIDAK bisa/mahal untuk di-generate ulang:
                    VCF asli hasil upload user, dan seluruh isi folder
                    "results/" job (assoc final, json manhattan/qq, report
                    PDF, zip export) — yaitu apa pun yang path-nya
                    direferensikan oleh tabel `results`.

ASUMSI STRUKTUR DIREKTORI (mengacu docs/design.md bagian 2.3 — SESUAIKAN
kalau upload_service.py/config.py aslimu pakai layout berbeda):

    /storage/datasets/{dataset_id}/raw/{filename}.vcf.gz   <- dataset.storage_path (JANGAN dihapus)
    /storage/datasets/{dataset_id}/staging/chunks/...      <- sisa chunk upload (boleh dihapus)
    /storage/datasets/{dataset_id}/jobs/{job_id}/staging/  <- kerja PLINK/Beagle (boleh dihapus)
    /storage/datasets/{dataset_id}/jobs/{job_id}/results/  <- assoc, json, report, zip (JANGAN dihapus)
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.engine.models import AnalysisJob, Dataset

logger = logging.getLogger(__name__)

# Set True kalau VCF hasil normalisasi (bcftools norm) juga mau dihapus,
# karena toh bisa di-generate ulang dari VCF asli + parameter yang sama.
# Default False = lebih aman, biar tidak perlu re-normalisasi kalau user
# minta re-run QC dengan parameter berbeda dalam waktu dekat.
DELETE_NORMALIZED_VCF = False

# File pendamping PLINK & Beagle yang aman dihapus setelah tahap masing-masing selesai.
PLINK_INTERMEDIATE_SUFFIXES = [".bed", ".bim", ".fam", ".log", ".hh", ".nosex"]
BEAGLE_INTERMEDIATE_SUFFIXES = [".vcf.gz", ".vcf.gz.tbi", ".log"]


def _remove_file(path_str: str | None) -> int:
    """Hapus satu file kalau ada. Return byte yang dibebaskan (0 kalau file tidak ada)."""
    if not path_str:
        return 0
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return 0
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError as exc:
        logger.warning("Gagal menghapus file %s: %s", path, exc)
        return 0


def _remove_dir(path_str: str | None) -> int:
    """Hapus direktori beserta isinya kalau ada. Return estimasi byte yang dibebaskan."""
    if not path_str:
        return 0
    path = Path(path_str)
    if not path.exists() or not path.is_dir():
        return 0
    try:
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path)
        return size
    except OSError as exc:
        logger.warning("Gagal menghapus direktori %s: %s", path, exc)
        return 0


def _remove_by_prefix(prefix: str | None, suffixes: list[str]) -> int:
    """Hapus semua file `{prefix}{suffix}` untuk tiap suffix di `suffixes`."""
    freed = 0
    for suffix in suffixes:
        freed += _remove_file(f"{prefix}{suffix}" if prefix else None)
    return freed


def cleanup_intermediate_files(db: Session, job: AnalysisJob) -> dict:
    """
    Bersihkan file intermediate milik satu AnalysisJob yang pipeline-nya
    sudah selesai (COMPLETED/SUCCESS). JANGAN dipanggil di tengah chain —
    pastikan ini task paling akhir, setelah generate_plot_data.
    """
    freed_bytes = 0

    # 1. Output PLINK QC (.bed/.bim/.fam + file pendamping)
    freed_bytes += _remove_by_prefix(job.qc_output_prefix, PLINK_INTERMEDIATE_SUFFIXES)

    # 2. Output imputasi (Beagle/Minimac) — VCF hasil imputasi mentah,
    #    BUKAN hasil akhir (hasil akhir sudah diringkas jadi assoc + json
    #    di tahap GWAS/parsing, itu yang dipertahankan).
    freed_bytes += _remove_by_prefix(job.imputed_prefix, BEAGLE_INTERMEDIATE_SUFFIXES)

    # 3. Sisa direktori staging chunk upload dataset terkait (harusnya sudah
    #    dibersihkan oleh upload_tasks.py saat finalize, ini cuma jaring pengaman).
    dataset: Dataset | None = job.dataset
    if dataset is not None and dataset.storage_path:
        staging_chunk_dir = Path(dataset.storage_path).parent.parent / "staging" / "chunks"
        freed_bytes += _remove_dir(str(staging_chunk_dir))

    # 4. Subfolder "staging" milik job ini — TIDAK termasuk subfolder "results"
    #    (di situ ada assoc final, json manhattan/qq, report PDF, zip export).
    if job.work_dir:
        freed_bytes += _remove_dir(str(Path(job.work_dir) / "staging"))

    if DELETE_NORMALIZED_VCF and dataset is not None:
        freed_bytes += _remove_file(dataset.normalized_vcf_path)
        dataset.normalized_vcf_path = None
        db.add(dataset)

    db.commit()
    logger.info(
        "Cleanup intermediate job=%s selesai — %.2f MB dibebaskan",
        job.id, freed_bytes / (1024 * 1024),
    )
    return {"job_id": str(job.id), "freed_bytes": freed_bytes}
