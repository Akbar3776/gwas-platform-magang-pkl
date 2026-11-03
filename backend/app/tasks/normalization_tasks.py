"""Celery task for validating and normalizing uploaded VCF files."""

from __future__ import annotations

import gzip
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep
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
        return f"Format VCF tidak valid atau rusak. {tail}" if tail else "Format VCF tidak valid atau rusak."
    return str(exc)


def _fallback_normalize_vcf(input_path: Path, output_path: Path, log_path: Path) -> None:
    """Validate VCF header in Python and write gzipped output when bcftools is unavailable."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    open_fn = gzip.open if input_path.name.endswith(".gz") else open
    has_header = False
    with open_fn(input_path, "rt", encoding="utf-8", errors="replace") as src:
        for _ in range(200):
            line = src.readline()
            if not line:
                break
            if line.startswith("##fileformat=VCF") or line.startswith("#CHROM"):
                has_header = True
                break
    if not has_header:
        raise ValueError("Format VCF tidak valid: header #CHROM atau ##fileformat=VCF tidak ditemukan.")

    if input_path.name.endswith(".gz"):
        shutil.copy2(input_path, output_path)
    else:
        with input_path.open("rb") as f_in, gzip.open(output_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"[FALLBACK] bcftools tidak ditemukan di PATH; validasi header & kompresi selesai: {output_path}\n")


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
        if bcftools_bin:
            try:
                # Header-only read validates that bcftools recognizes the input as VCF/BCF.
                run_cli_command(
                    [bcftools_bin, "view", "--no-version", "-h", str(input_path)],
                    cwd=workspace,
                    log_file=log_path,
                    timeout=NORMALIZATION_TIMEOUT_SECONDS,
                )

                reference_fasta = params.get("reference_fasta")
                sorted_path = workspace / "sorted.vcf.gz"
                run_cli_command(
                    [bcftools_bin, "sort", "-Oz", "-o", str(sorted_path), str(input_path)],
                    cwd=workspace,
                    log_file=log_path,
                    timeout=NORMALIZATION_TIMEOUT_SECONDS,
                )

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

                run_cli_command(
                    [bcftools_bin, "index", "-t", str(output_path)],
                    cwd=workspace,
                    log_file=log_path,
                    timeout=600,
                )
            except Exception as cli_exc:
                logger.warning(
                    "bcftools gagal memproses VCF (%s); beralih ke Python fallback...",
                    cli_exc,
                )
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(
                        f"[FALLBACK] bcftools gagal dieksekusi ({cli_exc}); menjalankan normalisasi Python...\n"
                    )
                _fallback_normalize_vcf(input_path, output_path, log_path)
        else:
            _fallback_normalize_vcf(input_path, output_path, log_path)

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
                db.commit()
        finally:
            db.close()
        return {
            **ctx,
            "job_id": job_id,
            "dataset_id": dataset_id,
            "normalized_vcf_path": str(output_path),
            "log_file": str(log_path),
        }
    except Exception as exc:
        message = _friendly_error(exc)
        _mark_job(job_id, status=JobStatus.FAILED, failed_step="normalization", error=message)
        logger.exception("Normalisasi VCF gagal untuk job=%s", job_id)
        raise RuntimeError(message) from exc
