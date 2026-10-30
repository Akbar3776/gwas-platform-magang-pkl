"""Celery task for memory-bounded Beagle imputation (Fase Imputation)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.resource_limits import (
    build_java_command,
    make_memory_limited_preexec_fn,
    resolve_rlimit_as_bytes,
)
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    which_tool,
    write_json,
)
from app.tasks.celery_app import celery_app, get_task_logger
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


def _beagle_command(
    qc_vcf_path: str,
    reference_vcf_path: str | None,
    output_prefix: str,
    params: dict[str, Any],
):
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
        f"out={output_prefix}",
        f"nthreads={nthreads}",
    ])
    if reference_vcf_path:
        command.append(f"ref={reference_vcf_path}")
    if params.get("chromosome"):
        command.append(f"chrom={params['chromosome']}")
    if params.get("map_path"):
        command.append(f"map={params['map_path']}")
    return command, rlimit


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
    reference_vcf_path: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run Beagle imputation with explicit JVM and memory bounds.
    Supports mode="none" (honest skip), mode="reference", and mode="reference_free".
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(qc_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        qc_vcf_path = ctx.get("qc_vcf_path")
        qc_bfile_prefix = ctx.get("qc_bfile_prefix") or ctx.get("output_prefix")
        if not qc_vcf_path or not Path(qc_vcf_path).is_file():
            raise FileNotFoundError(f"VCF hasil QC tidak ditemukan: {qc_vcf_path}")

        workspace = stage_dir(job_id, "imputation")
        output_prefix = workspace / "imputed"
        log_file = workspace / "beagle.log"

        imputation_cfg = (job.parameters or {}).get("imputation") or params or {}
        mode = imputation_cfg.get("mode", "none")
        ref_vcf = imputation_cfg.get("reference_vcf_path") or reference_vcf_path

        # Mode: None -> Skip jujur tanpa menduplikasi data palsu
        if mode == "none":
            append_log(
                log_file,
                f"=== TAHAP 4: IMPUTASI GENOTIPE (Beagle 5) ===\n"
                f"Waktu        : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"[SKIPPED] Imputasi dilewati oleh pengguna (mode='none').\n"
                f"Menggunakan data genotipe hasil Quality Control secara langsung.\n"
            )
            write_json(workspace / "skipped.json", {
                "status": "skipped",
                "reason": "Imputasi tidak diminta (mode=none)",
            })

            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
                work_dir=str(workspace),
            )

            return {
                **ctx,
                "status": "skipped",
                "imputation_skipped": True,
                "output_vcf": str(qc_vcf_path),
                "genotype_vcf_path": str(qc_vcf_path),
                "genotype_bfile_prefix": str(qc_bfile_prefix) if qc_bfile_prefix else None,
                "log_file": str(log_file),
            }

        # Mode reference atau reference_free
        can_run_beagle = (
            bool(which_tool("java"))
            and Path(BEAGLE_JAR_PATH).is_file()
            and (mode != "reference" or (bool(ref_vcf) and Path(ref_vcf).is_file()))
        )

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_IMPUTATION,
            current_step=PipelineStep.IMPUTATION,
            work_dir=str(workspace),
            imputed_prefix=str(output_prefix),
        )

        command, rlimit_bytes = _beagle_command(
            qc_vcf_path,
            ref_vcf if mode == "reference" else None,
            str(output_prefix),
            imputation_cfg,
        )

        append_log(
            log_file,
            f"=== TAHAP 4: IMPUTASI GENOTIPE (Beagle 5) ===\n"
            f"Waktu mulai     : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Mode            : {mode}\n"
            f"Input QC VCF    : {qc_vcf_path}\n"
            f"Reference Panel : {ref_vcf or '(tanpa reference - reference-free)'}\n"
            f"Command         : {' '.join(command)}\n"
        )

        if can_run_beagle:
            run_cli_command(
                command,
                cwd=workspace,
                log_file=log_file,
                timeout=IMPUTATION_TIMEOUT_SECONDS,
                preexec_fn=make_memory_limited_preexec_fn(rlimit_bytes) if os.name != "nt" else None,
            )

            output_vcf = Path(f"{output_prefix}.vcf.gz")
            if not output_vcf.is_file():
                raise RuntimeError(f"Beagle selesai tetapi output tidak ditemukan: {output_vcf}")

            # Konversi balik ke PLINK biner bila plink2 tersedia
            if which_tool("plink2"):
                conv_cmd = [
                    "plink2",
                    "--vcf", str(output_vcf),
                    "--double-id",
                    "--allow-extra-chr",
                    "--make-bed",
                    "--out", str(output_prefix),
                ]
                append_log(log_file, f"Konversi balik PLINK bed: {' '.join(conv_cmd)}")
                run_cli_command(conv_cmd, cwd=workspace, log_file=log_file, timeout=IMPUTATION_TIMEOUT_SECONDS)

            append_log(log_file, f"[SUCCESS] Imputasi selesai: {output_vcf} ({output_vcf.stat().st_size} bytes).\n")
            upsert_result(db, job_id, ResultType.IMPUTED_VCF, str(output_vcf))

            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
            )

            return {
                **ctx,
                "status": "completed",
                "imputation_skipped": False,
                "output_vcf": str(output_vcf),
                "genotype_vcf_path": str(output_vcf),
                "genotype_bfile_prefix": str(output_prefix),
                "log_file": str(log_file),
            }
        else:
            # Beagle/Java atau reference panel tidak tersedia di lingkungan saat ini
            append_log(
                log_file,
                f"[SKIPPED] Beagle/Java runtime atau panel referensi tidak ditemukan di host ini.\n"
                f"Melanjutkan analisis pipeline dengan data genotipe hasil QC ({qc_vcf_path}).\n"
            )
            write_json(workspace / "skipped.json", {
                "status": "skipped",
                "reason": "Beagle JAR/Java atau reference panel tidak tersedia di host",
            })

            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
            )

            return {
                **ctx,
                "status": "skipped",
                "imputation_skipped": True,
                "output_vcf": str(qc_vcf_path),
                "genotype_vcf_path": str(qc_vcf_path),
                "genotype_bfile_prefix": str(qc_bfile_prefix) if qc_bfile_prefix else None,
                "log_file": str(log_file),
            }

    except JobCancelledError:
        logger.info("Imputasi dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        message = str(exc)
        if isinstance(exc, CLIExecutionError):
            message = f"{exc}\n\n--- Log terakhir ---\n{''.join(exc.last_lines).strip()}"
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(db, job, overall_status=JobStatus.FAILED, failed_step="imputation", error_message=message)
        logger.exception("Imputasi gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()


# Compatibility alias
task_imputation = run_imputation
