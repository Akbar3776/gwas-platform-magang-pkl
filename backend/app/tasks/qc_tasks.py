"""Celery task for PLINK 1.9 quality control."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep
from app.services.qc_service import build_plink_qc_command
from app.tasks.celery_app import celery_app
from app.utils.process import CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)
QC_TIMEOUT_SECONDS = int(os.getenv("QC_TIMEOUT_SECONDS", str(6 * 60 * 60)))


def _error_text(exc: Exception) -> str:
    """Return a useful bounded error message for the database and UI."""
    if isinstance(exc, CLIExecutionError):
        tail = "".join(exc.last_lines).strip()
        if tail:
            return f"{exc}\n\n--- Log terakhir ---\n{tail}"
    return str(exc)


def _set_failed(job: AnalysisJob, exc: Exception, db) -> None:
    message = _error_text(exc)
    job.overall_status = JobStatus.FAILED
    job.error_message = message
    job.error_log = message
    db.commit()


@celery_app.task(
    bind=True,
    name="app.tasks.qc_tasks.run_qc_pipeline",
    queue="qc",
    max_retries=0,
    soft_time_limit=QC_TIMEOUT_SECONDS + 30,
    time_limit=QC_TIMEOUT_SECONDS + 120,
)
def run_qc_pipeline(
    self,
    normalization_result: dict[str, Any] | None,
    job_id: str,
    dataset_id: str,
    qc_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run PLINK QC for one dataset and fail fast on CLI errors."""
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
        input_vcf_path = (normalization_result or {}).get("normalized_vcf_path") or dataset.storage_path
        if not input_vcf_path:
            raise ValueError("Dataset tidak memiliki path VCF pada storage")

        params = {
            "maf": 0.01,
            "geno": 0.05,
            "mind": 0.1,
            "hwe": 1e-6,
            **(qc_params or {}),
        }
        workspace = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id) / "qc"
        output_prefix = workspace / "plink_qc"
        log_file = workspace / "plink_qc.log"
        command = build_plink_qc_command(
            input_vcf_path=input_vcf_path,
            output_prefix=str(output_prefix),
            maf=float(params["maf"]),
            geno=float(params["geno"]),
            mind=float(params["mind"]),
            hwe=float(params["hwe"]),
        )

        job.overall_status = JobStatus.RUNNING
        job.current_step = PipelineStep.QC
        job.started_at = datetime.now(timezone.utc)
        job.work_dir = str(workspace)
        job.qc_output_prefix = str(output_prefix)
        job.error_message = None
        job.error_log = None
        job.parameters = {**(job.parameters or {}), **params, "qc_log_path": str(log_file)}
        db.commit()

        logger.info("Menjalankan PLINK QC job=%s dataset=%s", job_id, dataset_id)
        run_cli_command(
            command,
            cwd=workspace,
            log_file=log_file,
            timeout=QC_TIMEOUT_SECONDS,
        )

        expected_outputs = [Path(f"{output_prefix}{suffix}") for suffix in (".bed", ".bim", ".fam", ".vcf.gz")]
        missing_outputs = [str(path) for path in expected_outputs if not path.exists()]
        if missing_outputs:
            raise RuntimeError(f"Output PLINK tidak lengkap: {', '.join(missing_outputs)}")

        job.overall_status = JobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "status": "completed",
            "job_id": str(job.id),
            "dataset_id": str(dataset.id),
            "output_prefix": str(output_prefix),
            "qc_vcf_path": str(output_prefix) + ".vcf.gz",
            "log_file": str(log_file),
        }

    except SoftTimeLimitExceeded as exc:
        if "job" in locals() and job is not None:
            _set_failed(job, RuntimeError("PLINK QC melewati batas waktu dan dihentikan"), db)
        raise exc
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            _set_failed(job, exc, db)
        logger.exception("QC gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()


# Compatibility name for callers that still import the old task symbol.
run_plink_qc = run_qc_pipeline
