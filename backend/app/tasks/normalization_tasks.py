"""Celery task for validating and normalizing uploaded VCF files."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep
from app.services.qc_service import MAX_32BIT_POS, filter_vcf_32bit_overflow
from app.tasks._common import which_tool
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)
NORMALIZATION_TIMEOUT_SECONDS = int(os.getenv("NORMALIZATION_TIMEOUT_SECONDS", str(2 * 60 * 60)))


def _mark_job(job_id: str, *, status: JobStatus | None = None, error: str | None = None, **fields: Any) -> None:
    db = SessionLocal()
    try:
        job = db.get(AnalysisJob, job_id)
        if job is None:
            return
        if status is not None:
            job.overall_status = status
        if error is not None:
            job.error_message = error
            job.error_log = error
        for key, value in fields.items():
            setattr(job, key, value)
        db.commit()
    finally:
        db.close()


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, CLIExecutionError):
        tail = "".join(exc.last_lines).strip()
        return f"Eksekusi bcftools gagal: {tail}" if tail else f"Eksekusi bcftools gagal: {exc}"
    return str(exc)


def _index_normalized_vcf(bcftools_bin: str, output_path: Path, workspace: Path, log_path: Path) -> None:
    """Index VCF using TBI, with automatic fallback to CSI for large chromosomes (>512Mb)."""
    try:
        run_cli_command(
            [bcftools_bin, "index", "-t", str(output_path)],
            cwd=workspace,
            log_file=log_path,
            timeout=600,
        )
    except Exception as tbi_exc:
        logger.warning(
            "Index TBI gagal (%s), beralih membuat index CSI (mendukung koordinat >512Mb)...",
            tbi_exc,
        )
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"[INFO] Index TBI gagal ({tbi_exc}); beralih membuat index CSI (koordinat >512Mb)...\n"
            )
        try:
            run_cli_command(
                [bcftools_bin, "index", "-c", str(output_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=600,
            )
        except Exception as csi_exc:
            logger.warning(
                "Index CSI juga gagal (%s); melanjutkan pipeline tanpa index.",
                csi_exc,
            )
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[WARNING] Pembuatan index VCF gagal ({csi_exc}); melanjutkan pipeline tanpa index.\n"
                )


@celery_app.task(
    bind=True,
    name="app.tasks.normalization_tasks.run_vcf_normalization",
    queue="normalization",
    max_retries=0,
    acks_late=True,
    soft_time_limit=NORMALIZATION_TIMEOUT_SECONDS + 30,
    time_limit=NORMALIZATION_TIMEOUT_SECONDS + 120,
)
def run_vcf_normalization(
    self,
    prev_result: dict[str, Any] | str | None = None,
    job_id: str | None = None,
    dataset_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, sort, optionally normalize, and index one uploaded VCF."""
    if isinstance(prev_result, dict):
        ctx = dict(prev_result)
        job_id = str(job_id or ctx.get("job_id") or "")
        dataset_id = str(dataset_id or ctx.get("dataset_id") or "")
        params = params or ctx.get("params") or {}
    elif isinstance(prev_result, str):
        ctx = {}
        if not job_id:
            job_id = prev_result
    else:
        ctx = {}

    params = params or {}
    try:
        db = SessionLocal()
        try:
            job = db.get(AnalysisJob, job_id)
            dataset = db.get(Dataset, dataset_id)
            if job is None:
                raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")
            if dataset is None:
                raise ValueError(f"Dataset {dataset_id} tidak ditemukan")
            if str(job.dataset_id) != str(dataset.id):
                raise ValueError("Dataset tidak sesuai dengan AnalysisJob")
            input_path = Path(dataset.storage_path)
            if not input_path.is_file():
                raise FileNotFoundError(f"VCF upload tidak ditemukan: {input_path}")

            workspace = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id) / "normalization"
            output_path = workspace / "normalized.vcf.gz"
            log_path = workspace / "bcftools.log"
            job.overall_status = JobStatus.RUNNING_NORMALIZATION
            job.current_step = PipelineStep.NORMALIZATION
            if job.started_at is None:
                job.started_at = datetime.now(timezone.utc)
            job.work_dir = str(workspace)
            job.error_message = None
            job.error_log = None
            db.commit()
        finally:
            db.close()

        workspace.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"=== TAHAP 2: NORMALISASI VCF (bcftools) ===\n"
                f"Waktu mulai : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"Input VCF   : {input_path.name} ({input_path.stat().st_size} bytes)\n"
                f"Target      : {output_path}\n"
            )
        bcftools_bin = which_tool("bcftools")
        if not bcftools_bin:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("[ERROR] Tools bcftools tidak ditemukan; proses normalisasi dihentikan.\n")
            raise RuntimeError("Tools bcftools tidak ditemukan; proses normalisasi dihentikan.")

        clean_vcf_path = workspace / "clean.vcf"
        sorted_path = workspace / "sorted.vcf.gz"
        overflow_stats = {
            "total_variants": 0,
            "kept_variants": 0,
            "overflow_removed": 0,
            "max_pos": MAX_32BIT_POS,
        }
        try:
            # Header-only read validates that bcftools recognizes the input as VCF/BCF.
            run_cli_command(
                [bcftools_bin, "view", "--no-version", "-h", str(input_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=NORMALIZATION_TIMEOUT_SECONDS,
            )

            # Fase 0. Additional (QC Overflowing):
            # zcat namafile_vcf.gz | awk '/^#/ || $2 <= 2147483647' > clean.vcf
            overflow_stats = filter_vcf_32bit_overflow(
                input_vcf=input_path,
                output_vcf=clean_vcf_path,
                max_pos=MAX_32BIT_POS,
                compress_output=False,
            )
            with (workspace / "qc_overflow_stats.json").open("w", encoding="utf-8") as sf:
                json.dump(overflow_stats, sf, indent=2)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[Fase 0 - QC Overflowing] zcat {input_path.name} | awk '/^#/ || $2 <= {MAX_32BIT_POS}' > clean.vcf\n"
                    f"[Fase 0 - QC Overflowing] Selesai: total={overflow_stats['total_variants']} varian, "
                    f"lolos={overflow_stats['kept_variants']} varian (POS <= {MAX_32BIT_POS}), "
                    f"tereliminasi overflow 32-bit={overflow_stats['overflow_removed']} varian.\n"
                )

            reference_fasta = params.get("reference_fasta")
            run_cli_command(
                [bcftools_bin, "sort", "-Oz", "-o", str(sorted_path), str(clean_vcf_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=NORMALIZATION_TIMEOUT_SECONDS,
            )
            clean_vcf_path.unlink(missing_ok=True)

            if reference_fasta:
                run_cli_command(
                    [bcftools_bin, "norm", "-f", str(reference_fasta), "-Oz", "-o", str(output_path), str(sorted_path)],
                    cwd=workspace,
                    log_file=log_path,
                    timeout=NORMALIZATION_TIMEOUT_SECONDS,
                )
                sorted_path.unlink(missing_ok=True)
            else:
                sorted_path.replace(output_path)
        except Exception as cli_exc:
            clean_vcf_path.unlink(missing_ok=True)
            sorted_path.unlink(missing_ok=True)
            logger.error("bcftools gagal memproses VCF (%s); menghentikan pipeline.", cli_exc)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[ERROR] bcftools gagal dieksekusi ({cli_exc}); proses dihentikan tanpa fallback Python.\n"
                )
            raise

        if output_path.is_file():
            _index_normalized_vcf(bcftools_bin, output_path, workspace, log_path)

        if not output_path.is_file():
            raise RuntimeError("Normalisasi selesai tetapi file VCF hasil normalisasi tidak ditemukan")

        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"[SUCCESS] Normalisasi VCF selesai ({output_path.stat().st_size} bytes).\n"
            )

        # Keep job status as RUNNING_QC so the frontend polling hook knows the pipeline is still active.
        _mark_job(
            job_id,
            status=JobStatus.RUNNING_QC,
            current_step=PipelineStep.QC,
        )
        db = SessionLocal()
        try:
            dataset = db.get(Dataset, dataset_id)
            if dataset is not None:
                dataset.normalized_vcf_path = str(output_path)
            job = db.get(AnalysisJob, job_id)
            if job is not None:
                job.parameters = {
                    **(job.parameters or {}),
                    "qc_overflow_removed": overflow_stats["overflow_removed"],
                    "qc_overflow_stats": overflow_stats,
                }
            db.commit()
        finally:
            db.close()
        return {
            **ctx,
            "job_id": job_id,
            "dataset_id": dataset_id,
            "normalized_vcf_path": str(output_path),
            "qc_overflow_removed": overflow_stats["overflow_removed"],
            "qc_overflow_stats": overflow_stats,
            "log_file": str(log_path),
        }
    except Exception as exc:
        message = _friendly_error(exc)
        _mark_job(job_id, status=JobStatus.FAILED, failed_step="normalization", error=message)
        logger.exception("Normalisasi VCF gagal untuk job=%s", job_id)
        raise RuntimeError(message) from exc
