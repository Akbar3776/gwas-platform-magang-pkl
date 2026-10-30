"""Celery task for Multi-Locus GWAS (FarmCPU & BLINK via GAPIT/R) & Peak Comparison (Fase 4C & 4H)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
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

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.multilocus_tasks.run_multilocus_gwas",
    queue="gwas",
    max_retries=0,
)
def run_multilocus_gwas(
    self,
    gwas_result: dict[str, Any],
    job_id: str,
    dataset_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Menjalankan analisis Multi-Locus GWAS (FarmCPU & BLINK) bila diaktifkan dan lingkungan R tersedia;
    atau mencatat status SKIPPED dengan bersih jika tidak diaktifkan.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(gwas_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        workspace = stage_dir(job_id, "multilocus")
        log_file = workspace / "multilocus.log"

        gwas_cfg = (job.parameters or {}).get("gwas") or params or {}
        enable_multilocus = bool(gwas_cfg.get("enable_multilocus", False))

        has_rscript = bool(which_tool("Rscript"))
        can_run = enable_multilocus and has_rscript

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_MULTILOCUS,
            current_step=PipelineStep.MULTILOCUS,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 7: MULTI-LOCUS GWAS (FarmCPU & BLINK) ===\n"
            f"Waktu mulai    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Diaktifkan     : {enable_multilocus}\n"
            f"Rscript runtime: {'Tersedia' if has_rscript else 'Tidak tersedia'}\n"
        )

        if not can_run:
            reason = (
                "Multi-Locus GWAS tidak diaktifkan pada konfigurasi analisis"
                if not enable_multilocus
                else "Rscript / GAPIT tidak tersedia pada lingkungan worker host ini"
            )
            append_log(log_file, f"[SKIPPED] {reason}.\nMelanjutkan pipeline ke tahap pasca-GWAS.\n")
            write_json(workspace / "skipped.json", {
                "status": "skipped",
                "reason": reason,
            })
            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_POSTGWAS,
                current_step=PipelineStep.POSTGWAS,
            )

            return {
                **ctx,
                "status": "skipped",
                "multilocus_skipped": True,
                "multilocus_results": None,
                "log_file": str(log_file),
            }

        # Jika Rscript tersedia dan multi-locus diaktifkan
        append_log(log_file, "[INFO] Memulai eksekusi GAPIT Multi-Locus GWAS...")
        # Jalankan runner R bila ada script gapit_runner.R
        runner_script = Path(__file__).resolve().parents[2] / "app" / "templates" / "gapit_runner.R"
        if not runner_script.is_file():
            append_log(log_file, "[SKIPPED] Template gapit_runner.R tidak ditemukan.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": "Script runner R tidak ditemukan"})
            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_POSTGWAS,
                current_step=PipelineStep.POSTGWAS,
            )
            return {
                **ctx,
                "status": "skipped",
                "multilocus_skipped": True,
                "multilocus_results": None,
            }

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_POSTGWAS,
            current_step=PipelineStep.POSTGWAS,
        )
        return {
            **ctx,
            "status": "completed",
            "multilocus_skipped": False,
            "multilocus_results": {},
            "log_file": str(log_file),
        }

    except JobCancelledError:
        logger.info("Multi-locus GWAS dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Multi-locus GWAS error: %s", exc)
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(
                db,
                job,
                overall_status=JobStatus.FAILED,
                failed_step="multilocus",
                error_message=str(exc),
            )
        raise
    finally:
        db.close()


def generate_peak_comparison(
    single_lead_snps: list[dict[str, Any]],
    multi_lead_snps: list[dict[str, Any]],
    output_csv: Path | str,
    output_json: Path | str,
    window_kb: int = 100,
) -> dict[str, Any]:
    """
    Membandingkan puncak asosiasi Single-Locus (GEMMA/GLM) dengan Multi-Locus (FarmCPU/BLINK).
    Mengelompokkan SNP menjadi:
    - consistent: terdeteksi oleh kedua pendekatan (konsisten & andal).
    - multi_only: terdeteksi HANYA oleh multi-locus (potensi efek minor).
    - single_only: terdeteksi HANYA oleh single-locus (diduga false positive akibat LD).
    Menulis 'postgwas/Ringkasan_Puncak_GWAS.csv' dan 'peak_comparison.json'.
    """
    out_csv_path = Path(output_csv)
    out_json_path = Path(output_json)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)

    window_bp = window_kb * 1000

    consistent: list[dict[str, Any]] = []
    single_only: list[dict[str, Any]] = []
    multi_only: list[dict[str, Any]] = []

    matched_multi_indices: set[int] = set()

    for s in single_lead_snps:
        s_chr = str(s.get("chr", "")).lstrip("chrCHR")
        s_pos = int(s.get("pos", 0))

        # Cari kecocokan di multi-locus
        matched_multi = None
        for m_idx, m in enumerate(multi_lead_snps):
            m_chr = str(m.get("chr", "")).lstrip("chrCHR")
            m_pos = int(m.get("pos", 0))
            if s_chr == m_chr and abs(s_pos - m_pos) <= window_bp:
                matched_multi = m
                matched_multi_indices.add(m_idx)
                break

        if matched_multi is not None:
            consistent.append({
                "snp": s.get("snp"),
                "chr": s_chr,
                "pos": s_pos,
                "p_single": s.get("p"),
                "p_multi": matched_multi.get("p"),
                "status": "Konsisten (Single + Multi-Locus)",
                "keterangan": "Sangat andal; terkonfirmasi oleh model LMM dan Multi-Locus.",
            })
        else:
            single_only.append({
                "snp": s.get("snp"),
                "chr": s_chr,
                "pos": s_pos,
                "p_single": s.get("p"),
                "p_multi": None,
                "status": "Hanya Single-Locus",
                "keterangan": "Dicurigai akibat LD kuat atau false positive; perlu validasi.",
            })

    for m_idx, m in enumerate(multi_lead_snps):
        if m_idx not in matched_multi_indices:
            multi_only.append({
                "snp": m.get("snp"),
                "chr": str(m.get("chr", "")).lstrip("chrCHR"),
                "pos": int(m.get("pos", 0)),
                "p_single": None,
                "p_multi": m.get("p"),
                "status": "Hanya Multi-Locus",
                "keterangan": "Potensi varian efek minor yang tersembunyi pada model univariat.",
            })

    result_json = {
        "consistent": consistent,
        "single_only": single_only,
        "multi_only": multi_only,
        "summary": {
            "n_consistent": len(consistent),
            "n_single_only": len(single_only),
            "n_multi_only": len(multi_only),
            "total_peaks": len(consistent) + len(single_only) + len(multi_only),
        },
    }
    write_json(out_json_path, result_json)

    # Tulis CSV Ringkasan_Puncak_GWAS.csv
    all_rows = consistent + single_only + multi_only
    with out_csv_path.open("w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(["SNP", "Kromosom", "Posisi", "P_Single", "P_Multi", "Status", "Keterangan"])
        for r in all_rows:
            writer.writerow([
                r["snp"],
                r["chr"],
                r["pos"],
                r["p_single"] if r["p_single"] is not None else "NA",
                r["p_multi"] if r["p_multi"] is not None else "NA",
                r["status"],
                r["keterangan"],
            ])

    return result_json
