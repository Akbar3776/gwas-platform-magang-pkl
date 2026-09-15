"""Celery task for GEMMA linear and linear mixed model GWAS analysis."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.utils.log import get_task_logger

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep
from app.tasks.celery_app import celery_app
from app.utils.process import CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)
GEMMA_BINARY = os.getenv("GEMMA_BINARY_PATH", "gemma")
PLINK_BINARY = os.getenv("PLINK_BINARY_PATH", "plink")
GEMMA_TIMEOUT_SECONDS = int(os.getenv("GEMMA_TIMEOUT_SECONDS", str(6 * 60 * 60)))


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


def _bfile_prefix(genotype_path: str, work_dir: Path) -> str:
    path = Path(genotype_path)
    if path.suffix == ".bed":
        prefix = str(path.with_suffix(""))
        required = [Path(f"{prefix}{suffix}") for suffix in (".bed", ".bim", ".fam")]
        missing = [str(item) for item in required if not item.is_file()]
        if missing:
            raise FileNotFoundError(f"PLINK dataset tidak lengkap: {', '.join(missing)}")
        return prefix
    if path.suffix in {".vcf", ".gz"} and (path.name.endswith(".vcf") or path.name.endswith(".vcf.gz")):
        converted_prefix = work_dir / "genotypes"
        run_cli_command(
            [PLINK_BINARY, "--vcf", str(path), "--make-bed", "--out", str(converted_prefix)],
            cwd=work_dir,
            log_file=work_dir / "plink_conversion.log",
            timeout=GEMMA_TIMEOUT_SECONDS,
        )
        return str(converted_prefix)
    raise ValueError("genotype_path harus berupa .vcf/.vcf.gz atau prefix/file .bed dengan .bim dan .fam")


@celery_app.task(
    bind=True,
    name="app.tasks.gemma_tasks.run_gemma_gwas",
    queue="gwas",
    max_retries=0,
    acks_late=True,
    soft_time_limit=GEMMA_TIMEOUT_SECONDS + 30,
    time_limit=GEMMA_TIMEOUT_SECONDS + 120,
)
def run_gemma_gwas(
    self,
    imputation_result: dict[str, str],
    job_id: str,
    phenotype_path: str,
    model: str = "lmm",
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Run GEMMA kinship and association analysis for an imputed dataset."""
    try:
        params = params or {}
        genotype_path = imputation_result.get("output_vcf")
        if not genotype_path:
            raise ValueError("Hasil imputasi tidak memiliki output_vcf")
        output_dir = params.get("output_dir")
        if model not in {"lmm", "lm"}:
            raise ValueError("model harus bernilai 'lmm' atau 'lm'")
        work_dir = Path(output_dir or Path(settings.STORAGE_ROOT) / "jobs" / job_id / "gwas")
        log_file = work_dir / "gemma.log"
        if not Path(phenotype_path).is_file():
            raise FileNotFoundError(f"File fenotip tidak ditemukan: {phenotype_path}")
        work_dir.mkdir(parents=True, exist_ok=True)
        bfile_prefix = _bfile_prefix(genotype_path, work_dir)

        _mark_job(
            job_id,
            status=JobStatus.RUNNING_GWAS,
            current_step=PipelineStep.GWAS,
            started_at=datetime.now(timezone.utc),
            work_dir=str(work_dir),
            error_message=None,
            error_log=None,
        )
        output_dir_gemma = work_dir / "output"
        output_dir_gemma.mkdir(parents=True, exist_ok=True)
        kinship_command = [GEMMA_BINARY, "-bfile", bfile_prefix, "-gk", "1", "-outdir", str(output_dir_gemma), "-o", "kinship"]
        run_cli_command(kinship_command, cwd=work_dir, log_file=log_file, timeout=GEMMA_TIMEOUT_SECONDS)
        kinship_path = output_dir_gemma / "kinship.cXX.txt"
        if not kinship_path.is_file():
            raise RuntimeError(f"Matriks kinship tidak ditemukan: {kinship_path}")

        association_command = [
            GEMMA_BINARY,
            "-bfile", bfile_prefix,
            "-p", phenotype_path,
            "-outdir", str(output_dir_gemma),
            "-o", "association",
            "-lmm" if model == "lmm" else "-lm",
            "4" if model == "lmm" else "1",
        ]
        if model == "lmm":
            association_command.extend(["-k", str(kinship_path)])
        run_cli_command(association_command, cwd=work_dir, log_file=log_file, timeout=GEMMA_TIMEOUT_SECONDS)
        assoc_path = output_dir_gemma / "association.assoc.txt"
        if not assoc_path.is_file():
            raise RuntimeError(f"Output association GEMMA tidak ditemukan: {assoc_path}")

        _mark_job(
            job_id,
            status=JobStatus.COMPLETED,
            current_step=PipelineStep.VISUALIZATION,
            completed_at=datetime.now(timezone.utc),
            assoc_output_path=str(assoc_path),
        )
        return {"job_id": job_id, "assoc_output_path": str(assoc_path), "log_file": str(log_file)}
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, CLIExecutionError):
            message = f"{exc}\n\n--- Log terakhir ---\n{''.join(exc.last_lines).strip()}"
        _mark_job(job_id, status=JobStatus.FAILED, error=message)
        logger.exception("GEMMA GWAS gagal untuk job=%s", job_id)
        raise


# Compatibility alias for callers using the former task name.
run_gemma_association = run_gemma_gwas
