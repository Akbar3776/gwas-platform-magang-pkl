"""Celery task for validating and normalizing uploaded VCF files."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.utils.log import get_task_logger

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep
from app.tasks.celery_app import celery_app
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
    job_id: str,
    dataset_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Validate, sort, optionally normalize, and index one uploaded VCF."""
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
            job.started_at = datetime.now(timezone.utc)
            job.work_dir = str(workspace)
            job.error_message = None
            job.error_log = None
            db.commit()
        finally:
            db.close()

        workspace.mkdir(parents=True, exist_ok=True)
        # Header-only read validates that bcftools recognizes the input as VCF/BCF.
        run_cli_command(
            ["bcftools", "view", "--no-version", "-h", str(input_path)],
            cwd=workspace,
            log_file=log_path,
            timeout=NORMALIZATION_TIMEOUT_SECONDS,
        )

        reference_fasta = params.get("reference_fasta")
        sorted_path = workspace / "sorted.vcf.gz"
        run_cli_command(
            ["bcftools", "sort", "-Oz", "-o", str(sorted_path), str(input_path)],
            cwd=workspace,
            log_file=log_path,
            timeout=NORMALIZATION_TIMEOUT_SECONDS,
        )

        if reference_fasta:
            run_cli_command(
                ["bcftools", "norm", "-f", str(reference_fasta), "-Oz", "-o", str(output_path), str(sorted_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=NORMALIZATION_TIMEOUT_SECONDS,
            )
            sorted_path.unlink(missing_ok=True)
        else:
            sorted_path.replace(output_path)

        run_cli_command(
            ["bcftools", "index", "-t", str(output_path)],
            cwd=workspace,
            log_file=log_path,
            timeout=600,
        )
        if not output_path.is_file():
            raise RuntimeError("bcftools selesai tetapi file VCF hasil normalisasi tidak ditemukan")

        _mark_job(
            job_id,
            status=JobStatus.COMPLETED,
            current_step=PipelineStep.QC,
            completed_at=datetime.now(timezone.utc),
        )
        db = SessionLocal()
        try:
            dataset = db.get(Dataset, dataset_id)
            if dataset is not None:
                dataset.normalized_vcf_path = str(output_path)
                db.commit()
        finally:
            db.close()
        return {"job_id": job_id, "dataset_id": dataset_id, "normalized_vcf_path": str(output_path), "log_file": str(log_path)}
    except Exception as exc:
        message = _friendly_error(exc)
        _mark_job(job_id, status=JobStatus.FAILED, error=message)
        logger.exception("Normalisasi VCF gagal untuk job=%s", job_id)
        raise RuntimeError(message) from exc
