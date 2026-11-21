"""Celery task for Multi-Locus GWAS (FarmCPU, BLINK, and MLMM) & Peak Comparison (Fase 4C & 4H)."""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any, Callable

import numpy as np

from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.services.assoc_normalizer import CANONICAL_COLUMNS, normalize_association_results
from app.services.chromosome_service import load_split_map, restore_coord
from app.services.manhattan_parser import _build_chr_mapping
from app.core.resource_limits import read_container_memory_limit_mb
from app.tasks._common import (
    JobCancelledError,
    append_log,
    chr_label,
    ensure_not_cancelled,
    is_job_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    which_tool,
    write_json,
    write_stage_summary,
)
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLICancelledError, run_cli_command

logger = get_task_logger(__name__)

SUPPORTED_MULTILOCUS_MODELS = ["FarmCPU", "BLINK", "MLMM"]
METHOD_DISPLAY_NAMES = {"FARMCPU": "FarmCPU", "BLINK": "BLINK", "MLMM": "MLMM"}
MULTILOCUS_TIMEOUT_SECONDS = int(os.getenv("MULTILOCUS_TIMEOUT_SECONDS", str(6 * 60 * 60)))
GAPIT_MAX_SNPS = int(os.getenv("GAPIT_MAX_SNPS", "300000"))


def _single_model_label(parameters: dict[str, Any] | None, ctx: dict[str, Any] | None = None) -> str:
    """Label model single-locus; structure_tasks menulis model_used di level atas job.parameters."""
    parameters = parameters or {}
    label = (
        parameters.get("model_used")
        or (parameters.get("gwas") or {}).get("model_used")
        or (ctx or {}).get("model_used")
        or "Single-Locus"
    )
    return str(label).upper()


def _resolve_genotype_bfile(workspace: Path, ctx: dict[str, Any] | None = None) -> Path | None:
    """Resolve one shared genotype source, preferring imputed data over QC data."""
    ctx = ctx or {}
    candidates = [
        ctx.get("genotype_bfile_prefix"),
        ctx.get("output_prefix"),
        workspace.parent / "imputation" / "imputed",
        workspace.parent / "qc" / "plink_qc",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        prefix = Path(str(candidate))
        if prefix.suffix in {".bed", ".bim", ".fam"}:
            prefix = prefix.with_suffix("")
        if Path(f"{prefix}.bed").is_file():
            return prefix
    return None


def _count_bfile_snps(prefix: Path) -> int:
    bim_path = Path(f"{prefix}.bim")
    if not bim_path.is_file():
        return 0
    with bim_path.open("r", encoding="utf-8", errors="replace") as bim:
        return sum(1 for line in bim if line.strip())


def _prune_gapit_bfile(
    source_prefix: Path,
    workspace: Path,
    log_file: Path,
    n_before: int,
) -> tuple[Path, int, float]:
    """LD-prune a large bfile and return the input prefix for GAPIT."""
    plink = which_tool("plink")
    if not plink:
        append_log(log_file, f"[WARN] GAPIT dilewati: PLINK tidak tersedia untuk memangkas {n_before} SNP.")
        raise RuntimeError("PLINK diperlukan untuk membatasi SNP GAPIT")

    selected_prefix = source_prefix
    selected_count = n_before
    used_r2 = 0.8
    for r2 in (0.8, 0.5):
        if selected_count <= GAPIT_MAX_SNPS:
            break
        used_r2 = r2
        ld_prefix = workspace / f"gapit_ld_r{str(r2).replace('.', '')}"
        run_cli_command(
            [
                plink,
                "--bfile", str(source_prefix),
                "--allow-extra-chr",
                "--indep-pairwise", "50", "5", str(r2),
                "--out", str(ld_prefix),
            ],
            cwd=workspace,
            log_file=log_file,
            timeout=MULTILOCUS_TIMEOUT_SECONDS,
        )
        prune_file = ld_prefix.with_suffix(".prune.in")
        if not prune_file.is_file():
            raise RuntimeError(f"PLINK tidak menghasilkan daftar SNP terpilih: {prune_file}")
        selected_count = sum(1 for line in prune_file.read_text(encoding="utf-8").splitlines() if line.strip())
        append_log(log_file, f"[7.1] LD pruning r2={r2}: {n_before} -> {selected_count} SNP.")
        if selected_count <= GAPIT_MAX_SNPS:
            input_prefix = workspace / "gapit_input"
            run_cli_command(
                [
                    plink,
                    "--bfile", str(source_prefix),
                    "--allow-extra-chr",
                    "--extract", str(prune_file),
                    "--make-bed",
                    "--out", str(input_prefix),
                ],
                cwd=workspace,
                log_file=log_file,
                timeout=MULTILOCUS_TIMEOUT_SECONDS,
            )
            selected_prefix = input_prefix
            break

    if selected_count > GAPIT_MAX_SNPS:
        append_log(
            log_file,
            f"[WARN] GAPIT tidak dijalankan: LD pruning r2={used_r2} masih menghasilkan "
            f"{selected_count} SNP (batas {GAPIT_MAX_SNPS}); beralih ke engine Python.",
        )
        raise RuntimeError("Jumlah SNP GAPIT tetap melebihi batas setelah LD pruning")
    return selected_prefix, selected_count, used_r2


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
) -> float:
    """
    Hitung Phenotype_Variance_Explained(%) (PVE%) untuk satu SNP.
    Gunakan R^2 parsial t^2 / (t^2 + n - 2) dari statistik t = beta / se
    (atau chi2 isf dari p_val).
    """
    from scipy.stats import chi2

    n_eff = max(int(nobs), 3)
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


def _resolve_orig_coord(
    item: dict[str, Any],
    chrom_fallback: str,
    pos_fallback: int,
    split_map: dict[str, Any] | None,
) -> tuple[str, int]:
    """Resolve original genomic (chr, pos) using split_map when available."""
    if split_map is not None:
        return restore_coord(item.get("chr") or chrom_fallback, pos_fallback, split_map)
    if item.get("orig_chr") is not None and item.get("orig_pos") is not None:
        return str(item["orig_chr"]), int(item["orig_pos"])
    return chrom_fallback, pos_fallback


def extract_multilocus_leads_from_tsv(
    tsv_path: Path,
    method_name: str,
    trait_name: str = "Trait",
    max_top: int = 5,
    split_map: dict[str, Any] | None = None,
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
                    "chr": chr_label(r.get("chr", "")),
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
        orig_chr, orig_pos = restore_coord(r["chr"], r["pos"], split_map)
        out.append({
            "trait": trait_name,
            "snp": r["rs"],
            "chr": r["chr"],
            "pos": r["pos"],
            "orig_chr": orig_chr,
            "orig_pos": orig_pos,
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
    db: Any = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Eksekusi multi-locus GWAS (FarmCPU, BLINK, MLMM) di lingkungan Python murni
    berbasis NumPy/SciPy tervektorisasi ketika Rscript / GAPIT tidak tersedia.
    """
    import uuid
    from scipy.special import erfc
    from app.services.structure_service import compute_centered_kinship, read_plink_binary
    from app.tasks.gemma_tasks import _parse_vcf_genotypes, _prepare_phenotype_vector

    def _check_cancel() -> None:
        if db is not None:
            ensure_not_cancelled(job_id, db, run_id=run_id)
            return
        try:
            uuid.UUID(str(job_id))
        except (ValueError, TypeError):
            return
        ensure_not_cancelled(job_id, None, run_id=run_id)

    _check_cancel()
    append_log(log_file, "[INFO] Menginisialisasi Python Native Engine untuk Multi-Locus GWAS...")

    # 1. Muat Genotipe dari resolver bersama agar GAPIT dan engine Python identik.
    candidate_bfile = _resolve_genotype_bfile(workspace, ctx)
    G_mat: np.ndarray | None = None
    variants_meta: list[dict[str, Any]] = []
    samples: list[str] = []

    if candidate_bfile is not None:
        try:
            samples, plink_vars, G_mat = read_plink_binary(str(candidate_bfile))
            for pv in plink_vars:
                chrom_label = chr_label(pv["chrom"])
                variants_meta.append(
                    {
                        "chr": chrom_label,
                        "rs": pv["rsid"] if pv["rsid"] and pv["rsid"] != "." else f"{chrom_label}:{pv['pos']}",
                        "ps": int(pv["pos"]),
                        "alt": pv["a1"],
                        "ref": pv["a2"],
                    }
                )
            append_log(
                log_file,
                f"[7.1] Sumber genotipe: {candidate_bfile}; {len(samples)} sampel, {len(variants_meta)} varian.",
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
            samples = [f"S{i + 1}" for i in range(20)]
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
    pheno_file = workspace.parent / "inspection" / "pheno_plink.txt"
    pheno_path_str = str(pheno_file) if pheno_file.is_file() else ""
    pheno_vec, _ = _prepare_phenotype_vector(samples, [], pheno_path_str, workspace, log_file)

    valid_pheno = (pheno_vec != -9.0) & ~np.isnan(pheno_vec)
    if int(np.sum(valid_pheno)) < 3:
        valid_pheno = np.ones(n_samples, dtype=bool)

    y_val = np.where(valid_pheno, pheno_vec, 0.0)
    y_mean = np.mean(y_val[valid_pheno])
    y_centered = np.where(valid_pheno, y_val - y_mean, 0.0)
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

    # EMMAX transform and REML grid search for the MLMM approximation.
    valid_indices = np.flatnonzero(valid_pheno)
    kinship_valid = kinship_mat[np.ix_(valid_indices, valid_indices)]
    eigenvalues, eigenvectors = np.linalg.eigh((kinship_valid + kinship_valid.T) / 2.0)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    y_valid = y_centered[valid_indices]
    cov_valid = c_mat[valid_indices]
    best_h2 = 0.0
    best_ll = -np.inf
    best_transform = eigenvectors.T
    n_valid_samples = len(valid_indices)
    n_covariates = cov_valid.shape[1]
    for h2 in np.arange(0.0, 1.0, 0.01):
        d = h2 * eigenvalues + (1.0 - h2)
        transform = eigenvectors.T / np.sqrt(np.maximum(d, 1e-12))[:, None]
        y_rot = transform @ y_valid
        x_rot = transform @ cov_valid
        q_rot, _ = np.linalg.qr(x_rot)
        residual = y_rot - q_rot @ (q_rot.T @ y_rot)
        dof = max(n_valid_samples - n_covariates, 1)
        rss = max(float(np.sum(residual ** 2)), 1e-12)
        sign, logdet_x = np.linalg.slogdet(x_rot.T @ x_rot)
        if sign <= 0:
            continue
        ll = -0.5 * (
            dof * math.log(rss / dof)
            + float(np.sum(np.log(np.maximum(d, 1e-12))))
            + float(logdet_x)
        )
        if ll > best_ll:
            best_ll = ll
            best_h2 = float(h2)
            best_transform = transform
    append_log(log_file, f"  [MLMM] h2 null = {best_h2:.2f}")

    # Fungsi bantu regresi vektorisasi per chunk
    def _scan_genome(
        cofactors_idx: list[int] | None = None,
        transform: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Menjalankan pemindaian seluruh varian dengan matriks kofaktor tambahan secara tervektorisasi per chunk."""
        scan_g = G_mat[valid_indices]
        scan_y = y_centered[valid_indices]
        scan_c = c_mat[valid_indices]

        if cofactors_idx:
            x_cof = [scan_c]
            for c_i in cofactors_idx:
                col = np.where(scan_g[:, c_i] >= 0, scan_g[:, c_i].astype(np.float64), 0.0)
                col = col - np.mean(col)
                x_cof.append(col[:, None])
            full_c = np.column_stack(x_cof)
            q_base, _ = np.linalg.qr(transform @ full_c if transform is not None else full_c)
        else:
            q_base, _ = np.linalg.qr(transform @ scan_c if transform is not None else scan_c)

        n_cov_cols = q_base.shape[1]
        transformed_y = transform @ scan_y if transform is not None else scan_y
        y_p = transformed_y - q_base @ (q_base.T @ transformed_y)

        p_vals = np.ones(n_variants, dtype=np.float64)
        betas = np.zeros(n_variants, dtype=np.float64)
        ses = np.zeros(n_variants, dtype=np.float64)

        chunk_size = 5000
        for start in range(0, n_variants, chunk_size):
            _check_cancel()
            end = min(start + chunk_size, n_variants)
            k = end - start
            g_chunk = scan_g[:, start:end]

            valid_mask = g_chunk >= 0
            n_valid = np.sum(valid_mask, axis=0)

            x_float = np.where(valid_mask, g_chunk.astype(np.float64), 0.0)
            x_sum = np.sum(x_float, axis=0)
            x_mean = np.divide(x_sum, n_valid, out=np.zeros(k, dtype=np.float64), where=n_valid > 0)

            # Imputasi mean untuk missing sebelum proyeksi ortogonal
            x_imp = np.where(valid_mask, x_float, x_mean[None, :])
            transformed_x = transform @ x_imp if transform is not None else x_imp
            x_proj = transformed_x - q_base @ (q_base.T @ transformed_x)
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

    def _refit_qtn_statistics(
        p_values: np.ndarray,
        betas: np.ndarray,
        ses: np.ndarray,
        qtns: list[int],
        transform: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Evaluate each selected QTN while excluding itself from the cofactors."""
        final_p = p_values.copy()
        final_b = betas.copy()
        final_se = ses.copy()
        for qtn in qtns:
            other_qtns = [idx for idx in qtns if idx != qtn]
            qtn_p, qtn_b, qtn_se = _scan_genome(other_qtns, transform=transform)
            final_p[qtn] = qtn_p[qtn]
            final_b[qtn] = qtn_b[qtn]
            final_se[qtn] = qtn_se[qtn]
        return final_p, final_b, final_se

    # Initial single-locus scan
    append_log(log_file, "[7.2] Menghitung baseline scan awal seluruh penanda...")
    p_base, beta_base, se_base = _scan_genome(None)

    all_methods_results: dict[str, Any] = {}
    lead_snps_multi: list[dict[str, Any]] = []
    split_map = load_split_map(job_id)

    # Eksekusi masing-masing model yang dipilih
    for m in methods:
        _check_cancel()
        m_name = m.strip().upper()
        if m_name == "FARMCPU":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: FarmCPU ---")
            append_log(log_file, "Membagi model menjadi Fixed Effect Model (FEM) dan Random Effect Model (REM)...")
            # Stepwise pseudo-QTN selection
            pseudo_qtns: list[int] = []
            cur_p, cur_b, cur_se = p_base, beta_base, se_base
            for it in range(3):
                top_cand = int(np.lexsort((-np.arange(n_variants), cur_p))[0])
                if cur_p[top_cand] < 0.05 / max(n_variants, 1) or len(pseudo_qtns) == 0:
                    if top_cand not in pseudo_qtns:
                        pseudo_qtns.append(top_cand)
                        append_log(
                            log_file,
                            f"  [Iterasi {it + 1}] Pseudo-QTN terpilih: {variants_meta[top_cand]['rs']} "
                            f"(Chr {variants_meta[top_cand]['chr']}:{variants_meta[top_cand]['ps']}, p={cur_p[top_cand]:.2e})",
                        )
                        cur_p, cur_b, cur_se = _scan_genome(pseudo_qtns)
                else:
                    break
            final_p, final_b, final_se = _refit_qtn_statistics(
                cur_p, cur_b, cur_se, pseudo_qtns
            )
            out_tsv = workspace / "farmcpu_results.tsv"

        elif m_name == "BLINK":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: BLINK ---")
            append_log(log_file, "Memfilter kandidat via LD Pruning & Bayesian Information Criterion (BIC)...")
            cand_indices = [
                int(idx)
                for idx in np.lexsort((-np.arange(n_variants), p_base))
                if p_base[idx] < 0.05
            ][:15]
            if not cand_indices:
                cand_indices = list(np.argsort(p_base)[: min(5, n_variants)])

            bic_qtns: list[int] = []
            n_valid_samples = int(np.sum(valid_pheno))

            # Hitung baseline BIC dengan hanya kovariat populasi
            y_res0 = y_centered - q_cov @ (q_cov.T @ y_centered)
            rss0 = float(np.sum(np.where(valid_pheno, y_res0**2, 0.0)))
            k0 = q_cov.shape[1]
            best_bic = n_valid_samples * np.log(max(rss0 / max(n_valid_samples, 1), 1e-9)) + k0 * np.log(max(n_valid_samples, 1))

            for cand in cand_indices:
                _check_cancel()
                if any(
                    np.corrcoef(
                        np.where(G_mat[valid_pheno, qtn] >= 0, G_mat[valid_pheno, qtn], np.mean(G_mat[valid_pheno, qtn][G_mat[valid_pheno, qtn] >= 0])),
                        np.where(G_mat[valid_pheno, cand] >= 0, G_mat[valid_pheno, cand], np.mean(G_mat[valid_pheno, cand][G_mat[valid_pheno, cand] >= 0])),
                    )[0, 1] ** 2 >= 0.7
                    for qtn in bic_qtns
                ):
                    continue
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
            final_p, final_b, final_se = _refit_qtn_statistics(
                *_scan_genome(bic_qtns), bic_qtns
            )
            out_tsv = workspace / "blink_results.tsv"

        elif m_name == "MLMM":
            append_log(log_file, "\n--- Eksekusi Model Multi-Locus: MLMM ---")
            append_log(log_file, "Menjalankan Multiple Locus Mixed Model (Forward-Backward Stepwise)...")
            mlmm_cofactors: list[int] = []
            cur_p, cur_b, cur_se = p_base, beta_base, se_base
            for step in range(5):
                top_m = int(np.lexsort((-np.arange(n_variants), cur_p))[0])
                if cur_p[top_m] < (0.05 / max(n_variants, 1)) or len(mlmm_cofactors) == 0:
                    if top_m not in mlmm_cofactors:
                        mlmm_cofactors.append(top_m)
                        append_log(
                            log_file,
                            f"  [Forward Step {step + 1}] Menambahkan kofaktor: {variants_meta[top_m]['rs']} "
                            f"(Chr {variants_meta[top_m]['chr']}:{variants_meta[top_m]['ps']}, p={cur_p[top_m]:.2e})",
                        )
                        cur_p, cur_b, cur_se = _scan_genome(mlmm_cofactors, transform=best_transform)
                else:
                    break
            final_p, final_b, final_se = _refit_qtn_statistics(
                cur_p, cur_b, cur_se, mlmm_cofactors, transform=best_transform
            )
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
            )
            orig_chr, orig_pos = restore_coord(
                str(variants_meta[ti]["chr"]),
                int(variants_meta[ti]["ps"]),
                split_map,
            )
            snp_entry = {
                "trait": trait_name,
                "snp": variants_meta[ti]["rs"],
                "chr": str(variants_meta[ti]["chr"]),
                "pos": variants_meta[ti]["ps"],
                "orig_chr": orig_chr,
                "orig_pos": orig_pos,
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
            "pseudo_qtns": [variants_meta[idx]["rs"] for idx in (
                pseudo_qtns if m_name == "FARMCPU" else bic_qtns if m_name == "BLINK" else mlmm_cofactors
            )],
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


def _write_gapit_cv(
    eigenvec_file: Path,
    samples: list[str],
    n_pcs: int,
    cv_path: Path,
) -> int:
    """Build gapit_CV.txt aligned strictly to gapit_GD sample order from PCA eigenvec."""
    eigenvec_by_iid: dict[str, list[str]] = {}
    lines = [
        ln.strip()
        for ln in eigenvec_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip()
    ]
    for idx, line in enumerate(lines):
        cols = line.split()
        if not cols:
            continue
        if idx == 0 and (
            cols[0].startswith("#")
            or (len(cols) >= 2 and cols[0].upper() in {"FID", "#FID"} and cols[1].upper() == "IID")
        ):
            continue
        if len(cols) < 3:
            continue
        iid = cols[1].strip()
        if iid:
            eigenvec_by_iid.setdefault(iid, cols[2:])

    missing_samples = [sid for sid in samples if sid not in eigenvec_by_iid]
    if missing_samples:
        preview = ", ".join(missing_samples[:5])
        suffix = "..." if len(missing_samples) > 5 else ""
        raise ValueError(
            f"Sampel pada gapit_GD tidak ditemukan di {eigenvec_file.name}: {preview}{suffix}"
        )

    min_avail_pcs = min(len(eigenvec_by_iid[sid]) for sid in samples)
    k_pcs = min(int(n_pcs), min_avail_pcs)
    if k_pcs < 1:
        raise ValueError(f"Jumlah PC pada {eigenvec_file.name} kurang dari 1")

    header = ["Taxa", *(f"PC{i}" for i in range(1, k_pcs + 1))]
    cv_lines = ["\t".join(header)]
    for sid in samples:
        cv_lines.append("\t".join([sid, *eigenvec_by_iid[sid][:k_pcs]]))
    cv_path.write_text("\n".join(cv_lines) + "\n", encoding="utf-8")
    return k_pcs


def _write_gapit_inputs(
    workspace: Path,
    log_file: Path,
    ctx: dict[str, Any] | None = None,
    n_pcs: int = 3,
) -> tuple[Path, Path, Path, Path | None, dict[int, str]]:
    """Generate GAPIT inputs from the shared genotype source, phenotype, and PCA covariates."""
    from app.services.structure_service import read_plink_binary

    bfile_prefix = _resolve_genotype_bfile(workspace, ctx) or workspace.parent / "qc" / "plink_qc"
    n_before = _count_bfile_snps(bfile_prefix)
    append_log(log_file, f"[7.1] Sumber genotipe: {bfile_prefix} ({n_before} SNP sebelum pruning).")
    n_after = n_before
    pruning_r2 = None
    if n_before > GAPIT_MAX_SNPS:
        bfile_prefix, n_after, pruning_r2 = _prune_gapit_bfile(
            bfile_prefix, workspace, log_file, n_before
        )
    append_log(
        log_file,
        f"[7.1] SNP GAPIT: sebelum={n_before}, sesudah={n_after}"
        + (f", r2={pruning_r2}" if pruning_r2 is not None else ""),
    )
    samples, variants, g_mat = read_plink_binary(bfile_prefix)
    if not samples or not variants or g_mat.size == 0:
        raise ValueError("Data genotipe PLINK kosong untuk input GAPIT.")

    pheno_file = workspace.parent / "inspection" / "pheno_plink.txt"
    if not pheno_file.is_file():
        raise FileNotFoundError(f"File fenotipe tidak ditemukan: {pheno_file}")

    raw_lines = [
        ln.rstrip("\r\n")
        for ln in pheno_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip()
    ]
    if not raw_lines:
        raise ValueError(f"File fenotipe kosong: {pheno_file}")

    header_cols = raw_lines[0].split("\t")
    trait_name = header_cols[2].strip() if len(header_cols) >= 3 and header_cols[2].strip() else "TRAIT"

    pheno_by_iid: dict[str, str] = {}
    for line in raw_lines[1:]:
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        iid = cols[1].strip()
        raw_val = cols[2].strip()
        if not iid:
            continue
        if not raw_val or raw_val.upper() == "NA":
            pheno_by_iid[iid] = "NA"
            continue
        try:
            num = float(raw_val)
            pheno_by_iid[iid] = "NA" if (math.isnan(num) or math.isinf(num)) else raw_val
        except ValueError:
            pheno_by_iid[iid] = "NA"

    y_rows: list[str] = []
    valid_y_count = 0
    for sample_id in samples:
        val = pheno_by_iid.get(sample_id, "NA")
        if val != "NA":
            valid_y_count += 1
        y_rows.append(f"{sample_id}\t{val}")

    if valid_y_count < 3:
        raise ValueError(
            f"Jumlah nilai fenotipe non-NA kurang dari 3 ({valid_y_count}); GAPIT memerlukan minimal 3 sampel."
        )

    y_path = workspace / "gapit_Y.txt"
    y_path.write_text("\n".join([f"Taxa\t{trait_name}", *y_rows]) + "\n", encoding="utf-8")

    keep_mask = np.any(g_mat >= 0, axis=0)
    kept_indices = np.flatnonzero(keep_mask)
    if len(kept_indices) == 0:
        raise ValueError("Seluruh SNP missing; tidak ada varian tersisa untuk input GAPIT.")

    used_snp_names: set[str] = set()
    snp_names: list[str] = []
    raw_chroms: list[str] = []
    positions: list[int] = []
    for idx in kept_indices:
        var = variants[int(idx)]
        chrom_raw = str(var.get("chrom") or "").strip()
        pos = int(var.get("pos") or 0)
        rsid = str(var.get("rsid") or "").strip()
        base_name = f"{chrom_raw}_{pos}" if (not rsid or rsid == ".") else rsid
        candidate = base_name
        if candidate in used_snp_names:
            candidate = f"{base_name}_{idx}"
            suffix = 1
            while candidate in used_snp_names:
                candidate = f"{base_name}_{idx}_{suffix}"
                suffix += 1
        used_snp_names.add(candidate)
        snp_names.append(candidate)
        raw_chroms.append(chrom_raw)
        positions.append(pos)

    chr_map, _ = _build_chr_mapping({c for c in raw_chroms if c})
    chr_idx_to_label: dict[int, str] = {}
    for chrom_raw in dict.fromkeys(raw_chroms):
        gm_idx = chr_map.get(chrom_raw, 0)
        label = chr_label(chrom_raw)
        if gm_idx in chr_idx_to_label:
            append_log(
                log_file,
                f"[WARN] Label kromosom berbeda '{chr_idx_to_label[gm_idx]}' dan '{chrom_raw}' "
                f"memetakan ke indeks GM yang sama ({gm_idx}); mempertahankan '{chr_idx_to_label[gm_idx]}'.",
            )
            continue
        chr_idx_to_label[gm_idx] = label

    gm_path = workspace / "gapit_GM.txt"
    with gm_path.open("w", encoding="utf-8", newline="") as gm_f:
        gm_writer = csv.writer(gm_f, delimiter="\t")
        gm_writer.writerow(["SNP", "Chromosome", "Position"])
        for snp_name, chrom_raw, pos in zip(snp_names, raw_chroms, positions):
            gm_writer.writerow([snp_name, chr_map.get(chrom_raw, 0), pos])

    g_kept = g_mat[:, kept_indices].astype(np.int16, copy=True)
    for j in range(g_kept.shape[1]):
        col = g_kept[:, j]
        miss_mask = col < 0
        if np.any(miss_mask):
            mean_val = int(round(float(np.mean(col[~miss_mask]))))
            col[miss_mask] = max(0, min(2, mean_val))

    gd_path = workspace / "gapit_GD.txt"
    with gd_path.open("w", encoding="utf-8", newline="") as gd_f:
        gd_writer = csv.writer(gd_f, delimiter="\t")
        gd_writer.writerow(["taxa", *snp_names])
        for s_idx, sample_id in enumerate(samples):
            gd_writer.writerow([sample_id, *g_kept[s_idx].tolist()])

    cv_path: Path | None = None
    ctx_eigenvec = (ctx or {}).get("eigenvec_path")
    eigenvec_candidate = Path(ctx_eigenvec) if ctx_eigenvec and Path(ctx_eigenvec).is_file() else (
        workspace.parent / "structure" / "pca_results.eigenvec"
    )
    if eigenvec_candidate.is_file() and int(n_pcs) > 0:
        cv_target = workspace / "gapit_CV.txt"
        k_used = _write_gapit_cv(eigenvec_candidate, samples, int(n_pcs), cv_target)
        cv_path = cv_target
        append_log(
            log_file,
            f"[7.1] Kovariat PCA CV dibuat: {cv_target.name} ({len(samples)} sampel, {k_used} PC dari {eigenvec_candidate.name}).",
        )

    append_log(
        log_file,
        f"[7.1] Input GAPIT dibuat: {len(samples)} sampel, {len(snp_names)} SNP "
        f"(Y={y_path.name}, GD={gd_path.name}, GM={gm_path.name}"
        + (f", CV={cv_path.name}" if cv_path is not None else "")
        + ").",
    )
    return y_path, gd_path, gm_path, cv_path, chr_idx_to_label


def _restore_gapit_chr_labels(tsv_path: Path, chr_idx_to_label: dict[int, str]) -> None:
    """Tulis ulang kolom chr TSV hasil GAPIT memakai label kromosom asli bila ada di peta."""
    if not chr_idx_to_label or not tsv_path.is_file():
        return
    str_map = {str(idx): label for idx, label in chr_idx_to_label.items()}
    with tsv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return
    try:
        chr_col = rows[0].index("chr")
    except ValueError:
        return
    tmp_path = tsv_path.with_suffix(".tsv.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as out_f:
        writer = csv.writer(out_f, delimiter="\t")
        writer.writerow(rows[0])
        for row in rows[1:]:
            if chr_col < len(row):
                raw_chr = row[chr_col].strip()
                if raw_chr in str_map:
                    row[chr_col] = str_map[raw_chr]
            writer.writerow(row)
    tmp_path.replace(tsv_path)


def _run_gapit_models(
    *,
    rscript: str,
    runner_script: Path,
    methods: list[str],
    workspace: Path,
    log_file: Path,
    cancel_check: Callable[[], bool] | None = None,
    ctx: dict[str, Any] | None = None,
    n_pcs: int = 3,
) -> dict[str, dict[str, Any]]:
    """Run GAPIT via Rscript and normalize outputs only for models producing GAPIT CSV files."""
    for stale_file in workspace.glob("GAPIT.*"):
        if stale_file.is_file():
            stale_file.unlink(missing_ok=True)
    for stale_tsv in workspace.glob("*_results.tsv"):
        if stale_tsv.is_file():
            stale_tsv.unlink(missing_ok=True)

    y_path, gd_path, gm_path, cv_path, chr_idx_to_label = _write_gapit_inputs(
        workspace,
        log_file,
        ctx=ctx,
        n_pcs=n_pcs,
    )
    cmd = [
        rscript,
        str(runner_script),
        "--y", str(y_path),
        "--gd", str(gd_path),
        "--gm", str(gm_path),
        "--models", ",".join(methods),
    ]
    if cv_path is not None:
        cmd.extend(["--cv", str(cv_path), "--pca", "0"])
        append_log(
            log_file,
            "[7.1] GAPIT memakai CV dari pca_results.eigenvec (PCA.total=0); "
            "SNP.MAF tidak ditambahkan karena QC sudah menerapkan --maf.",
        )
    else:
        cmd.extend(["--pca", str(n_pcs)])
        append_log(
            log_file,
            f"[7.1] File pca_results.eigenvec tidak ditemukan; GAPIT memakai PCA.total={n_pcs}. "
            "SNP.MAF tidak ditambahkan karena QC sudah menerapkan --maf.",
        )
    cmd.extend(["--out", str(workspace)])
    run_cli_command(
        cmd,
        cwd=workspace,
        log_file=log_file,
        timeout=MULTILOCUS_TIMEOUT_SECONDS,
        cancel_check=cancel_check,
    )

    results: dict[str, dict[str, Any]] = {}
    for m in methods:
        candidates = sorted(
            p for p in {
                *workspace.glob(f"GAPIT.Association.GWAS_Results.{m}.*.csv"),
                *workspace.glob(f"GAPIT.{m}.*.GWAS.Results.csv"),
            }
            if "GWAS_StdErr" not in p.name and "Filter_GWAS_results" not in p.name
        )
        if not candidates:
            continue
        nyc_candidates = [p for p in candidates if "(NYC)" in p.name]
        chosen_csv = nyc_candidates[0] if nyc_candidates else candidates[0]
        tsv_target = workspace / f"{m.lower()}_results.tsv"
        normalize_association_results(chosen_csv, tsv_target)
        _restore_gapit_chr_labels(tsv_target, chr_idx_to_label)
        results[m.upper()] = {
            "status": "completed",
            "tsv_file": str(tsv_target),
            "engine": "gapit",
        }
    return results


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
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Menjalankan analisis Multi-Locus GWAS (FarmCPU, BLINK, dan MLMM) bila diaktifkan;
    atau mencatat status SKIPPED dengan bersih jika tidak diaktifkan.
    """
    stage_started_at = datetime.now(timezone.utc)
    db = SessionLocal()
    ctx: dict[str, Any] = dict(gwas_result or {})
    try:
        ensure_not_cancelled(job_id, db, run_id=run_id)
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

        rscript_bin = which_tool("Rscript")
        has_rscript = bool(rscript_bin)
        runner_script = Path(__file__).resolve().parents[1] / "templates" / "gapit_runner.R"
        can_run_r = bool(rscript_bin) and runner_script.is_file()
        n_pcs = int(gwas_cfg.get("n_pc_covariates") or (params or {}).get("n_pc_covariates") or 3)

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
            write_stage_summary(
                job_id,
                "multilocus",
                {
                    "status": "skipped",
                    "started_at": stage_started_at,
                    "reason": reason,
                    "models": [],
                    "engines": {},
                    "counts": {"n_models": 0, "n_lead_snps": 0},
                },
                db=db,
            )
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
        if can_run_r and rscript_bin is not None:
            try:
                append_log(log_file, "[INFO] Mengeksekusi script GAPIT via Rscript...")
                gapit_results = _run_gapit_models(
                    rscript=rscript_bin,
                    runner_script=runner_script,
                    methods=active_methods,
                    workspace=workspace,
                    log_file=log_file,
                    cancel_check=lambda: is_job_cancelled(job_id, run_id=run_id),
                    ctx=ctx,
                    n_pcs=n_pcs,
                )
                methods_results.update(gapit_results)
                if gapit_results:
                    append_log(
                        log_file,
                        f"[INFO] GAPIT R selesai untuk model: {', '.join(gapit_results.keys())}.",
                    )
            except (JobCancelledError, CLICancelledError):
                raise
            except Exception as r_err:
                returncode = getattr(r_err, "returncode", None)
                if returncode in {-9, 137}:
                    source = _resolve_genotype_bfile(workspace, ctx)
                    snp_count = _count_bfile_snps(source) if source else 0
                    limit_mb = read_container_memory_limit_mb()
                    append_log(
                        log_file,
                        f"[WARN] GAPIT dihentikan OS (SIGKILL, kemungkinan kehabisan memori; "
                        f"limit container {limit_mb or '?'} MB, SNP={snp_count})",
                    )
                else:
                    append_log(log_file, f"[WARN] Eksekusi Rscript/GAPIT gagal, beralih ke Python Native Engine: {r_err}")

        # Model aktif yang belum dihasilkan GAPIT dijalankan dengan Python Native Engine
        remaining_methods = [m for m in active_methods if m.upper() not in methods_results]
        if remaining_methods:
            engine_output = _run_python_multilocus_engine(
                job_id=job_id,
                dataset_id=dataset_id,
                methods=remaining_methods,
                workspace=workspace,
                log_file=log_file,
                ctx=ctx,
                db=db,
                run_id=run_id,
            )
            py_results = engine_output.get("methods_results", {})
            for m_key, m_entry in py_results.items():
                m_entry["engine"] = "python-approx"
                methods_results[m_key] = m_entry
            multi_lead_snps.extend(engine_output.get("lead_snps_multi", []))
            append_log(
                log_file,
                "[WARN] Hasil multi-locus berasal dari aproksimasi Python (bukan GAPIT): "
                "FarmCPU/BLINK = GLM bertahap + PC; MLMM = EMMAX + kofaktor bertahap. "
                "Validasi dengan GAPIT sebelum publikasi.",
            )

        # Pastikan lead SNPs & PVE CSV terisi untuk tiap model (khususnya keluaran GAPIT R)
        trait_label = str(
            ctx.get("trait")
            or ((job.parameters or {}).get("phenotype") or {}).get("trait")
            or "Trait"
        )
        split_map = load_split_map(job_id)
        for m in active_methods:
            m_upper = m.upper()
            entry = methods_results.get(m_upper)
            if not entry:
                continue
            if "significant_snps" not in entry:
                tsv_target = Path(entry.get("tsv_file") or (workspace / f"{m.lower()}_results.tsv"))
                if tsv_target.is_file():
                    extracted = extract_multilocus_leads_from_tsv(
                        tsv_target,
                        method_name=m_upper,
                        trait_name=trait_label,
                        max_top=5,
                        split_map=split_map,
                    )
                    if extracted:
                        multi_lead_snps.extend(extracted)
                        display_m = METHOD_DISPLAY_NAMES.get(m_upper, m)
                        pve_csv = workspace / f"{display_m}_Significant_SNPs_PVE.csv"
                        write_significant_pve_csv(pve_csv, extracted, trait_name=trait_label)
                        entry["significant_snps"] = extracted
                        entry["pve_csv_file"] = str(pve_csv)
            elif isinstance(entry.get("significant_snps"), list):
                for snp_item in entry["significant_snps"]:
                    if isinstance(snp_item, dict) and "orig_chr" not in snp_item:
                        o_chr, o_pos = restore_coord(snp_item.get("chr"), snp_item.get("pos"), split_map)
                        snp_item["orig_chr"] = o_chr
                        snp_item["orig_pos"] = o_pos

        for snp_item in multi_lead_snps:
            if isinstance(snp_item, dict) and "orig_chr" not in snp_item:
                o_chr, o_pos = restore_coord(snp_item.get("chr"), snp_item.get("pos"), split_map)
                snp_item["orig_chr"] = o_chr
                snp_item["orig_pos"] = o_pos

        engines_used = {
            m_upper: str(entry.get("engine", "unknown"))
            for m_upper, entry in methods_results.items()
        }
        for m_upper, eng in engines_used.items():
            append_log(log_file, f"[INFO] Model {m_upper} selesai menggunakan engine: {eng}")

        # Simpan ringkasan multi-locus JSON
        summary_payload = {
            "status": "completed",
            "models_evaluated": active_methods,
            "engines": engines_used,
            "results": methods_results,
            "n_lead_snps": len(multi_lead_snps),
            "lead_snps": multi_lead_snps,
        }
        if any(engine == "python-approx" for engine in engines_used.values()):
            summary_payload["approximation_note"] = (
                "Hasil Python adalah aproksimasi; validasi dengan GAPIT sebelum publikasi."
            )
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
            single_model_label=_single_model_label(job.parameters, ctx),
            split_map=split_map,
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

        write_stage_summary(
            job_id,
            "multilocus",
            {
                "status": "completed",
                "started_at": stage_started_at,
                "models": list(active_methods),
                "engines": engines_used,
                "counts": {
                    "n_models": len(methods_results),
                    "n_lead_snps": len(multi_lead_snps),
                },
            },
            db=db,
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

    except (JobCancelledError, CLICancelledError):
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
    single_model_label: str = "Single-Locus",
    split_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Membandingkan puncak asosiasi Single-Locus (GEMMA/GLM) dengan Multi-Locus (FarmCPU, BLINK, MLMM).
    Mengelompokkan SNP menjadi:
    - consistent: terdeteksi oleh kedua pendekatan (konsisten & andal).
    - multi_only: terdeteksi HANYA oleh multi-locus (potensi efek minor).
    - single_only: terdeteksi HANYA oleh single-locus dan perlu validasi.
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

    significant_multi = [m for m in multi_lead_snps if m.get("is_significant", True)]
    matched_multi_indices: set[int] = set()

    for s in single_lead_snps:
        s_chr = chr_label(s.get("chr", ""))
        s_pos = int(s.get("pos", 0))

        # Cari kecocokan di multi-locus
        matched_multi = None
        for m_idx, m in enumerate(significant_multi):
            m_chr = chr_label(m.get("chr", ""))
            m_pos = int(m.get("pos", 0))
            if s_chr == m_chr and abs(s_pos - m_pos) <= window_bp:
                matched_multi = m
                matched_multi_indices.add(m_idx)
                break

        s_orig_chr, s_orig_pos = _resolve_orig_coord(s, s_chr, s_pos, split_map)

        if matched_multi is not None:
            consistent.append({
                "snp": s.get("snp"),
                "chr": s_chr,
                "pos": s_pos,
                "orig_chr": s_orig_chr,
                "orig_pos": s_orig_pos,
                "p_single": s.get("p"),
                "p_multi": matched_multi.get("p"),
                "maf": matched_multi.get("maf"),
                "nobs": matched_multi.get("nobs"),
                "effect": matched_multi.get("effect", s.get("beta")),
                "h_b_p_value": matched_multi.get("h_b_p_value"),
                "pve_pct": matched_multi.get("pve_pct"),
                "method": matched_multi.get("method", "Multi-Locus"),
                "status": "Konsisten (Single + Multi-Locus)",
                "keterangan": (
                    f"Sangat andal; terkonfirmasi oleh model {single_model_label} "
                    f"dan {matched_multi.get('method', 'Multi-Locus')}."
                ),
            })
        else:
            single_only.append({
                "snp": s.get("snp"),
                "chr": s_chr,
                "pos": s_pos,
                "orig_chr": s_orig_chr,
                "orig_pos": s_orig_pos,
                "p_single": s.get("p"),
                "p_multi": None,
                "effect": s.get("beta"),
                "method": single_model_label,
                "status": "Hanya Single-Locus",
                "keterangan": "Tidak terkonfirmasi multi-locus pada ambang Bonferroni; perlu validasi.",
            })

    for m_idx, m in enumerate(significant_multi):
        if m_idx not in matched_multi_indices:
            m_chr = chr_label(m.get("chr", ""))
            m_pos = int(m.get("pos", 0))
            m_orig_chr, m_orig_pos = _resolve_orig_coord(m, m_chr, m_pos, split_map)
            multi_only.append({
                "snp": m.get("snp"),
                "chr": m_chr,
                "pos": m_pos,
                "orig_chr": m_orig_chr,
                "orig_pos": m_orig_pos,
                "p_single": None,
                "p_multi": m.get("p"),
                "maf": m.get("maf"),
                "nobs": m.get("nobs"),
                "effect": m.get("effect", m.get("beta")),
                "h_b_p_value": m.get("h_b_p_value"),
                "pve_pct": m.get("pve_pct"),
                "method": m.get("method", "Multi-Locus"),
                "status": "Minor-Effect (Hanya Multi-Locus)",
                "keterangan": f"Potensi varian efek minor ({m.get('method', 'Multi-Locus')}) yang tersembunyi pada model univariat.",
            })

    summary: dict[str, Any] = {
        "n_consistent": len(consistent),
        "n_single_only": len(single_only),
        "n_multi_only": len(multi_only),
        "total_peaks": len(consistent) + len(single_only) + len(multi_only),
    }
    if not significant_multi:
        summary["reason"] = "Tidak ada SNP multi-locus yang lolos Bonferroni"

    result_json = {
        "consistent": consistent,
        "single_only": single_only,
        "multi_only": multi_only,
        "summary": summary,
    }
    write_json(out_json_path, result_json)

    # Tulis CSV Ringkasan_Puncak_GWAS.csv
    all_rows = consistent + single_only + multi_only
    with out_csv_path.open("w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow([
            "SNP",
            "Kromosom",
            "Posisi",
            "Metode",
            "P_Single",
            "P_Multi",
            "Status",
            "Keterangan",
            "Orig_Chr",
            "Orig_Pos",
        ])
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
                r.get("orig_chr", r["chr"]),
                r.get("orig_pos", r["pos"]),
            ])

    return result_json
