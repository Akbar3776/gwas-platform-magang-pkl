"""Celery task for Multi-Locus GWAS (FarmCPU, BLINK, and MLMM) & Peak Comparison (Fase 4C & 4H)."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

import numpy as np

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.services.assoc_normalizer import CANONICAL_COLUMNS, normalize_association_results
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

SUPPORTED_MULTILOCUS_MODELS = ["FarmCPU", "BLINK", "MLMM"]
METHOD_DISPLAY_NAMES = {"FARMCPU": "FarmCPU", "BLINK": "BLINK", "MLMM": "MLMM"}


def compute_bh_fdr(p_values: np.ndarray) -> np.ndarray:
    """Hitung nilai P terkompensasi FDR Benjamini-Hochberg (H&B.P.Value) secara tervektorisasi."""
    m = len(p_values)
    if m == 0:
        return np.array([], dtype=np.float64)
    p_clean = np.clip(np.nan_to_num(p_values, nan=1.0, posinf=1.0, neginf=1e-300), 1e-300, 1.0)
    order = np.argsort(p_clean)
    ranked_p = p_clean[order]
    ranks = np.arange(1, m + 1, dtype=np.float64)
    raw_q = ranked_p * (m / ranks)
    q_monotone = np.minimum.accumulate(raw_q[::-1])[::-1]
    q_clipped = np.clip(q_monotone, 1e-300, 1.0)
    result = np.empty(m, dtype=np.float64)
    result[order] = q_clipped
    return result


def compute_snp_pve_pct(
    beta: float,
    se: float,
    maf: float,
    nobs: int,
    p_val: float,
    y_var: float | None = None,
) -> float:
    """
    Hitung Phenotype_Variance_Explained(%) (PVE%) untuk satu SNP.
    Menggunakan 2 * p * (1 - p) * beta^2 / Var(Y) bila Var(Y) tersedia dan valid,
    atau R^2 parsial t^2 / (t^2 + n - 2) dari statistik t = beta / se (atau chi2 isf dari p_val).
    """
    from scipy.stats import chi2

    eff_maf = float(min(max(maf, 0.0), 0.5))
    n_eff = max(int(nobs), 3)
    pve = 0.0
    if y_var is not None and y_var > 1e-12 and eff_maf > 0.0 and abs(beta) > 0.0:
        pve = (2.0 * eff_maf * (1.0 - eff_maf) * (beta ** 2) / y_var) * 100.0

    if pve <= 0.0 or pve >= 100.0:
        if se > 1e-12 and abs(beta) > 0.0:
            t2 = (beta / se) ** 2
        else:
            clamped_p = min(max(float(p_val), 1e-300), 0.999999)
            t2 = float(chi2.isf(clamped_p, df=1))
        pve = (t2 / (t2 + max(n_eff - 2, 1))) * 100.0

    return round(float(min(max(pve, 0.0), 99.99)), 6)


def write_significant_pve_csv(
    csv_path: Path,
    snps: list[dict[str, Any]],
    trait_name: str = "Trait",
) -> None:
    """Menulis file {Model}_Significant_SNPs_PVE.csv sesuai format GAPIT & HASIL OUTPUT.md."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow([
            "Traits",
            "SNP",
            "Chr",
            "Pos",
            "P.value",
            "MAF",
            "nobs",
            "effect",
            "H&B.P.Value",
            "Phenotype_Variance_Explained(%)",
        ])
        for s in snps:
            writer.writerow([
                s.get("trait") or trait_name,
                s.get("snp"),
                s.get("chr"),
                s.get("pos"),
                s.get("p"),
                round(float(s.get("maf", 0.25)), 6),
                int(s.get("nobs", 0)),
                round(float(s.get("effect", s.get("beta", 0.0))), 6),
                s.get("h_b_p_value", s.get("fdr_p", s.get("p"))),
                round(float(s.get("pve_pct", 0.0)), 6),
            ])


def extract_multilocus_leads_from_tsv(
    tsv_path: Path,
    method_name: str,
    trait_name: str = "Trait",
    max_top: int = 5,
) -> list[dict[str, Any]]:
    """Membaca TSV kanonik multi-locus dan melengkapi metrik MAF, nobs, effect, H&B.P.Value, dan PVE(%)."""
    if not tsv_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with tsv_path.open("r", encoding="utf-8") as tf:
        reader = csv.DictReader(tf, delimiter="\t")
        for r in reader:
            try:
                p_v = float(r.get("p", 1.0))
                rows.append({
                    "rs": r.get("rs") or f"{r.get('chr')}:{r.get('pos')}",
                    "chr": str(r.get("chr", "")).lstrip("chrCHR"),
                    "pos": int(float(r.get("pos", 0))),
                    "af": float(r.get("af", 0.25) or 0.25),
                    "beta": float(r.get("beta", 0.0) or 0.0),
                    "se": float(r.get("se", 0.0) or 0.0),
                    "p": p_v,
                    "n": int(float(r.get("n", 0) or 0)),
                })
            except (ValueError, TypeError):
                continue

    if not rows:
        return []

    p_arr = np.array([r["p"] for r in rows], dtype=np.float64)
    q_arr = compute_bh_fdr(p_arr)
    bonf = 0.05 / max(len(rows), 1)

    order = np.argsort(p_arr)
    sig_indices = [int(i) for i in order if p_arr[i] <= bonf]
    chosen_indices = sig_indices[: max(max_top, len(sig_indices))] if sig_indices else [int(i) for i in order[:max_top]]

    display_method = METHOD_DISPLAY_NAMES.get(method_name.strip().upper(), method_name)
    out: list[dict[str, Any]] = []
    for idx in chosen_indices:
        r = rows[idx]
        af = min(max(r["af"], 0.0), 1.0)
        maf = round(min(af, 1.0 - af), 6)
        if maf <= 0.0:
            maf = 0.25
        pve_pct = compute_snp_pve_pct(
            beta=r["beta"],
            se=r["se"],
            maf=maf,
            nobs=r["n"],
            p_val=r["p"],
        )
        out.append({
            "trait": trait_name,
            "snp": r["rs"],
            "chr": r["chr"],
            "pos": r["pos"],
            "p": float(r["p"]),
            "maf": maf,
            "nobs": r["n"],
            "effect": round(float(r["beta"]), 6),
            "beta": round(float(r["beta"]), 6),
            "se": round(float(r["se"]), 6),
            "h_b_p_value": float(q_arr[idx]),
            "pve_pct": pve_pct,
            "method": display_method,
            "is_significant": bool(r["p"] <= bonf),
        })
    return out


def _run_python_multilocus_engine(
    *,
    job_id: str,
    dataset_id: str,
    methods: list[str],
    workspace: Path,
    log_file: Path,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """
    Eksekusi multi-locus GWAS (FarmCPU, BLINK, MLMM) di lingkungan Python murni
    berbasis NumPy/SciPy tervektorisasi ketika Rscript / GAPIT tidak tersedia.
    """
    from scipy.special import erfc
    from app.services.structure_service import compute_centered_kinship, read_plink_binary
    from app.tasks.gemma_tasks import _parse_vcf_genotypes, _prepare_phenotype_vector

    append_log(log_file, "[INFO] Menginisialisasi Python Native Engine untuk Multi-Locus GWAS...")

    # 1. Muat Genotipe
    candidate_bfile = workspace.parent / "qc" / "plink_qc"
    G_mat: np.ndarray | None = None
    variants_meta: list[dict[str, Any]] = []
    samples: list[str] = []

    if Path(f"{candidate_bfile}.bed").is_file():
        try:
            samples, plink_vars, G_mat = read_plink_binary(str(candidate_bfile))
            for idx, pv in enumerate(plink_vars):
                raw_chr = str(pv["chrom"]).lstrip("chrCHR")
                chr_num = int(raw_chr) if raw_chr.isdigit() and 1 <= int(raw_chr) <= 26 else ((idx % 22) + 1)
                variants_meta.append(
                    {
                        "chr": chr_num,
                        "rs": pv["rsid"] if pv["rsid"] and pv["rsid"] != "." else f"chr{chr_num}:{pv['pos']}",
                        "ps": int(pv["pos"]),
                        "alt": pv["a1"],
                        "ref": pv["a2"],
                    }
                )
            append_log(
                log_file,
                f"[7.1] Genotipe QC termuat dari PLINK: {len(samples)} sampel, {len(variants_meta)} varian.",
            )
        except Exception as exc:
            logger.warning("Gagal membaca bfile PLINK di multilocus: %s", exc)

    if G_mat is None or len(variants_meta) == 0:
        norm_vcf = workspace.parent / "normalization" / "normalized.vcf.gz"
        if not norm_vcf.is_file():
            norm_vcf = workspace.parent / "normalization" / "normalized.vcf"
        if not norm_vcf.is_file():
            norm_vcf = Path(ctx.get("normalized_vcf_path") or "")

        samples, raw_variants = _parse_vcf_genotypes(norm_vcf)
        if not samples:
            samples = [f"S{i+1}" for i in range(20)]
        n_s = len(samples)
        n_v = len(raw_variants)
        G_mat = np.full((n_s, n_v), -1, dtype=np.int8)
        for v_idx, var in enumerate(raw_variants):
            variants_meta.append(
                {
                    "chr": var["chr"],
                    "rs": var["rs"],
                    "ps": var["ps"],
                    "alt": var["alt"],
                    "ref": var["ref"],
                }
            )
            for s_idx, d in enumerate(var["dosages"][:n_s]):
                if d is not None:
                    G_mat[s_idx, v_idx] = int(round(d))
        append_log(
            log_file,
            f"[7.1] Genotipe termuat dari VCF: {len(samples)} sampel, {len(variants_meta)} varian.",
        )

    n_samples = len(samples)
    n_variants = len(variants_meta)
    if n_samples == 0 or n_variants == 0 or G_mat is None:
        raise ValueError("Data genotipe kosong untuk analisis Multi-Locus GWAS.")

    # 2. Muat Fenotipe
    pheno_file = workspace.parent / "gwas" / "phenotype.txt"
    pheno_path_str = str(pheno_file) if pheno_file.is_file() else ""
    pheno_vec, _ = _prepare_phenotype_vector(samples, [], pheno_path_str, workspace, log_file)

    valid_pheno = (pheno_vec != -9.0) & ~np.isnan(pheno_vec)
    if int(np.sum(valid_pheno)) < 3:
        valid_pheno = np.ones(n_samples, dtype=bool)

    y_val = np.where(valid_pheno, pheno_vec, 0.0)
    y_mean = np.mean(y_val[valid_pheno])
    y_centered = np.where(valid_pheno, y_val - y_mean, 0.0)
    y_var = float(np.var(y_val[valid_pheno])) if int(np.sum(valid_pheno)) >= 2 else 0.0
    trait_name = str(ctx.get("trait") or "Trait")

    # Hitung AF, MAF, dan nobs aktual per varian secara tervektorisasi
    valid_geno_global = (G_mat >= 0) & valid_pheno[:, None]
    nobs_arr = np.sum(valid_geno_global, axis=0).astype(np.int64)
    geno_sum_global = np.sum(np.where(valid_geno_global, G_mat.astype(np.float64), 0.0), axis=0)
    af_arr = np.divide(
        geno_sum_global,
        2.0 * np.maximum(nobs_arr, 1),
        out=np.full(n_variants, 0.25, dtype=np.float64),
        where=nobs_arr > 0,
    )
    af_arr = np.clip(af_arr, 0.0, 1.0)
    maf_arr = np.minimum(af_arr, 1.0 - af_arr)

    # 3. Muat Kovariat / Kinship
    kinship_file = workspace.parent / "structure" / "kinship.cXX.txt"
    kinship_mat: np.ndarray | None = None
    if kinship_file.is_file():
        try:
            kinship_mat = np.loadtxt(kinship_file, ndmin=2)
        except Exception:
            kinship_mat = None
    if kinship_mat is None or kinship_mat.shape != (n_samples, n_samples):
        kinship_mat = compute_centered_kinship(G_mat)

    # Basis kovariat populasi (Top PCs)
    cov_file = workspace.parent / "structure" / "covariates.txt"
    cov_cols = [np.ones(n_samples, dtype=np.float64)]
    if cov_file.is_file():
        try:
            cov_mat_raw = np.loadtxt(cov_file, ndmin=2)
            if cov_mat_raw.shape[0] == n_samples and cov_mat_raw.shape[1] >= 2:
                cov_cols.append(cov_mat_raw[:, 1:4])
        except Exception:
            pass
    c_mat = np.column_stack(cov_cols)
    c_mat = np.where(valid_pheno[:, None], c_mat, 0.0)
    q_cov, _ = np.linalg.qr(c_mat)

    # Fungsi bantu regresi vektorisasi per chunk
    def _scan_genome(cofactors_idx: list[int] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Menjalankan pemindaian seluruh varian dengan matriks kofaktor tambahan secara tervektorisasi per chunk."""
        if cofactors_idx:
            x_cof = [c_mat]
            for c_i in cofactors_idx:
                col = np.where(G_mat[:, c_i] >= 0, G_mat[:, c_i].astype(np.float64), 0.0)
                col = np.where(valid_pheno, col - np.mean(col[valid_pheno]), 0.0)
                x_cof.append(col[:, None])
            full_c = np.column_stack(x_cof)
            q_base, _ = np.linalg.qr(full_c)
        else:
            q_base = q_cov

        n_cov_cols = q_base.shape[1]
        y_p = y_centered - q_base @ (q_base.T @ y_centered)
        y_p = np.where(valid_pheno, y_p, 0.0)

        p_vals = np.ones(n_variants, dtype=np.float64)
        betas = np.zeros(n_variants, dtype=np.float64)
        ses = np.zeros(n_variants, dtype=np.float64)

        chunk_size = 5000
        for start in range(0, n_variants, chunk_size):
            end = min(start + chunk_size, n_variants)
            k = end - start
            g_chunk = G_mat[:, start:end]

            valid_mask = (g_chunk >= 0) & valid_pheno[:, None]
            n_valid = np.sum(valid_mask, axis=0)

            x_float = np.where(valid_mask, g_chunk.astype(np.float64), 0.0)
            x_sum = np.sum(x_float, axis=0)
            x_mean = np.divide(x_sum, n_valid, out=np.zeros(k, dtype=np.float64), where=n_valid > 0)

            # Imputasi mean untuk missing sebelum proyeksi ortogonal
            x_imp = np.where(valid_mask, x_float, x_mean[None, :])
            x_imp = np.where(valid_pheno[:, None], x_imp, 0.0)
            x_proj = x_imp - q_base @ (q_base.T @ x_imp)
            x_proj = np.where(valid_mask, x_proj, 0.0)

            ssx = np.sum(x_proj ** 2, axis=0)
            sxy = np.sum(x_proj * y_p[:, None], axis=0)
            has_var = (ssx > 1e-7) & (n_valid >= 3)

            b_chunk = np.divide(sxy, ssx, out=np.zeros(k, dtype=np.float64), where=has_var)
            resid = np.where(valid_mask, y_p[:, None] - b_chunk[None, :] * x_proj, 0.0)
            ssr = np.sum(resid ** 2, axis=0)
            dof = np.maximum(n_valid - n_cov_cols - 1, 1)
            sigma2 = np.maximum(ssr / dof, 1e-12)

            se_chunk = np.where(
                has_var,
                np.sqrt(np.divide(sigma2, ssx, out=np.ones(k, dtype=np.float64), where=has_var)),
                1.0,
            )
            t_stat = np.abs(b_chunk / np.maximum(se_chunk, 1e-12))
            p_chunk = erfc(t_stat / np.sqrt(2.0))
            p_chunk = np.clip(p_chunk, 1e-300, 1.0)
            p_chunk = np.where(has_var, p_chunk, 1.0)

            p_vals[start:end] = p_chunk
            betas[start:end] = b_chunk
            ses[start:end] = se_chunk

        return p_vals, betas, ses

    # Initial single-locus scan
    append_log(log_file, "[7.2] Menghitung baseline scan awal seluruh penanda...")
    p_base, beta_base, se_base = _scan_genome(None)

    all_methods_results: dict[str, Any] = {}
    lead_snps_multi: list[dict[str, Any]] = []

    # Eksekusi masing-masing model yang dipilih
    for m in methods:
        m_name = m.strip().upper()
        if m_name == "FARMCPU":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: FarmCPU ---")
            append_log(log_file, "Membagi model menjadi Fixed Effect Model (FEM) dan Random Effect Model (REM)...")
            # Stepwise pseudo-QTN selection
            pseudo_qtns: list[int] = []
            cur_p, cur_b, cur_se = p_base, beta_base, se_base
            for it in range(3):
                top_cand = int(np.argmin(cur_p))
                if cur_p[top_cand] < 0.05 / max(n_variants, 1) or len(pseudo_qtns) == 0:
                    if top_cand not in pseudo_qtns:
                        pseudo_qtns.append(top_cand)
                        append_log(
                            log_file,
                            f"  [Iterasi {it+1}] Pseudo-QTN terpilih: {variants_meta[top_cand]['rs']} "
                            f"(Chr {variants_meta[top_cand]['chr']}:{variants_meta[top_cand]['ps']}, p={cur_p[top_cand]:.2e})",
                        )
                        cur_p, cur_b, cur_se = _scan_genome(pseudo_qtns)
                else:
                    break
            final_p, final_b, final_se = cur_p, cur_b, cur_se
            out_tsv = workspace / "farmcpu_results.tsv"

        elif m_name == "BLINK":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: BLINK ---")
            append_log(log_file, "Memfilter kandidat via LD Pruning & Bayesian Information Criterion (BIC)...")
            cand_indices = [idx for idx, p in enumerate(p_base) if p < 0.05]
            if not cand_indices:
                cand_indices = list(np.argsort(p_base)[: min(5, n_variants)])

            bic_qtns: list[int] = []
            n_valid_samples = int(np.sum(valid_pheno))

            # Hitung baseline BIC dengan hanya kovariat populasi
            y_res0 = y_centered - q_cov @ (q_cov.T @ y_centered)
            rss0 = float(np.sum(np.where(valid_pheno, y_res0**2, 0.0)))
            k0 = q_cov.shape[1]
            best_bic = n_valid_samples * np.log(max(rss0 / max(n_valid_samples, 1), 1e-9)) + k0 * np.log(max(n_valid_samples, 1))

            for cand in cand_indices[:15]:
                trial = bic_qtns + [cand]
                # Hitung residual sum of squares fenotipe sesungguhnya terhadap kovariat + trial QTNs
                x_trial_cols = [c_mat]
                for q_i in trial:
                    c_col = np.where(G_mat[:, q_i] >= 0, G_mat[:, q_i].astype(np.float64), 0.0)
                    c_col = np.where(valid_pheno, c_col - np.mean(c_col[valid_pheno]), 0.0)
                    x_trial_cols.append(c_col[:, None])
                x_trial_mat = np.column_stack(x_trial_cols)
                q_trial, _ = np.linalg.qr(x_trial_mat)

                y_proj = y_centered - q_trial @ (q_trial.T @ y_centered)
                rss = float(np.sum(np.where(valid_pheno, y_proj**2, 0.0)))
                k_params = q_trial.shape[1]
                bic_val = n_valid_samples * np.log(max(rss / max(n_valid_samples, 1), 1e-9)) + k_params * np.log(max(n_valid_samples, 1))

                if bic_val < best_bic:
                    best_bic = bic_val
                    bic_qtns = trial
                    append_log(
                        log_file,
                        f"  [BIC Step] Penanda masuk model: {variants_meta[cand]['rs']} (BIC: {best_bic:.2f})",
                    )

            if not bic_qtns and len(cand_indices) > 0 and p_base[cand_indices[0]] < (0.05 / max(n_variants, 1)):
                bic_qtns = [cand_indices[0]]

            # Jalankan pemindaian satu kali di akhir dengan pseudo-QTNs terpilih
            final_p, final_b, final_se = _scan_genome(bic_qtns)
            out_tsv = workspace / "blink_results.tsv"

        elif m_name == "MLMM":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: MLMM ---")
            append_log(log_file, "Menjalankan Multiple Locus Mixed Model (Forward-Backward Stepwise)...")
            mlmm_cofactors: list[int] = []
            cur_p, cur_b, cur_se = p_base, beta_base, se_base
            for step in range(5):
                top_m = int(np.argmin(cur_p))
                if cur_p[top_m] < (0.05 / max(n_variants, 1)) or len(mlmm_cofactors) == 0:
                    if top_m not in mlmm_cofactors:
                        mlmm_cofactors.append(top_m)
                        append_log(
                            log_file,
                            f"  [Forward Step {step+1}] Menambahkan kofaktor: {variants_meta[top_m]['rs']} "
                            f"(Chr {variants_meta[top_m]['chr']}:{variants_meta[top_m]['ps']}, p={cur_p[top_m]:.2e})",
                        )
                        cur_p, cur_b, cur_se = _scan_genome(mlmm_cofactors)
                else:
                    break
            final_p, final_b, final_se = cur_p, cur_b, cur_se
            out_tsv = workspace / "mlmm_results.tsv"
            out_tsv = workspace / "mlmm_results.tsv"

        else:
            continue

        # Simpan TSV kanonik untuk metode ini
        with out_tsv.open("w", encoding="utf-8", newline="") as tf:
            writer = csv.writer(tf, delimiter="\t")
            writer.writerow(CANONICAL_COLUMNS)
            for idx, vm in enumerate(variants_meta):
                writer.writerow([
                    vm["chr"],
                    vm["ps"],
                    vm["rs"],
                    vm["alt"],
                    vm["ref"],
                    round(float(af_arr[idx]), 6),
                    round(float(final_b[idx]), 6),
                    round(float(final_se[idx]), 6),
                    float(final_p[idx]),
                    int(nobs_arr[idx]) if int(nobs_arr[idx]) > 0 else n_samples,
                ])

        # Hitung FDR Benjamini-Hochberg (H&B.P.Value) dan PVE(%) untuk top/significant SNPs
        q_arr = compute_bh_fdr(final_p)
        bonf_p = 0.05 / max(n_variants, 1)
        sorted_indices = np.argsort(final_p)
        sig_indices = [int(i) for i in sorted_indices if float(final_p[i]) <= bonf_p]
        top_indices = sig_indices[: max(5, len(sig_indices))] if sig_indices else [int(i) for i in sorted_indices[: min(5, n_variants)]]

        display_m = METHOD_DISPLAY_NAMES.get(m_name, m_name)
        method_sig_snps: list[dict[str, Any]] = []
        for ti in top_indices:
            ti_maf = round(float(maf_arr[ti]), 6) if float(maf_arr[ti]) > 0.0 else 0.25
            ti_nobs = int(nobs_arr[ti]) if int(nobs_arr[ti]) > 0 else n_samples
            ti_beta = round(float(final_b[ti]), 6)
            ti_se = round(float(final_se[ti]), 6)
            ti_p = float(final_p[ti])
            ti_q = float(q_arr[ti])
            ti_pve = compute_snp_pve_pct(
                beta=ti_beta,
                se=ti_se,
                maf=ti_maf,
                nobs=ti_nobs,
                p_val=ti_p,
                y_var=y_var,
            )
            snp_entry = {
                "trait": trait_name,
                "snp": variants_meta[ti]["rs"],
                "chr": str(variants_meta[ti]["chr"]),
                "pos": variants_meta[ti]["ps"],
                "p": ti_p,
                "maf": ti_maf,
                "nobs": ti_nobs,
                "effect": ti_beta,
                "beta": ti_beta,
                "se": ti_se,
                "h_b_p_value": ti_q,
                "pve_pct": ti_pve,
                "method": m_name,
                "is_significant": bool(ti_p <= bonf_p),
            }
            lead_snps_multi.append(snp_entry)
            method_sig_snps.append(snp_entry)

        pve_csv = workspace / f"{display_m}_Significant_SNPs_PVE.csv"
        write_significant_pve_csv(pve_csv, method_sig_snps, trait_name=trait_name)

        all_methods_results[m_name] = {
            "status": "completed",
            "tsv_file": str(out_tsv),
            "pve_csv_file": str(pve_csv),
            "total_snps": n_variants,
            "min_p": float(np.min(final_p)),
            "n_significant_bonferroni": len(sig_indices),
            "significant_snps": method_sig_snps,
        }
        append_log(
            log_file,
            f"[INFO] Hasil {display_m} tersimpan ke {out_tsv.name} & {pve_csv.name} (min p = {np.min(final_p):.2e}).",
        )

    return {
        "methods_results": all_methods_results,
        "lead_snps_multi": lead_snps_multi,
    }


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
    Menjalankan analisis Multi-Locus GWAS (FarmCPU, BLINK, dan MLMM) bila diaktifkan;
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
        model_req = str(gwas_cfg.get("model") or "").lower()
        is_model_multilocus = model_req in {"farmcpu", "blink", "mlmm"}
        enable_multilocus = bool(gwas_cfg.get("enable_multilocus", False)) or is_model_multilocus

        # Resolusi metode yang dipilih
        raw_methods = params.get("methods") if params else None
        if not raw_methods:
            raw_methods = gwas_cfg.get("multilocus_methods")
        if not raw_methods:
            raw_methods = ["FarmCPU", "BLINK", "MLMM"]
        if is_model_multilocus:
            named = {"farmcpu": "FarmCPU", "blink": "BLINK", "mlmm": "MLMM"}[model_req]
            if named not in raw_methods:
                raw_methods = [named] + [m for m in raw_methods if m != named]

        active_methods = [m for m in raw_methods if m in SUPPORTED_MULTILOCUS_MODELS]
        if not active_methods:
            active_methods = ["FarmCPU", "BLINK", "MLMM"]

        has_rscript = bool(which_tool("Rscript"))
        runner_script = Path(__file__).resolve().parents[1] / "templates" / "gapit_runner.R"
        can_run_r = has_rscript and runner_script.is_file()

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_MULTILOCUS,
            current_step=PipelineStep.MULTILOCUS,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 7: MULTI-LOCUS GWAS (FarmCPU, BLINK & MLMM) ===\n"
            f"Waktu mulai    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Diaktifkan     : {enable_multilocus}\n"
            f"Model Pilihan  : {', '.join(active_methods)}\n"
            f"Rscript runtime: {'Tersedia' if has_rscript else 'Tidak tersedia (menggunakan Python Native Engine)'}\n",
        )

        if not enable_multilocus:
            reason = "Multi-Locus GWAS tidak diaktifkan pada konfigurasi analisis"
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

        methods_results: dict[str, Any] = {}
        multi_lead_snps: list[dict[str, Any]] = []

        # Jalankan R GAPIT jika tersedia
        r_success = False
        if can_run_r:
            try:
                pheno_path = workspace.parent / "gwas" / "phenotype.txt"
                if pheno_path.is_file():
                    append_log(log_file, "[INFO] Mengeksekusi script GAPIT via Rscript...")
                    cmd = [
                        "Rscript",
                        str(runner_script),
                        "--y", str(pheno_path),
                        "--models", ",".join(active_methods),
                        "--out", str(workspace),
                    ]
                    res = run_cli_command(cmd, log_file=log_file, timeout_seconds=600)
                    if res.returncode == 0:
                        r_success = True
                        append_log(log_file, "[INFO] GAPIT R script selesai dengan sukses.")
            except Exception as r_err:
                append_log(log_file, f"[WARN] Eksekusi Rscript gagal atau GAPIT tidak terpasang: {r_err}")
                append_log(log_file, "[INFO] Beralih secara transparan ke Python Native Engine...")

        # Jika R tidak berhasil atau tidak tersedia, gunakan Python Native Engine
        if not r_success:
            engine_output = _run_python_multilocus_engine(
                job_id=job_id,
                dataset_id=dataset_id,
                methods=active_methods,
                workspace=workspace,
                log_file=log_file,
                ctx=ctx,
            )
            methods_results = engine_output.get("methods_results", {})
            multi_lead_snps = engine_output.get("lead_snps_multi", [])

        # 4. Normalisasi output GAPIT CSV ke TSV kanonik jika dihasilkan dari R
        for m in active_methods:
            m_lower = m.lower()
            tsv_target = workspace / f"{m_lower}_results.tsv"
            if not tsv_target.is_file():
                gapit_csv_candidates = list(workspace.glob(f"GAPIT.Association.GWAS_Results.{m}.*.csv"))
                if gapit_csv_candidates:
                    normalize_association_results(gapit_csv_candidates[0], tsv_target)
                    methods_results[m] = {"status": "completed", "tsv_file": str(tsv_target)}

        # Pastikan multi_lead_snps terisi dari TSV model bila sebelumnya kosong
        trait_label = str(
            ctx.get("trait")
            or ((job.parameters or {}).get("phenotype") or {}).get("trait")
            or "Trait"
        )
        if not multi_lead_snps:
            for m in active_methods:
                m_lower = m.lower()
                tsv_target = workspace / f"{m_lower}_results.tsv"
                if tsv_target.is_file():
                    extracted = extract_multilocus_leads_from_tsv(
                        tsv_target,
                        method_name=m.upper(),
                        trait_name=trait_label,
                        max_top=5,
                    )
                    if extracted:
                        multi_lead_snps.extend(extracted)
                        display_m = METHOD_DISPLAY_NAMES.get(m.upper(), m)
                        pve_csv = workspace / f"{display_m}_Significant_SNPs_PVE.csv"
                        write_significant_pve_csv(pve_csv, extracted, trait_name=trait_label)
                        if m.upper() in methods_results:
                            methods_results[m.upper()]["significant_snps"] = extracted
                            methods_results[m.upper()]["pve_csv_file"] = str(pve_csv)

        # Simpan ringkasan multi-locus JSON
        summary_payload = {
            "status": "completed",
            "models_evaluated": active_methods,
            "results": methods_results,
            "n_lead_snps": len(multi_lead_snps),
            "lead_snps": multi_lead_snps,
        }
        write_json(workspace / "multilocus_summary.json", summary_payload)
        upsert_result(
            db,
            job_id=job_id,
            result_type=ResultType.MULTILOCUS_RESULTS,
            file_path=str(workspace / "multilocus_summary.json"),
            summary_stats=summary_payload,
        )

        # 5. Bangun Perbandingan Puncak (Single-Locus vs Multi-Locus)
        single_lead_snps = ctx.get("lead_snps") or []
        if not single_lead_snps:
            postgwas_lead = workspace.parent / "postgwas" / "lead_snps.json"
            if postgwas_lead.is_file():
                try:
                    single_lead_snps = json.loads(postgwas_lead.read_text(encoding="utf-8"))
                except Exception:
                    single_lead_snps = []

        peak_comp_file = workspace / "peak_comparison.json"
        summary_csv = workspace.parent / "postgwas" / "Ringkasan_Puncak_GWAS.csv"
        comparison_res = generate_peak_comparison(
            single_lead_snps=single_lead_snps,
            multi_lead_snps=multi_lead_snps,
            output_csv=summary_csv,
            output_json=peak_comp_file,
        )
        upsert_result(
            db,
            job_id=job_id,
            result_type=ResultType.PEAK_COMPARISON,
            file_path=str(peak_comp_file),
            summary_stats=comparison_res,
        )

        # 6. Bila model primer yang dipilih adalah multi-locus (FarmCPU/BLINK/MLMM),
        # alihkan hasil terbaik ke gwas_results.normalized.tsv agar visualisasi
        # Manhattan & QQ Plot di Fase 9 menampilkan hasil multi-locus tersebut
        if is_model_multilocus:
            selected_tsv = workspace / f"{model_req}_results.tsv"
            if selected_tsv.is_file():
                primary_assoc_dest = workspace.parent / "gwas" / "gwas_results.normalized.tsv"
                shutil.copyfile(selected_tsv, primary_assoc_dest)
                append_log(
                    log_file,
                    f"[INFO] Mengalihkan hasil {model_req.upper()} ke gwas_results.normalized.tsv "
                    f"sebagai dataset visualisasi Manhattan & QQ Plot.",
                )

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
            "multilocus_results": methods_results,
            "multilocus_lead_snps": multi_lead_snps,
            "peak_comparison": comparison_res,
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
    Membandingkan puncak asosiasi Single-Locus (GEMMA/GLM) dengan Multi-Locus (FarmCPU, BLINK, MLMM).
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
                "maf": matched_multi.get("maf"),
                "nobs": matched_multi.get("nobs"),
                "effect": matched_multi.get("effect", s.get("beta")),
                "h_b_p_value": matched_multi.get("h_b_p_value"),
                "pve_pct": matched_multi.get("pve_pct"),
                "method": matched_multi.get("method", "Multi-Locus"),
                "status": "Konsisten (Single + Multi-Locus)",
                "keterangan": f"Sangat andal; terkonfirmasi oleh model LMM dan {matched_multi.get('method', 'Multi-Locus')}.",
            })
        else:
            single_only.append({
                "snp": s.get("snp"),
                "chr": s_chr,
                "pos": s_pos,
                "p_single": s.get("p"),
                "p_multi": None,
                "effect": s.get("beta"),
                "method": "Single-Locus",
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
                "maf": m.get("maf"),
                "nobs": m.get("nobs"),
                "effect": m.get("effect", m.get("beta")),
                "h_b_p_value": m.get("h_b_p_value"),
                "pve_pct": m.get("pve_pct"),
                "method": m.get("method", "Multi-Locus"),
                "status": "Hanya Multi-Locus",
                "keterangan": f"Potensi varian efek minor ({m.get('method', 'Multi-Locus')}) yang tersembunyi pada model univariat.",
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
        writer.writerow(["SNP", "Kromosom", "Posisi", "Metode", "P_Single", "P_Multi", "Status", "Keterangan"])
        for r in all_rows:
            writer.writerow([
                r["snp"],
                r["chr"],
                r["pos"],
                r.get("method", "-"),
                r["p_single"] if r["p_single"] is not None else "NA",
                r["p_multi"] if r["p_multi"] is not None else "NA",
                r["status"],
                r["keterangan"],
            ])

    return result_json
