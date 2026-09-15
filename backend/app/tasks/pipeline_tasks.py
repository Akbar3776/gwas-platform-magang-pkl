"""
Definisi task individual untuk setiap tahap pipeline GWAS:

    task_qc -> task_imputation -> task_gemma_gwas -> task_parse_results

Prinsip desain:
  1. `job_id` dikirim sebagai KEYWORD ARGUMENT di SEMUA task, termasuk task
     pertama (`task_qc`). Ini membuat `JobAwareTask.on_failure` bisa membaca
     `job_id` dengan cara seragam dari `kwargs`, terlepas dari task mana
     yang gagal dan berapa pun jumlah argumen posisionalnya.
  2. Setiap task mengembalikan SATU nilai (path file output) yang otomatis
     menjadi argumen POSISIONAL PERTAMA task berikutnya ketika dirangkai
     dengan `celery.chain` (lihat `pipeline_orchestrator.py`).
  3. Status & progress step pada `AnalysisJob` di-update di awal tiap task,
     supaya frontend (via polling) bisa menampilkan stepper yang akurat.
  4. Tidak ada auto-retry (`max_retries = 0`): kegagalan bioinformatika
     (format VCF salah, data tidak lolos QC, dst.) biasanya bukan error
     transient, sehingga retry otomatis hanya akan membuang waktu komputasi.

Asumsi struktur proyek (sesuaikan importnya bila berbeda):
  - app.core.celery_app.celery_app          -> instance Celery
  - app.db.session.SessionLocal             -> sessionmaker SQLAlchemy
  - app.models.job.AnalysisJob              -> kolom minimal: id, status,
        error_message, failed_step, work_dir, qc_output_prefix,
        imputed_prefix, assoc_output_path, manhattan_cache_path,
        phenotype_file_path, progress_step
  - app.models.job.JobStatus                -> TAMBAHKAN value berikut jika
        belum ada: PENDING, RUNNING_QC, RUNNING_IMPUTATION, RUNNING_GWAS,
        RUNNING_PARSING, SUCCESS, FAILED
  - app.services.manhattan_parser.generate_manhattan_cache (lihat task
    sebelumnya untuk implementasinya)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from celery import Task

from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
from app.tasks.celery_app import celery_app
from app.services.manhattan_parser import generate_manhattan_cache
from app.utils.process import SubprocessFailed, run_subprocess

logger = logging.getLogger(__name__)

PLINK_BINARY = os.getenv("PLINK_BINARY_PATH", "plink")
IMPUTATION_BINARY = os.getenv("IMPUTATION_BINARY_PATH", "beagle")
GEMMA_BINARY = os.getenv("GEMMA_BINARY_PATH", "gemma")

TASK_TIME_LIMIT = 60 * 60 * 6  # 6 jam batas keras per task
TASK_SOFT_TIME_LIMIT = TASK_TIME_LIMIT - 300


def _update_job(job_id: str, **fields: Any) -> None:
    """Buka DB session baru (worker-safe, terpisah dari request FastAPI) lalu update kolom job."""
    db = SessionLocal()
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None:
            logger.warning("AnalysisJob %s tidak ditemukan saat mencoba update status.", job_id)
            return
        for key, value in fields.items():
            setattr(job, key, value)
        db.commit()
    finally:
        db.close()


def _get_job_or_raise(job_id: str) -> AnalysisJob:
    """Ambil job dari DB lalu lepas (`expunge`) dari session supaya aman dipakai setelah session ditutup."""
    db = SessionLocal()
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")
        db.expunge(job)
        return job
    finally:
        db.close()


class JobAwareTask(Task):
    """
    Base class Celery Task yang otomatis menandai `AnalysisJob` terkait
    sebagai FAILED (beserta pesan error & nama tahap yang gagal) setiap kali
    task turunan melempar exception -- di tahap manapun pipeline berhenti.

    Catatan fail-fast: `celery.chain` hanya memanggil task berikutnya saat
    task sebelumnya SUKSES (Celery menghubungkannya lewat callback `link`,
    yang hanya terpicu pada state SUCCESS). Karena itu, satu task yang
    gagal di sini otomatis menghentikan seluruh sisa chain tanpa perlu
    logika pemutus tambahan -- `on_failure` di bawah ini murni untuk
    mencatat status & error ke database.
    """

    autoretry_for: tuple = ()
    max_retries = 0
    time_limit = TASK_TIME_LIMIT
    soft_time_limit = TASK_SOFT_TIME_LIMIT

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: N803 - ikuti signature Celery
        job_id = kwargs.get("job_id")
        if not job_id:
            logger.error(
                "Task '%s' (id=%s) gagal namun job_id tidak ditemukan di kwargs: %s",
                self.name, task_id, kwargs,
            )
            return

        error_message = str(exc)
        logger.error("Pipeline berhenti di task '%s' untuk job %s: %s", self.name, job_id, error_message)

        _update_job(
            job_id,
            overall_status=JobStatus.FAILED,
            failed_step=self.name,
            error_message=error_message,
        )


# ---------------------------------------------------------------------------
# Tahap 1: Quality Control (PLINK)
# ---------------------------------------------------------------------------
@celery_app.task(base=JobAwareTask, bind=True, name="pipeline.task_qc")
def task_qc(self, job_id: str, raw_vcf_path: str) -> str:
    """
    Menjalankan filtering QC standar (missingness, MAF, Hardy-Weinberg)
    memakai PLINK, mengonversi VCF hasil normalisasi menjadi PLINK binary
    (.bed/.bim/.fam).

    Returns
    -------
    str: prefix file PLINK binary hasil QC (tanpa ekstensi) -- dipakai
    sebagai argumen posisional pertama `task_imputation`.
    """
    job = _get_job_or_raise(job_id)
    work_dir = Path(job.work_dir)
    log_file = work_dir / "pipeline.log"

    _update_job(job_id, overall_status=JobStatus.RUNNING_QC, current_step=PipelineStep.QC, error_message=None)

    qc_prefix = str(work_dir / "qc_output")
    cmd = [
        PLINK_BINARY,
        "--vcf", raw_vcf_path,
        "--maf", "0.01",
        "--geno", "0.05",
        "--hwe", "1e-6",
        "--make-bed",
        "--out", qc_prefix,
    ]

    try:
        run_subprocess(cmd, cwd=work_dir, log_file=log_file)
    except SubprocessFailed as exc:
        # Pesan dibungkus ulang lebih ramah untuk ditampilkan ke peneliti non-technical
        raise RuntimeError(
            "Tahap Quality Control gagal. Kemungkinan format file VCF tidak "
            f"valid, atau tidak ada varian yang lolos filter QC. Detail: {exc}"
        ) from exc

    if not Path(f"{qc_prefix}.bed").exists():
        raise RuntimeError("File PLINK binary (.bed) hasil QC tidak ditemukan setelah eksekusi PLINK")

    _update_job(job_id, qc_output_prefix=qc_prefix)
    return qc_prefix


# ---------------------------------------------------------------------------
# Tahap 2: Imputasi
# ---------------------------------------------------------------------------
@celery_app.task(base=JobAwareTask, bind=True, name="pipeline.task_imputation")
def task_imputation(self, qc_output_prefix: str, job_id: str) -> str:
    """
    Menjalankan imputasi genotipe pada hasil QC.

    CATATAN: command di bawah adalah PLACEHOLDER generik. Ganti sesuai tool
    imputasi yang benar-benar dipakai (Beagle, IMPUTE2, Minimac, dll) --
    masing-masing punya format argumen dan kebutuhan reference panel yang
    berbeda.

    Returns
    -------
    str: prefix/path file hasil imputasi -- dipakai sebagai argumen
    posisional pertama `task_gemma_gwas`.
    """
    job = _get_job_or_raise(job_id)
    work_dir = Path(job.work_dir)
    log_file = work_dir / "pipeline.log"

    _update_job(job_id, overall_status=JobStatus.RUNNING_IMPUTATION, current_step=PipelineStep.IMPUTATION)

    imputed_prefix = str(work_dir / "imputed_output")
    cmd = [
        IMPUTATION_BINARY,
        f"gt={qc_output_prefix}.vcf.gz",  # sesuaikan: sebagian tool butuh VCF, bukan .bed/.bim/.fam
        f"out={imputed_prefix}",
    ]

    try:
        run_subprocess(cmd, cwd=work_dir, log_file=log_file)
    except SubprocessFailed as exc:
        raise RuntimeError(f"Tahap Imputasi gagal. Detail: {exc}") from exc

    _update_job(job_id, imputed_prefix=imputed_prefix)
    return imputed_prefix


# ---------------------------------------------------------------------------
# Tahap 3: Analisis GWAS (GEMMA)
# ---------------------------------------------------------------------------
@celery_app.task(base=JobAwareTask, bind=True, name="pipeline.task_gemma_gwas")
def task_gemma_gwas(self, imputed_prefix: str, job_id: str) -> str:
    """
    Menjalankan GEMMA dua tahap: kinship matrix (-gk 1) lalu uji asosiasi
    linear mixed model (-lmm 4).

    Returns
    -------
    str: path file `.assoc.txt` -- dipakai sebagai argumen posisional
    pertama `task_parse_results`.
    """
    job = _get_job_or_raise(job_id)
    work_dir = Path(job.work_dir)
    log_file = work_dir / "pipeline.log"
    phenotype_file = job.phenotype_file_path

    _update_job(job_id, overall_status=JobStatus.RUNNING_GWAS, current_step=PipelineStep.GWAS)

    kinship_prefix = "kinship"
    kinship_cmd = [GEMMA_BINARY, "-bfile", imputed_prefix, "-gk", "1", "-o", kinship_prefix]
    try:
        run_subprocess(kinship_cmd, cwd=work_dir, log_file=log_file)
    except SubprocessFailed as exc:
        raise RuntimeError(f"GEMMA gagal menghitung kinship matrix. Detail: {exc}") from exc

    kinship_matrix_path = work_dir / "output" / f"{kinship_prefix}.cXX.txt"
    if not kinship_matrix_path.exists():
        raise RuntimeError("File kinship matrix tidak ditemukan setelah GEMMA -gk selesai")

    _update_job(job_id, current_step=PipelineStep.GWAS)

    assoc_prefix = "assoc"
    assoc_cmd = [
        GEMMA_BINARY,
        "-bfile", imputed_prefix,
        "-k", str(kinship_matrix_path),
        "-lmm", "4",
        "-o", assoc_prefix,
    ]
    if phenotype_file:
        assoc_cmd += ["-p", phenotype_file]

    try:
        run_subprocess(assoc_cmd, cwd=work_dir, log_file=log_file)
    except SubprocessFailed as exc:
        raise RuntimeError(f"GEMMA gagal pada uji asosiasi. Detail: {exc}") from exc

    assoc_output_path = work_dir / "output" / f"{assoc_prefix}.assoc.txt"
    if not assoc_output_path.exists():
        raise RuntimeError("File output .assoc.txt tidak ditemukan setelah GEMMA selesai")

    _update_job(job_id, assoc_output_path=str(assoc_output_path))
    return str(assoc_output_path)


# ---------------------------------------------------------------------------
# Tahap 4: Parsing hasil untuk Manhattan/QQ Plot
# ---------------------------------------------------------------------------
@celery_app.task(base=JobAwareTask, bind=True, name="pipeline.task_parse_results")
def task_parse_results(self, assoc_output_path: str, job_id: str) -> dict:
    """
    Tahap terakhir chain: parsing + downsampling output GEMMA menjadi cache
    ringan (Parquet) untuk Manhattan/QQ Plot, lalu menandai job SUCCESS.

    Ini adalah SATU-SATUNYA tempat status job diubah menjadi SUCCESS --
    jika salah satu tahap sebelumnya gagal, task ini tidak akan pernah
    dipanggil sama sekali oleh `celery.chain`.
    """
    job = _get_job_or_raise(job_id)
    work_dir = Path(job.work_dir)

    _update_job(job_id, overall_status=JobStatus.RUNNING_PARSING, current_step=PipelineStep.VISUALIZATION)

    cache_path = work_dir / "manhattan_cache.parquet"
    try:
        summary = generate_manhattan_cache(
            assoc_file_path=assoc_output_path,
            output_cache_path=str(cache_path),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gagal parsing hasil GWAS. Detail: {exc}") from exc

    _update_job(
        job_id,
        overall_status=JobStatus.SUCCESS,
        current_step=PipelineStep.VISUALIZATION,
        manhattan_cache_path=str(cache_path),
        error_message=None,
    )

    return {"job_id": job_id, "manhattan_cache_path": str(cache_path), **summary}
