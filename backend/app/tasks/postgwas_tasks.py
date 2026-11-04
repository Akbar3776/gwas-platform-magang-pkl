"""Celery task for Post-GWAS Significance Thresholds & Clumping (Fase 4E & 4F)."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.schemas.pipeline import ClumpConfig, ThresholdConfig
from app.services.postgwas_service import (
    calculate_unclamped_lambda_gc,
    determine_thresholds,
    run_greedy_clumping,
)
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
from app.utils.process import CLIExecutionError, SubprocessFailed, run_cli_command

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.postgwas_tasks.run_postgwas_analysis",
    queue="gwas",
    max_retries=0,
)
def run_postgwas_analysis(
    self,
    gwas_result: dict[str, Any],
    job_id: str,
    dataset_id: str,
    threshold_params: dict[str, Any] | None = None,
    clump_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Menjalankan kalkulasi pasca-GWAS:
    - Estimasi Genomic Inflation (Lambda GC) tanpa clamping.
    - Penentuan ambang signifikansi dinamis (Bonferroni, FDR, LOD, Suggestive).
    - Clumping varian signifikan untuk mengekstrak Lead SNPs independen.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(gwas_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        workspace = stage_dir(job_id, "postgwas")
        log_file = workspace / "postgwas.log"

        # Resolusi file asosiasi ternormalisasi kanonik
        norm_assoc_path = ctx.get("normalized_assoc_path")
        if not norm_assoc_path or not Path(norm_assoc_path).is_file():
            candidate_norm = workspace.parent / "gwas" / "gwas_results.normalized.tsv"
            if candidate_norm.is_file():
                norm_assoc_path = str(candidate_norm)
            else:
                candidate_raw = workspace.parent / "gwas" / "output" / "association.assoc.txt"
                if candidate_raw.is_file():
                    from app.services.assoc_normalizer import normalize_association_results
                    norm_assoc_path = str(candidate_norm)
                    normalize_association_results(candidate_raw, norm_assoc_path)
                else:
                    raise FileNotFoundError(f"File hasil asosiasi GWAS tidak ditemukan di {candidate_norm}")

        # Konfigurasi threshold & clumping
        raw_thresh = (job.parameters or {}).get("threshold") or threshold_params or {}
        thresh_cfg = ThresholdConfig(**raw_thresh) if isinstance(raw_thresh, dict) else raw_thresh

        raw_clump = (job.parameters or {}).get("clump") or clump_params or {}
        clump_cfg = ClumpConfig(**raw_clump) if isinstance(raw_clump, dict) else raw_clump

        # Jarak clumping default memakai ld_decay_kb dari tahap structure bila tidak diisi manual
        clump_kb = clump_cfg.kb or ctx.get("ld_decay_kb") or (job.parameters or {}).get("chosen_ld_decay_kb") or 100

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_POSTGWAS,
            current_step=PipelineStep.POSTGWAS,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 8: PASCA-GWAS & CLUMPING ===\n"
            f"Waktu mulai        : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Input Asosiasi     : {norm_assoc_path}\n"
            f"Metode Ambang      : {thresh_cfg.method.upper()} (alpha={thresh_cfg.alpha}, LOD={thresh_cfg.lod_score})\n"
            f"Jarak Clumping     : {clump_kb} kb (r2={clump_cfg.r2}, p2={clump_cfg.p2})\n"
        )

        # 1. Kumpulkan seluruh nilai p-values secara efisien
        p_values: list[float] = []
        with open(norm_assoc_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    pv = float(row["p"])
                    if 0 < pv <= 1.0:
                        p_values.append(pv)
                except (ValueError, KeyError):
                    continue

        n_tests = len(p_values)
        append_log(log_file, f"Membaca {n_tests} uji asosiasi varian.")

        # 2. Hitung lambda_GC murni
        lambda_gc = calculate_unclamped_lambda_gc(p_values)
        append_log(log_file, f"Genomic Inflation Factor (Lambda GC): {lambda_gc}")

        # 3. Tentukan ambang signifikansi
        thresholds = determine_thresholds(
            p_values=p_values,
            n_tests=n_tests,
            method=thresh_cfg.method,
            alpha=thresh_cfg.alpha,
            lod_score=thresh_cfg.lod_score,
            suggestive_p=thresh_cfg.suggestive_p,
        )
        thresholds_file = workspace / "thresholds.json"
        write_json(thresholds_file, thresholds)

        fdr_val = thresholds.get("fdr")
        fdr_str = f"{fdr_val:.3e}" if fdr_val is not None else "Tidak ada SNP lolos"

        append_log(
            log_file,
            f"Ambang Batas:\n"
            f"- Bonferroni (0.05/M) : {thresholds['bonferroni']:.3e} (-log10: {-math.log10(thresholds['bonferroni']):.2f})\n"
            f"- Suggestive          : {thresholds['suggestive']:.3e} (-log10: {-math.log10(thresholds['suggestive']):.2f})\n"
            f"- FDR (BH-q < 0.05)   : {fdr_str}\n"
            f"- LOD Score ({thresh_cfg.lod_score})     : {thresholds['lod_p']:.3e}\n"
            f"- Ambang Terpilih     : {thresholds['selected_p']:.3e} ({thresholds['selected_method'].upper()})\n"
        )
        if thresholds.get("warning"):
            append_log(log_file, f"[WARNING] {thresholds['warning']}")

        # 4. Clumping untuk identifikasi Lead SNPs
        append_log(log_file, "Menjalankan Clumping untuk memetakan Lead SNPs independen...")
        selected_p = float(thresholds["selected_p"])
        suggestive_p = float(thresholds.get("suggestive", 1e-5))
        min_p = float(thresholds.get("min_p") or (min(p_values) if p_values else 1.0))
        clump_p2_val = float(clump_cfg.p2)

        # Gunakan ambang clumping terluas dari selected, suggestive, dan clump_p2
        # agar varian yang lolos Suggestive/LOD/FDR tetap teridentifikasi sebagai lead SNP
        effective_clump_p1 = max(selected_p, suggestive_p, clump_p2_val, 1e-4)
        if min_p > effective_clump_p1 and min_p < 0.05:
            effective_clump_p1 = min(0.01, min_p * 2.0)

        # Gunakan PLINK 2 --clump jika plink2 tersedia, atau greedy fallback
        bfile_prefix = ctx.get("genotype_bfile_prefix") or ctx.get("qc_bfile_prefix")
        ran_plink_clump = False
        lead_snps: list[dict[str, Any]] = []

        plink2_bin = which_tool("plink2")
        if plink2_bin and bfile_prefix and Path(f"{bfile_prefix}.bed").is_file():
            clump_cmd = [
                plink2_bin,
                "--bfile", bfile_prefix,
                "--allow-extra-chr",
                "--clump", norm_assoc_path,
                "--clump-id-field", "rs",
                "--clump-p-field", "p",
                "--clump-p1", str(effective_clump_p1),
                "--clump-p2", str(clump_cfg.p2),
                "--clump-r2", str(clump_cfg.r2),
                "--clump-kb", str(clump_kb),
                "--out", str(workspace / "clumped"),
            ]
            try:
                append_log(log_file, f"Menjalankan PLINK2 Clumping: {' '.join(clump_cmd)}")
                run_cli_command(clump_cmd, cwd=workspace, log_file=log_file, timeout=1800)
                clumps_output = workspace / "clumped.clumps"
                if clumps_output.is_file():
                    # Parse .clumps file (kompatibel PLINK 2 & PLINK 1.9)
                    with clumps_output.open("r", encoding="utf-8") as cf:
                        creader = csv.DictReader(cf, delimiter="\t")
                        for crow in creader:
                            try:
                                s_id = crow.get("ID") or crow.get("SNP")
                                s_chr = crow.get("#CHROM") or crow.get("CHR")
                                s_pos = crow.get("POS") or crow.get("BP")
                                s_p = crow.get("P")
                                if not s_id or s_chr is None or s_pos is None or s_p is None:
                                    continue
                                lead_snps.append({
                                    "snp": str(s_id),
                                    "chr": str(s_chr).lstrip("chrCHR"),
                                    "pos": int(s_pos),
                                    "p": float(s_p),
                                    "beta": 0.0,
                                    "se": 0.0,
                                    "n_clumped": int(crow.get("TOTAL", 0)),
                                    "clumped_snps": [x.strip() for x in str(crow.get("SP2", "")).split(",") if x.strip()],
                                    "is_candidate": float(s_p) > selected_p,
                                })
                            except (ValueError, KeyError):
                                continue
                    if lead_snps:
                        ran_plink_clump = True
            except Exception as exc:
                logger.warning("PLINK2 Clump gagal, beralih ke greedy fallback: %s", exc)
                append_log(log_file, f"[FALLBACK] PLINK2 Clump gagal ({exc}), menjalankan greedy clumping internal.")

        if ran_plink_clump and lead_snps and Path(norm_assoc_path).is_file():
            lead_ids = {s["snp"] for s in lead_snps if s.get("snp")}
            stats_lookup: dict[str, dict[str, Any]] = {}
            with Path(norm_assoc_path).open("r", encoding="utf-8") as nf:
                for row in csv.DictReader(nf, delimiter="\t"):
                    rs_id = row.get("rs")
                    if rs_id in lead_ids:
                        stats_lookup[rs_id] = row
            for s in lead_snps:
                info = stats_lookup.get(s["snp"])
                if info:
                    s["a1"] = info.get("a1", "A")
                    s["a0"] = info.get("a0", "G")
                    try:
                        s["beta"] = float(info.get("beta") or 0.0)
                        s["se"] = float(info.get("se") or 0.0)
                    except (ValueError, TypeError):
                        pass

        if not ran_plink_clump or len(lead_snps) == 0:
            lead_snps = run_greedy_clumping(
                assoc_file=norm_assoc_path,
                selected_p=selected_p,
                clump_p2=clump_cfg.p2,
                clump_kb=clump_kb,
                clump_r2=clump_cfg.r2,
            )

        lead_snps_payload = {"count": len(lead_snps), "snps": lead_snps}
        lead_snps_file = workspace / "lead_snps.json"
        write_json(lead_snps_file, lead_snps_payload)

        # Tulis lead_snps.tsv untuk ekspor/unduhan
        lead_snps_tsv = workspace / "lead_snps.tsv"
        with lead_snps_tsv.open("w", encoding="utf-8", newline="") as lsf:
            writer = csv.writer(lsf, delimiter="\t")
            writer.writerow(["snp", "chr", "pos", "a1", "a0", "p", "beta", "se", "n_clumped"])
            for s in lead_snps:
                writer.writerow([
                    s.get("snp"),
                    s.get("chr"),
                    s.get("pos"),
                    s.get("a1", "A"),
                    s.get("a0", "G"),
                    s.get("p"),
                    s.get("beta", 0.0),
                    s.get("se", 0.0),
                    s.get("n_clumped", 0),
                ])

        n_sig_lead = sum(1 for s in lead_snps if float(s.get("p", 1.0)) <= selected_p)
        append_log(
            log_file,
            f"Clumping selesai: {len(lead_snps)} Lead SNP teridentifikasi ({n_sig_lead} signifikan pada ambang terpilih).\n"
        )
        if len(lead_snps) == 0:
            append_log(
                log_file,
                "[INFO] Tidak ada SNP yang melewati ambang signifikansi terpilih.\n"
                "Tahap post-GWAS selesai normal tanpa penemuan lead SNP.",
            )

        # 5. Buat Ringkasan Puncak GWAS (Perbandingan Single vs Multi-Locus)
        from app.tasks.multilocus_tasks import generate_peak_comparison

        peak_csv = workspace / "Ringkasan_Puncak_GWAS.csv"
        peak_json = workspace / "peak_comparison.json"
        multi_leads = ctx.get("multilocus_lead_snps") or []
        if not multi_leads:
            ml_summary_f = workspace.parent / "multilocus" / "multilocus_summary.json"
            if ml_summary_f.is_file():
                try:
                    ml_data = json.loads(ml_summary_f.read_text(encoding="utf-8"))
                    multi_leads = ml_data.get("lead_snps") or []
                except Exception:
                    multi_leads = []

        if not multi_leads:
            ml_peak_f = workspace.parent / "multilocus" / "peak_comparison.json"
            if ml_peak_f.is_file():
                try:
                    prev_peak = json.loads(ml_peak_f.read_text(encoding="utf-8"))
                    for mo in prev_peak.get("multi_only", []):
                        multi_leads.append({
                            "snp": mo.get("snp"),
                            "chr": mo.get("chr"),
                            "pos": mo.get("pos"),
                            "p": mo.get("p_multi"),
                            "method": mo.get("method"),
                        })
                except Exception:
                    pass

        if not multi_leads:
            ml_dir = workspace.parent / "multilocus"
            for method_name in ["farmcpu", "blink", "mlmm"]:
                m_tsv = ml_dir / f"{method_name}_results.tsv"
                if m_tsv.is_file():
                    try:
                        with m_tsv.open("r", encoding="utf-8") as tf:
                            t_reader = csv.DictReader(tf, delimiter="\t")
                            rows = sorted(t_reader, key=lambda x: float(x.get("p", 1.0)))[:5]
                            for r in rows:
                                multi_leads.append({
                                    "snp": r.get("rs"),
                                    "chr": str(r.get("chr", "")).lstrip("chrCHR"),
                                    "pos": int(r.get("pos", 0)),
                                    "p": float(r.get("p", 1.0)),
                                    "method": method_name.upper(),
                                })
                    except Exception:
                        pass

        peak_data = generate_peak_comparison(
            single_lead_snps=lead_snps,
            multi_lead_snps=multi_leads,
            output_csv=peak_csv,
            output_json=peak_json,
            window_kb=int(clump_kb),
        )

        # Sinkronkan juga ke direktori multilocus bila folder tersedia dan peak_data valid
        ml_dir = workspace.parent / "multilocus"
        if ml_dir.is_dir():
            ml_peak_target = ml_dir / "peak_comparison.json"
            if not ml_peak_target.is_file() or peak_data.get("summary", {}).get("total_peaks", 0) > 0:
                try:
                    shutil.copyfile(peak_json, ml_peak_target)
                except Exception:
                    pass
        ml_dir = workspace.parent / "multilocus"
        if ml_dir.is_dir():
            try:
                shutil.copyfile(peak_json, ml_dir / "peak_comparison.json")
            except Exception:
                pass

        # 6. Upsert Results ke database
        upsert_result(db, job_id, ResultType.THRESHOLDS, str(thresholds_file), thresholds)
        upsert_result(db, job_id, ResultType.LEAD_SNPS, str(lead_snps_file), {"count": len(lead_snps), "snps": lead_snps})
        upsert_result(db, job_id, ResultType.PEAK_COMPARISON, str(peak_json), peak_data)

        # Update parameter job & transisi ke tahap Visualisasi
        job.parameters = {
            **(job.parameters or {}),
            "lambda_gc": lambda_gc,
            "thresholds": thresholds,
            "n_lead_snps": len(lead_snps),
        }
        job.overall_status = JobStatus.RUNNING_PARSING
        job.current_step = PipelineStep.VISUALIZATION
        db.commit()

        return {
            **ctx,
            "status": "completed",
            "lambda_gc": lambda_gc,
            "thresholds": thresholds,
            "lead_snps": lead_snps,
            "lead_snps_path": str(lead_snps_tsv),
            "lead_snps_json_path": str(lead_snps_file),
            "thresholds_json_path": str(thresholds_file),
        }

    except JobCancelledError:
        logger.info("Post-GWAS dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(
                db,
                job,
                overall_status=JobStatus.FAILED,
                failed_step="postgwas",
                error_message=str(exc),
            )
        logger.exception("Post-GWAS gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()

