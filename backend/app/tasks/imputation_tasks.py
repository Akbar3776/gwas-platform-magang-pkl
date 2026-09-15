"""Celery task for memory-bounded Beagle imputation."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.utils.log import get_task_logger

from app.core.resource_limits import (
    build_java_command,
    make_memory_limited_preexec_fn,
    resolve_rlimit_as_bytes,
)
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
from app.tasks.celery_app import celery_app
from app.utils.process import CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)
BEAGLE_JAR_PATH = os.getenv("BEAGLE_JAR_PATH", "/opt/tools/beagle.jar")
IMPUTATION_TIMEOUT_SECONDS = int(os.getenv("IMPUTATION_TIMEOUT_SECONDS", str(6 * 60 * 60)))
BEAGLE_MAX_MEMORY = os.getenv("BEAGLE_MAX_MEMORY", "")


def _memory_to_mb(value: str) -> int:
    match = re.fullmatch(r"-Xmx(\d+)([mMgG])", value.strip())
    if not match:
        raise ValueError("BEAGLE_MAX_MEMORY harus berbentuk -Xmx512m atau -Xmx8g")
    amount = int(match.group(1))
    return amount * (1024 if match.group(2).lower() == "g" else 1)


def _beagle_command(qc_vcf_path: str, reference_vcf_path: str, output_prefix: str, params: dict[str, Any]):
    nthreads = int(params.get("nthreads", 2))
    memory_flag = str(params.get("beagle_max_memory") or BEAGLE_MAX_MEMORY).strip()
    if memory_flag:
        heap_mb = _memory_to_mb(memory_flag)
        command = [
            "java",
            memory_flag,
            f"-Xms{min(heap_mb, 1024)}m",
            "-XX:+ExitOnOutOfMemoryError",
            "-jar",
            BEAGLE_JAR_PATH,
        ]
        rlimit = resolve_rlimit_as_bytes(heap_mb)
    else:
        command, rlimit = build_java_command(
            BEAGLE_JAR_PATH,
            [],
            container_mem_limit_mb=params.get("container_mem_limit_mb"),
        )
    command.extend([
        f"gt={qc_vcf_path}",
        f"ref={reference_vcf_path}",
        f"out={output_prefix}",
        f"nthreads={nthreads}",
    ])
    if params.get("chromosome"):
        command.append(f"chrom={params['chromosome']}")
    if params.get("map_path"):
        command.append(f"map={params['map_path']}")
    return command, rlimit


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


@celery_app.task(
    bind=True,
    name="app.tasks.imputation_tasks.run_imputation",
    queue="imputation",
    max_retries=0,
    acks_late=True,
    soft_time_limit=IMPUTATION_TIMEOUT_SECONDS + 30,
    time_limit=IMPUTATION_TIMEOUT_SECONDS + 120,
)
def run_imputation(
    self,
    qc_result: dict[str, Any],
    job_id: str,
    reference_vcf_path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Run Beagle with explicit JVM and OS-level memory protection."""
    try:
        params = params or {}
        qc_vcf_path = qc_result.get("qc_vcf_path")
        if not qc_vcf_path:
            raise ValueError("Hasil QC tidak memiliki qc_vcf_path")
        output_dir = params.get("output_dir")
        output_path = Path(output_dir or Path(os.getenv("STORAGE_ROOT", "/data/storage")) / "jobs" / job_id / "imputation")
        output_prefix = output_path / "imputed"
        log_file = output_path / "beagle.log"
        if not Path(qc_vcf_path).is_file():
            raise FileNotFoundError(f"VCF hasil QC tidak ditemukan: {qc_vcf_path}")
        if not Path(reference_vcf_path).is_file():
            raise FileNotFoundError(f"VCF reference panel tidak ditemukan: {reference_vcf_path}")

        command, rlimit_bytes = _beagle_command(qc_vcf_path, reference_vcf_path, str(output_prefix), params)
        _mark_job(
            job_id,
            status=JobStatus.RUNNING_IMPUTATION,
            current_step=PipelineStep.IMPUTATION,
            started_at=datetime.now(timezone.utc),
            work_dir=str(output_path),
            imputed_prefix=str(output_prefix),
            error_message=None,
            error_log=None,
        )
        run_cli_command(
            command,
            cwd=output_path,
            log_file=log_file,
            timeout=IMPUTATION_TIMEOUT_SECONDS,
            preexec_fn=make_memory_limited_preexec_fn(rlimit_bytes) if os.name != "nt" else None,
        )
        output_vcf = Path(f"{output_prefix}.vcf.gz")
        if not output_vcf.is_file():
            raise RuntimeError(f"Beagle selesai tetapi output tidak ditemukan: {output_vcf}")
        _mark_job(job_id, status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        return {"job_id": job_id, "output_vcf": str(output_vcf), "log_file": str(log_file)}
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, CLIExecutionError):
            message = f"{exc}\n\n--- Log terakhir ---\n{''.join(exc.last_lines).strip()}"
        _mark_job(job_id, status=JobStatus.FAILED, error=message)
        logger.exception("Imputasi gagal untuk job=%s", job_id)
        raise


# Compatibility alias for code that still imports the former task symbol.
task_imputation = run_imputation
