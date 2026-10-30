"""Celery task for Visualization & Manhattan/QQ Cache Generation (Fase 4G)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.services.manhattan_parser import compute_assoc_summary_stats, generate_manhattan_cache
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.visualization_tasks.run_visualization",
    queue="gwas",
    max_retries=0,
)
def run_visualization(
    self,
    prev_result: dict[str, Any],
    job_id: str,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """
    Menghasilkan cache biner Parquet untuk Manhattan & QQ plot interaktif berperforma tinggi.
    Menyematkan data ambang signifikansi dan lead SNPs ke dalam metadata visualisasi.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(prev_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        workspace = stage_dir(job_id, "visualization")
        log_file = workspace / "visualization.log"

        # Resolusi file asosiasi sumber
        assoc_path = (
            ctx.get("normalized_assoc_path")
            or ctx.get("assoc_output_path")
            or ctx.get("assoc_output")
        )
        if not assoc_path or not Path(assoc_path).is_file():
            candidate_norm = workspace.parent / "gwas" / "gwas_results.normalized.tsv"
            if candidate_norm.is_file():
                assoc_path = str(candidate_norm)
            else:
                candidate_raw = workspace.parent / "gwas" / "output" / "association.assoc.txt"
                if candidate_raw.is_file():
                    assoc_path = str(candidate_raw)
                else:
                    raise FileNotFoundError("File hasil asosiasi tidak ditemukan untuk visualisasi")

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_PARSING,
            current_step=PipelineStep.VISUALIZATION,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 9: VISUALISASI & CACHE PLOT (Manhattan & QQ) ===\n"
            f"Waktu mulai    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Sumber data    : {assoc_path}\n"
        )

        # 1. Bangun Parquet cache
        manhattan_cache_path = workspace / "manhattan_cache.parquet"
        cache_summary = generate_manhattan_cache(
            assoc_file_path=str(assoc_path),
            output_cache_path=str(manhattan_cache_path),
        )

        # 2. Hitung statistik ringkasan GWAS
        summary_stats = compute_assoc_summary_stats(str(assoc_path))
        thresholds = ctx.get("thresholds") or (job.parameters or {}).get("thresholds", {})
        lead_snps = ctx.get("lead_snps") or []

        append_log(
            log_file,
            f"[9.1] Statistik Ringkasan GWAS:\n"
            f"- Total SNP teranalisis        : {summary_stats.get('total_snps', 0)}\n"
            f"- SNP Signifikan (p < 5e-8)    : {summary_stats.get('significant_snps', 0)}\n"
            f"- Genomic Inflation (Lambda GC): {summary_stats.get('lambda_gc', 1.0)}\n"
            f"- Titik tersimpan di cache     : {cache_summary.get('kept_rows', 0)} ({cache_summary.get('output_path')})\n"
            f"[SUCCESS] Cache visualisasi siap digunakan.",
        )

        full_summary = {
            **summary_stats,
            **cache_summary,
            "thresholds": thresholds,
            "lead_snps_count": len(lead_snps),
        }

        # 3. Upsert hasil
        upsert_result(
            db,
            job_id,
            ResultType.MANHATTAN_DATA,
            str(manhattan_cache_path),
            full_summary,
        )

        job.parameters = {
            **(job.parameters or {}),
            "manhattan_cache_path": str(manhattan_cache_path),
            "summary_stats": full_summary,
        }
        job.overall_status = JobStatus.RUNNING_ANNOTATION
        job.current_step = PipelineStep.ANNOTATION
        db.commit()

        return {
            **ctx,
            "status": "completed",
            "manhattan_cache_path": str(manhattan_cache_path),
            "summary_stats": full_summary,
            "vis_log_file": str(log_file),
        }

    except JobCancelledError:
        logger.info("Visualisasi dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(
                db,
                job,
                overall_status=JobStatus.FAILED,
                failed_step="visualization",
                error_message=str(exc),
            )
        logger.exception("Visualisasi gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()
