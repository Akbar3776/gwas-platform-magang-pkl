"""Celery task for validating and normalizing uploaded VCF files."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep
from app.services.chromosome_service import (
    load_chromosome_summary,
    save_split_map,
    split_map_path,
    split_overflow_chromosomes,
    summarize_chromosomes,
)
from app.tasks._common import (
    JobCancelledError,
    ensure_not_cancelled,
    guard_status_change,
    is_job_cancelled,
    mark_job,
    which_tool,
    write_json,
    write_stage_summary,
)
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLICancelledError, CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)
NORMALIZATION_TIMEOUT_SECONDS = int(os.getenv("NORMALIZATION_TIMEOUT_SECONDS", str(2 * 60 * 60)))


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, CLIExecutionError):
        tail = "".join(exc.last_lines).strip()
        return f"Eksekusi bcftools gagal: {tail}" if tail else f"Eksekusi bcftools gagal: {exc}"
    return str(exc)


def _index_normalized_vcf(
    bcftools_bin: str,
    output_path: Path,
    workspace: Path,
    log_path: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Index VCF using TBI, with automatic fallback to CSI for large chromosomes (>512Mb)."""
    try:
        run_cli_command(
            [bcftools_bin, "index", "-t", str(output_path)],
            cwd=workspace,
            log_file=log_path,
            timeout=600,
            cancel_check=cancel_check,
        )
    except (JobCancelledError, CLICancelledError):
        raise
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
                cancel_check=cancel_check,
            )
        except (JobCancelledError, CLICancelledError):
            raise
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
    run_id: str | None = None,
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
            ensure_not_cancelled(job_id, db, run_id=run_id)
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
            guard_status_change(db, job.id, JobStatus.RUNNING_NORMALIZATION)
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

        split_vcf_path = workspace / "split_input.vcf"
        sorted_path = workspace / "sorted.vcf.gz"
        map_file = split_map_path(job_id)
        overflow_stats: dict[str, Any] = {
            "total_variants": 0,
            "kept_variants": 0,
            "overflow_removed": 0,
            "invalid_pos_removed": 0,
            "split_applied": False,
            "overflow_chromosomes": [],
            "max_pos": int(settings.CHROM_SPLIT_THRESHOLD_BP),
        }

        def _cancel_check() -> bool:
            return is_job_cancelled(job_id, run_id=run_id)

        try:
            # Header-only read validates that bcftools recognizes the input as VCF/BCF.
            run_cli_command(
                [bcftools_bin, "view", "--no-version", "-h", str(input_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=NORMALIZATION_TIMEOUT_SECONDS,
                cancel_check=_cancel_check,
            )

            insp_summary_file = workspace.parent / "inspection" / "chromosome_characteristics_summary.txt"
            insp_summary = load_chromosome_summary(insp_summary_file)
            if insp_summary is None:
                insp_summary = summarize_chromosomes(input_path)

            split_result = split_overflow_chromosomes(
                input_path,
                split_vcf_path,
                summary=insp_summary,
            )
            overflow_stats = {
                "total_variants": split_result["total_variants"],
                "kept_variants": split_result["kept_variants"],
                "overflow_removed": 0,
                "invalid_pos_removed": split_result["invalid_pos_removed"],
                "split_applied": split_result["split_applied"],
                "overflow_chromosomes": split_result["overflow_chromosomes"],
                "threshold_bp": split_result["threshold_bp"],
                "unit_bp": split_result["unit_bp"],
                "max_pos": split_result["threshold_bp"],
            }
            write_json(workspace / "qc_overflow_stats.json", overflow_stats)
            split_desc = (
                ", ".join(split_result["overflow_chromosomes"])
                if split_result["overflow_chromosomes"]
                else "tidak ada"
            )
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[Fase 0 - Pemecahan Kromosom] threshold={split_result['threshold_bp']:,} bp, "
                    f"unit={split_result['unit_bp']:,} bp, dipecah={split_desc}\n"
                    f"[Fase 0 - Pemecahan Kromosom] Selesai: total={split_result['total_variants']} varian, "
                    f"dipertahankan={split_result['kept_variants']} varian, "
                    f"invalid POS<=0 disaring={split_result['invalid_pos_removed']} varian, "
                    f"overflow dibuang=0.\n"
                )

            run_cli_command(
                [bcftools_bin, "sort", "-Oz", "-o", str(sorted_path), str(split_vcf_path)],
                cwd=workspace,
                log_file=log_path,
                timeout=NORMALIZATION_TIMEOUT_SECONDS,
                cancel_check=_cancel_check,
            )
            split_vcf_path.unlink(missing_ok=True)
            sorted_path.replace(output_path)

            if output_path.is_file():
                _index_normalized_vcf(
                    bcftools_bin,
                    output_path,
                    workspace,
                    log_path,
                    cancel_check=_cancel_check,
                )
            ensure_not_cancelled(job_id, run_id=run_id)
            if not output_path.is_file():
                raise RuntimeError("Normalisasi selesai tetapi file VCF hasil normalisasi tidak ditemukan")
            saved_map_path = save_split_map(job_id, split_result["split_map"])
        except (JobCancelledError, CLICancelledError):
            split_vcf_path.unlink(missing_ok=True)
            sorted_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            map_file.unlink(missing_ok=True)
            raise
        except Exception as cli_exc:
            split_vcf_path.unlink(missing_ok=True)
            sorted_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            map_file.unlink(missing_ok=True)
            logger.error("bcftools gagal memproses VCF (%s); menghentikan pipeline.", cli_exc)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"[ERROR] bcftools gagal dieksekusi ({cli_exc}); proses dihentikan tanpa fallback Python.\n"
                )
            raise

        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"[SUCCESS] Normalisasi VCF selesai ({output_path.stat().st_size} bytes).\n"
            )

        chrom_split_meta = {
            "applied": bool(split_result["split_applied"]),
            "overflow_chromosomes": list(split_result["overflow_chromosomes"]),
            "invalid_pos_removed": int(split_result["invalid_pos_removed"]),
            "threshold_bp": int(split_result["threshold_bp"]),
            "unit_bp": int(split_result["unit_bp"]),
            "split_map_path": str(saved_map_path),
        }

        # Keep job status as RUNNING_QC so the frontend polling hook knows the pipeline is still active.
        mark_job(
            job_id,
            status=JobStatus.RUNNING_QC,
            current_step=PipelineStep.QC,
            session_factory=SessionLocal,
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
                    "qc_overflow_removed": 0,
                    "qc_overflow_stats": overflow_stats,
                    "chrom_split": chrom_split_meta,
                }
            write_stage_summary(
                job_id,
                "normalization",
                {
                    **chrom_split_meta,
                    "status": "completed",
                    "counts": {
                        "variants_in": int(split_result["total_variants"]),
                        "variants_out": int(split_result["kept_variants"]),
                        "invalid_pos_removed": int(split_result["invalid_pos_removed"]),
                        "overflow_chromosomes": len(split_result["overflow_chromosomes"]),
                    },
                },
                db=db,
            )
            db.commit()
        finally:
            db.close()
        return {
            **ctx,
            "job_id": job_id,
            "dataset_id": dataset_id,
            "normalized_vcf_path": str(output_path),
            "split_map_path": str(saved_map_path),
            "chrom_split": chrom_split_meta,
            "qc_overflow_removed": 0,
            "qc_overflow_stats": overflow_stats,
            "log_file": str(log_path),
        }
    except (JobCancelledError, CLICancelledError):
        logger.info("Normalisasi VCF dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        message = _friendly_error(exc)
        mark_job(
            job_id,
            status=JobStatus.FAILED,
            failed_step="normalization",
            error=message,
            session_factory=SessionLocal,
        )
        logger.exception("Normalisasi VCF gagal untuk job=%s", job_id)
        raise RuntimeError(message) from exc
