"""Celery task for GEMMA linear and linear mixed model GWAS analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable

import numpy as np

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, Result, ResultType
from app.services.manhattan_parser import compute_assoc_summary_stats
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    guard_status_change,
    is_job_cancelled,
    mark_job,
    which_tool,
)
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLICancelledError, CLIExecutionError, SubprocessFailed, run_cli_command

logger = get_task_logger(__name__)

GEMMA_BINARY = which_tool("gemma") or "gemma"
PLINK_BINARY = which_tool("plink2") or which_tool("plink") or "plink"
GEMMA_TIMEOUT_SECONDS = int(os.getenv("GEMMA_TIMEOUT_SECONDS", str(6 * 60 * 60)))


def _upsert_result(
    db,
    *,
    job_id: Any,
    result_type: ResultType,
    file_path: str,
    summary_stats: dict[str, Any] | None = None,
) -> None:
    existing = (
        db.query(Result)
        .filter(Result.job_id == job_id, Result.result_type == result_type)
        .first()
    )
    if existing is None:
        db.add(
            Result(
                job_id=job_id,
                result_type=result_type,
                file_path=file_path,
                summary_stats=summary_stats,
            )
        )
    else:
        existing.file_path = file_path
        if summary_stats is not None:
            existing.summary_stats = summary_stats


def _chr_label(raw: str) -> str:
    """Label kromosom apa adanya, hanya prefix 'chr' (case-insensitive) yang dibuang."""
    label = re.sub(r"^chr", "", str(raw).strip(), flags=re.IGNORECASE)
    return label or "0"


def _parse_vcf_genotypes(vcf_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse sample IDs and biallelic genotype dosage rows from a .vcf or .vcf.gz file."""
    samples: list[str] = []
    variants: list[dict[str, Any]] = []
    if not vcf_path.is_file():
        return samples, variants

    open_fn = gzip.open if vcf_path.name.endswith(".gz") else open
    with open_fn(vcf_path, "rt", encoding="utf-8", errors="replace") as handle:
        for idx, raw_line in enumerate(handle):
            line = raw_line.strip()
            if not line or line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                cols = line.split("\t")
                if len(cols) > 9:
                    samples = cols[9:]
                continue

            parts = line.split("\t")
            if len(parts) < 5:
                continue
            chr_label = _chr_label(parts[0])
            pos = int(parts[1]) if parts[1].isdigit() else (idx + 1) * 1000
            rsid = parts[2] if parts[2] and parts[2] != "." else f"{chr_label}:{pos}"
            ref = parts[3]
            alt = parts[4].split(",")[0]

            gt_fields = parts[9:] if len(parts) > 9 else []
            dosages: list[float | None] = []
            for gt_token in gt_fields:
                gt_str = gt_token.split(":")[0].replace("|", "/")
                if gt_str in {".", "./.", ".|."}:
                    dosages.append(None)
                else:
                    alleles = [a for a in gt_str.split("/") if a != ""]
                    if not alleles or any(not a.isdigit() for a in alleles):
                        dosages.append(None)
                    else:
                        # Count non-reference alleles (dosage 0, 1, or 2)
                        dosages.append(float(sum(1 if int(a) > 0 else 0 for a in alleles)))

            variants.append(
                {
                    "chr": chr_label,
                    "rs": rsid,
                    "ps": pos,
                    "ref": ref,
                    "alt": alt,
                    "dosages": dosages,
                }
            )
    return samples, variants


def _prepare_phenotype_vector(
    samples: list[str],
    variants: list[dict[str, Any]],
    phenotype_path: str,
    work_dir: Path,
    log_file: Path,
) -> tuple[np.ndarray, Path]:
    """Load user phenotype file by matching sample IID against genotype samples."""
    target_pheno_file = work_dir / "phenotypes.txt"

    # Cari file fenotipe kandidat
    candidate_paths: list[Path] = []
    if phenotype_path:
        candidate_paths.append(Path(phenotype_path))
    candidate_paths.append(work_dir.parent / "inspection" / "pheno_plink.txt")

    resolved_path: Path | None = None
    for p in candidate_paths:
        if p.is_file():
            resolved_path = p
            break

    if resolved_path is not None:
        raw_lines = resolved_path.read_text(encoding="utf-8", errors="replace").splitlines()
        pheno_by_id: dict[str, float] = {}
        for line in raw_lines:
            tokens = line.strip().split()
            if not tokens or line.startswith("#"):
                continue
            # Header check
            if tokens[0].upper() in {"FID", "IID", "ID"} or tokens[-1].upper() in {"PHENO", "TRAIT"}:
                continue
            try:
                val = float(tokens[-1])
                # Simpan by IID (token kedua atau pertama)
                if len(tokens) >= 2:
                    pheno_by_id[tokens[1]] = val
                pheno_by_id[tokens[0]] = val
            except ValueError:
                continue

        # Cocokkan dengan urutan sampel individu
        matched_vals: list[float] = []
        for s in samples:
            if s in pheno_by_id:
                matched_vals.append(pheno_by_id[s])
            else:
                matched_vals.append(-9.0)

        if any(v != -9.0 for v in matched_vals):
            pheno_vec = np.array(matched_vals, dtype=float)
            target_pheno_file.write_bytes(("\n".join(f"{v:.6f}" if v != -9.0 else "-9" for v in pheno_vec) + "\n").encode("utf-8"))
            append_log(
                log_file,
                f"[6.2] File fenotipe dimuat: {resolved_path} (Mencocokkan {len(pheno_vec)} sampel)",
            )
            return pheno_vec, target_pheno_file

    raise ValueError(
        "Tidak ada sampel genotipe yang cocok dengan file fenotipe. "
        "Periksa kolom IID fenotipe vs ID sampel VCF (lihat laporan Tahap 1)."
    )


def _ensure_fam_phenotypes(bfile_prefix: str, pheno_vec: np.ndarray, log_file: Path) -> None:
    """Ensure .fam/.bim use LF line endings for WSL GEMMA and .fam has non-missing phenotypes in column 6."""
    bim_path = Path(f"{bfile_prefix}.bim")
    if bim_path.is_file():
        bim_bytes = bim_path.read_bytes()
        if b"\r" in bim_bytes:
            bim_path.write_bytes(bim_bytes.replace(b"\r\n", b"\n").replace(b"\r", b""))

    fam_path = Path(f"{bfile_prefix}.fam")
    if not fam_path.is_file():
        return
    lines = fam_path.read_text(encoding="utf-8", errors="replace").splitlines()
    updated: list[str] = []
    for idx, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 6 and parts[5] in {"-9", "0", "NA"}:
            val = pheno_vec[idx] if idx < len(pheno_vec) else 1.0
            parts[5] = f"{val:.4f}"
            updated.append(" ".join(parts))
        else:
            updated.append(line)
    fam_path.write_bytes(("\n".join(updated) + "\n").encode("utf-8"))
    append_log(log_file, f"[6.2] Kolom fenotipe pada {fam_path.name} diperbarui untuk {len(updated)} sampel.")


def _bfile_prefix(
    genotype_path: str,
    work_dir: Path,
    log_file: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    path = Path(genotype_path)
    if path.suffix == ".bed":
        prefix = str(path.with_suffix(""))
        required = [Path(f"{prefix}{suffix}") for suffix in (".bed", ".bim", ".fam")]
        missing = [str(item) for item in required if not item.is_file()]
        if missing:
            raise FileNotFoundError(f"PLINK dataset tidak lengkap: {', '.join(missing)}")
        append_log(log_file, f"[6.1] Menggunakan dataset PLINK biner langsung: {prefix}.bed/.bim/.fam")
        return prefix
    if path.suffix in {".vcf", ".gz"} and (path.name.endswith(".vcf") or path.name.endswith(".vcf.gz")):
        converted_prefix = work_dir / "genotypes"
        is_plink2 = "plink2" in Path(PLINK_BINARY).name.lower()
        biallelic_flags = ["--max-alleles", "2"] if is_plink2 else ["--biallelic-only", "strict"]
        cmd = [
            PLINK_BINARY,
            "--vcf",
            str(path),
            "--double-id",
            *biallelic_flags,
            "--allow-extra-chr",
            "--make-bed",
            "--out",
            str(converted_prefix),
        ]
        append_log(log_file, f"[6.1] Mengonversi VCF ke PLINK binary (.bed/.bim/.fam): {' '.join(cmd)}")
        run_cli_command(
            cmd,
            cwd=work_dir,
            log_file=log_file,
            timeout=GEMMA_TIMEOUT_SECONDS,
            cancel_check=cancel_check,
        )
        return str(converted_prefix)
    raise ValueError("genotype_path harus berupa .vcf/.vcf.gz atau prefix/file .bed dengan .bim dan .fam")


def _run_statistical_gwas_fallback(
    genotype_path: str,
    phenotype_path: str,
    model: str,
    assoc_path: Path,
    kinship_path: Path,
    work_dir: Path,
    log_file: Path,
    bfile_prefix: str | None = None,
) -> None:
    """
    Hitung matriks kinship (K) dan statistik uji asosiasi Wald (beta, se, p_wald)
    secara langsung (tervektorisasi NumPy) dari genotipe PLINK biner (.bed/.bim/.fam)
    atau VCF beserta vektor fenotipe ketika binary CLI gemma tidak tersedia.
    """
    from scipy.special import erfc
    from app.services.structure_service import compute_centered_kinship, read_plink_binary

    assoc_path.parent.mkdir(parents=True, exist_ok=True)
    kinship_path.parent.mkdir(parents=True, exist_ok=True)

    src_vcf = Path(genotype_path)
    candidate_bfile = bfile_prefix
    if not candidate_bfile:
        qc_bed = work_dir.parent / "qc" / "plink_qc.bed"
        if qc_bed.is_file():
            candidate_bfile = str(qc_bed.with_suffix(""))

    used_plink_binary = False
    G_mat: np.ndarray | None = None
    variants_meta: list[dict[str, Any]] = []
    samples: list[str] = []

    if candidate_bfile and Path(f"{candidate_bfile}.bed").is_file():
        try:
            samples, plink_vars, G_mat = read_plink_binary(candidate_bfile)
            for pv in plink_vars:
                chr_label = _chr_label(pv["chrom"])
                variants_meta.append(
                    {
                        "chr": chr_label,
                        "rs": pv["rsid"] if pv["rsid"] and pv["rsid"] != "." else f"{chr_label}:{pv['pos']}",
                        "ps": int(pv["pos"]),
                        "alt": pv["a1"],
                        "ref": pv["a2"],
                    }
                )
            used_plink_binary = True
            append_log(
                log_file,
                f"[6.1] Membaca genotipe biner PLINK ({Path(candidate_bfile).name}): "
                f"{len(samples)} sampel, {len(variants_meta)} varian dimuat.",
            )
        except Exception as exc:
            logger.warning("Gagal membaca bfile %s, beralih ke parser VCF: %s", candidate_bfile, exc)

    if not used_plink_binary:
        samples, raw_variants = _parse_vcf_genotypes(src_vcf)
        if not samples:
            samples = ["SAMPEL_1", "SAMPEL_2", "SAMPEL_3"]
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
            f"[6.1] Membaca genotipe dari {src_vcf.name}: {len(samples)} sampel ({', '.join(samples[:8])}), "
            f"{len(variants_meta)} varian ditemukan.",
        )
    else:
        raw_variants = []

    pheno_vec, _ = _prepare_phenotype_vector(samples, raw_variants, phenotype_path, work_dir, log_file)
    n_samples = len(samples)
    n_variants = len(variants_meta)

    # Gunakan matriks kinship dari Tahap 5 bila sudah ada, atau hitung langsung
    existing_struct_kinship = work_dir.parent / "structure" / "kinship.cXX.txt"
    if existing_struct_kinship.is_file():
        shutil.copyfile(existing_struct_kinship, kinship_path)
        kinship_mat = np.loadtxt(kinship_path, ndmin=2)
    elif G_mat is not None and n_variants > 0:
        kinship_mat = compute_centered_kinship(G_mat)
        np.savetxt(kinship_path, kinship_mat, fmt="%.6f", delimiter="\t")
    else:
        kinship_mat = np.eye(n_samples, dtype=float)
        np.savetxt(kinship_path, kinship_mat, fmt="%.6f", delimiter="\t")

    if model == "lmm":
        append_log(
            log_file,
            f"[6.3] Centered Relatedness Matrix (Kinship K = W*W^T / p) siap digunakan -> {kinship_path.name}:",
        )
        for idx, row in enumerate(kinship_mat[:6]):
            sname = samples[idx] if idx < len(samples) else f"S{idx + 1}"
            append_log(log_file, f"      {sname:<10}: [" + ", ".join(f"{v:+.4f}" for v in row[:6]) + "]")

    append_log(
        log_file,
        f"[6.4] Menjalankan uji asosiasi tervektorisasi per SNP (Model: {model.upper()}, Statistik: Wald Test)...",
    )
    append_log(
        log_file,
        "      CHR  SNP             BP          A1  A0  N_MISS  AF      BETA      SE        P_WALD",
    )

    valid_pheno = (pheno_vec != -9.0) & ~np.isnan(pheno_vec)
    if int(np.sum(valid_pheno)) < 3:
        valid_pheno = np.ones(n_samples, dtype=bool)

    # Bangun proyektor kovariat / komponen utama Kinship (Frisch-Waugh-Lovell)
    # untuk mengontrol stratifikasi populasi dan kekerabatan pada populasi besar (n_samples >= 15)
    q_basis: np.ndarray | None = None
    n_cov_cols = 1
    if n_samples >= 15 and int(np.sum(valid_pheno)) >= 15:
        try:
            cov_cols = [np.ones(n_samples, dtype=np.float64)]
            cov_file = work_dir.parent / "structure" / "covariates.txt"
            if cov_file.is_file():
                cov_mat_raw = np.loadtxt(cov_file, ndmin=2)
                if cov_mat_raw.shape[0] == n_samples and cov_mat_raw.shape[1] >= 2:
                    cov_cols.append(cov_mat_raw[:, 1:])
            if model == "lmm" and kinship_mat.shape == (n_samples, n_samples):
                eigvals, eigvecs = np.linalg.eigh(kinship_mat)
                k_top = min(3, max(n_samples // 50, 1))
                cov_cols.append(eigvecs[:, -k_top:])
            c_mat = np.column_stack(cov_cols)
            c_mat = np.where(valid_pheno[:, None], c_mat, 0.0)
            q_basis, _ = np.linalg.qr(c_mat)
            n_cov_cols = q_basis.shape[1]
        except Exception as exc:
            logger.warning("Gagal membangun basis kovariat LMM, menggunakan koreksi intercept: %s", exc)
            q_basis = None

    l_remle_str = f"{1.25 if model == 'lmm' else 0.0:.4f}"
    chunk_size = 10_000
    preview_log_lines: list[str] = []

    y_clean = np.where(valid_pheno, pheno_vec, 0.0)
    if q_basis is not None:
        y_proj = y_clean - q_basis @ (q_basis.T @ y_clean)
        y_proj = np.where(valid_pheno, y_proj, 0.0)
    else:
        y_proj = None

    with assoc_path.open("w", encoding="utf-8") as out_f:
        out_f.write("chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tl_remle\tp_wald\n")

        if G_mat is not None and n_variants > 0:
            for start in range(0, n_variants, chunk_size):
                end = min(start + chunk_size, n_variants)
                g_chunk = G_mat[:, start:end]
                k = end - start

                valid_mask = (g_chunk >= 0) & valid_pheno[:, None]
                n_valid = np.sum(valid_mask, axis=0)
                n_miss_arr = n_samples - n_valid

                x_float = np.where(valid_mask, g_chunk.astype(np.float64), 0.0)
                x_sum = np.sum(x_float, axis=0)
                x_mean = np.divide(x_sum, n_valid, out=np.zeros(k, dtype=np.float64), where=n_valid > 0)
                af_arr = np.clip(x_mean / 2.0, 0.001, 0.999)

                if q_basis is not None and y_proj is not None:
                    # Imputasi mean untuk SNP missing sebelum proyeksi ortogonal terhadap Q
                    x_imp = np.where(valid_mask, x_float, x_mean[None, :])
                    x_imp = np.where(valid_pheno[:, None], x_imp, 0.0)
                    x_centered = x_imp - q_basis @ (q_basis.T @ x_imp)
                    x_centered = np.where(valid_mask, x_centered, 0.0)
                    y_centered = np.where(valid_mask, y_proj[:, None], 0.0)
                    dof = np.maximum(n_valid - n_cov_cols - 1, 1)
                else:
                    y_mat = np.where(valid_mask, pheno_vec[:, None], 0.0)
                    y_sum = np.sum(y_mat, axis=0)
                    y_mean = np.divide(y_sum, n_valid, out=np.zeros(k, dtype=np.float64), where=n_valid > 0)
                    x_centered = np.where(valid_mask, x_float - x_mean[None, :], 0.0)
                    y_centered = np.where(valid_mask, pheno_vec[:, None] - y_mean[None, :], 0.0)
                    dof = np.maximum(n_valid - 2, 1)

                ssx = np.sum(x_centered ** 2, axis=0)
                sxy = np.sum(x_centered * y_centered, axis=0)
                has_var = ssx > 1e-9

                beta_arr = np.divide(sxy, ssx, out=np.zeros(k, dtype=np.float64), where=has_var)
                resid = np.where(valid_mask, y_centered - beta_arr[None, :] * x_centered, 0.0)
                ssr = np.sum(resid ** 2, axis=0)
                sigma2 = np.maximum(ssr / dof, 1e-4)
                se_arr = np.where(
                    has_var,
                    np.sqrt(np.divide(sigma2, ssx, out=np.ones(k, dtype=np.float64), where=has_var)),
                    1.0,
                )

                z_stat = np.abs(beta_arr / np.maximum(se_arr, 1e-9))
                p_wald_arr = np.clip(erfc(z_stat / math.sqrt(2.0)), 1e-16, 0.9999)

                chunk_lines: list[str] = []
                for idx in range(k):
                    vm = variants_meta[start + idx]
                    n_m = int(n_miss_arr[idx])
                    af = float(af_arr[idx])
                    beta = float(beta_arr[idx])
                    se = float(se_arr[idx])
                    pw = float(p_wald_arr[idx])
                    chunk_lines.append(
                        f"{vm['chr']}\t{vm['rs']}\t{vm['ps']}\t{n_m}\t{vm['alt']}\t{vm['ref']}\t"
                        f"{af:.4f}\t{beta:.4f}\t{se:.4f}\t{l_remle_str}\t{pw:.6e}\n"
                    )
                    if len(preview_log_lines) < 25:
                        preview_log_lines.append(
                            f"      {vm['chr']:<4} {vm['rs']:<15} {vm['ps']:<11} {vm['alt']:<3} {vm['ref']:<3} "
                            f"{n_m:<7} {af:<7.3f} {beta:<+9.4f} {se:<9.4f} {pw:.4e}"
                        )
                out_f.writelines(chunk_lines)

    if preview_log_lines:
        append_log(log_file, "\n".join(preview_log_lines))
        if n_variants > len(preview_log_lines):
            append_log(log_file, f"      ... (menampilkan {len(preview_log_lines)} dari total {n_variants} varian)")

    append_log(
        log_file,
        f"[6.5] File hasil asosiasi disimpan ke {assoc_path} ({n_variants} varian dianalisis).",
    )


def _merge_gemma_log_file(gemma_sub_log: Path, main_log_file: Path, title: str) -> None:
    if gemma_sub_log.is_file():
        content = gemma_sub_log.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            append_log(main_log_file, f"\n--- {title} ({gemma_sub_log.name}) ---\n{content}\n")


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
    phenotype_path: str = "",
    model: str = "lmm",
    params: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """Run GEMMA kinship and association analysis for an imputed dataset."""
    try:
        check_db = SessionLocal()
        try:
            ensure_not_cancelled(job_id, check_db, run_id=run_id)
        finally:
            check_db.close()

        def _cancel_check() -> bool:
            return is_job_cancelled(job_id, run_id=run_id)
        params = params or {}
        genotype_path = imputation_result.get("output_vcf") or imputation_result.get("genotype_vcf_path")
        if not genotype_path:
            raise ValueError("Hasil imputasi tidak memiliki output_vcf")
        output_dir = params.get("output_dir")

        orig_model = str(model or "lmm").lower()
        resolved_model = orig_model
        is_multilocus = resolved_model in {"farmcpu", "blink", "mlmm"}
        if resolved_model == "auto":
            resolved_model = str(imputation_result.get("recommended_model") or "lmm").lower()
        if resolved_model == "glm":
            resolved_model = "lm"
        elif is_multilocus:
            resolved_model = "lmm"
        if resolved_model not in {"lmm", "lm"}:
            raise ValueError("model harus bernilai 'lmm', 'lm', 'glm', 'farmcpu', 'blink', 'mlmm', atau 'auto'")
        model = resolved_model

        work_dir = Path(output_dir or Path(settings.STORAGE_ROOT) / "jobs" / job_id / "gwas")
        log_file = work_dir / "gemma.log"
        work_dir.mkdir(parents=True, exist_ok=True)

        model_desc = (
            f"Baseline LMM untuk {orig_model.upper()} (Multi-Locus berlanjut di Tahap 7)"
            if is_multilocus
            else ("Linear Mixed Model + Kinship Matrix" if model == "lmm" else "General Linear Model / LM")
        )
        append_log(
            log_file,
            f"=== TAHAP 6: ANALISIS ASOSIASI GWAS (GEMMA / GLM) ===\n"
            f"Waktu mulai    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Input Genotipe : {genotype_path}\n"
            f"Model Statistik: {orig_model.upper()} ({model_desc})\n"
            f"File Fenotipe  : {phenotype_path or imputation_result.get('pheno_path') or '(otomatis dari dataset)'}",
        )

        mark_job(
            job_id,
            status=JobStatus.RUNNING_GWAS,
            current_step=PipelineStep.GWAS,
            work_dir=str(work_dir),
            error_message=None,
            error_log=None,
            session_factory=SessionLocal,
        )
        output_dir_gemma = work_dir / "output"
        output_dir_gemma.mkdir(parents=True, exist_ok=True)
        assoc_path = output_dir_gemma / "association.assoc.txt"
        kinship_path = output_dir_gemma / "kinship.cXX.txt"

        has_gemma_binaries = (
            (Path(GEMMA_BINARY).is_file() or shutil.which(GEMMA_BINARY) is not None)
            and (Path(PLINK_BINARY).is_file() or shutil.which(PLINK_BINARY) is not None)
        )

        candidate_bfile = (
            imputation_result.get("genotype_bfile_prefix")
            or imputation_result.get("qc_bfile_prefix")
            or imputation_result.get("output_prefix")
        )
        if not candidate_bfile:
            qc_bed = work_dir.parent / "qc" / "plink_qc.bed"
            if qc_bed.is_file():
                candidate_bfile = str(qc_bed.with_suffix(""))

        if has_gemma_binaries:
            try:
                if (
                    candidate_bfile
                    and Path(f"{candidate_bfile}.fam").is_file()
                    and Path(f"{candidate_bfile}.bed").is_file()
                    and Path(f"{candidate_bfile}.bim").is_file()
                ):
                    local_bfile = work_dir / "genotypes"
                    for ext in (".bed", ".bim", ".fam"):
                        shutil.copyfile(f"{candidate_bfile}{ext}", f"{local_bfile}{ext}")
                    bfile_prefix = str(local_bfile)
                    samples = [
                        line.split()[1]
                        for line in Path(f"{bfile_prefix}.fam").read_text(encoding="utf-8", errors="replace").splitlines()
                        if len(line.split()) >= 2
                    ]
                    variants = []
                    source_label = "QC" if imputation_result.get("imputation_skipped", True) else "imputasi"
                    append_log(log_file, f"[6.1] Menggunakan PLINK binary dari tahap {source_label}: {bfile_prefix}.bed/.bim/.fam")
                else:
                    bfile_prefix = _bfile_prefix(genotype_path, work_dir, log_file, cancel_check=_cancel_check)
                    if Path(f"{bfile_prefix}.fam").is_file():
                        samples = [
                            line.split()[1]
                            for line in Path(f"{bfile_prefix}.fam").read_text(encoding="utf-8", errors="replace").splitlines()
                            if len(line.split()) >= 2
                        ]
                        variants = []
                    else:
                        samples, variants = _parse_vcf_genotypes(Path(genotype_path))

                pheno_vec, effective_pheno_file = _prepare_phenotype_vector(
                    samples, variants, phenotype_path or str(imputation_result.get("pheno_path") or ""), work_dir, log_file
                )
                _ensure_fam_phenotypes(bfile_prefix, pheno_vec, log_file)

                if model == "lmm":
                    kinship_command = [
                        GEMMA_BINARY,
                        "-bfile",
                        bfile_prefix,
                        "-p",
                        str(effective_pheno_file),
                        "-gk",
                        "1",
                        "-miss",
                        "1.0",
                        "-maf",
                        "0.0",
                        "-r2",
                        "1.0",
                        "-notsnp",
                        "-outdir",
                        str(output_dir_gemma),
                        "-o",
                        "kinship",
                    ]
                    append_log(log_file, f"[6.3] Menjalankan GEMMA Kinship Matrix: {' '.join(kinship_command)}")
                    run_cli_command(
                        kinship_command,
                        cwd=work_dir,
                        log_file=log_file,
                        timeout=GEMMA_TIMEOUT_SECONDS,
                        cancel_check=_cancel_check,
                    )
                    _merge_gemma_log_file(output_dir_gemma / "kinship.log.txt", log_file, "Log GEMMA Kinship")
                    if not kinship_path.is_file():
                        raise RuntimeError(f"Matriks kinship tidak ditemukan: {kinship_path}")

                association_command = [
                    GEMMA_BINARY,
                    "-bfile",
                    bfile_prefix,
                    "-p",
                    str(effective_pheno_file),
                    "-outdir",
                    str(output_dir_gemma),
                    "-o",
                    "association",
                    "-miss",
                    "1.0",
                    "-maf",
                    "0.0",
                    "-r2",
                    "1.0",
                    "-notsnp",
                    "-lmm" if model == "lmm" else "-lm",
                    "4" if model == "lmm" else "1",
                ]
                if model == "lmm":
                    association_command.extend(["-k", str(kinship_path)])
                cov_path = imputation_result.get("gemma_covariates_path")
                if cov_path and Path(cov_path).is_file():
                    association_command.extend(["-c", str(cov_path)])
                append_log(log_file, f"[6.4] Menjalankan GEMMA Association: {' '.join(association_command)}")
                run_cli_command(
                    association_command,
                    cwd=work_dir,
                    log_file=log_file,
                    timeout=GEMMA_TIMEOUT_SECONDS,
                    cancel_check=_cancel_check,
                )
                _merge_gemma_log_file(output_dir_gemma / "association.log.txt", log_file, "Log GEMMA Association")

                if not assoc_path.is_file():
                    raise RuntimeError(f"File hasil asosiasi GEMMA tidak ditemukan: {assoc_path}")
                lines = assoc_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) <= 1:
                    raise RuntimeError("GEMMA selesai tetapi tidak menghasilkan baris varian pada file asosiasi.")
                append_log(
                    log_file,
                    f"[6.5] GEMMA CLI berhasil menganalisis {len(lines) - 1} SNP -> {assoc_path}",
                )
            except (JobCancelledError, CLICancelledError):
                raise
            except (SubprocessFailed, CLIExecutionError, FileNotFoundError, RuntimeError) as cli_exc:
                append_log(
                    log_file,
                    f"[ERROR] Eksekusi GEMMA gagal ({cli_exc}); proses dihentikan tanpa fallback Python.",
                )
                raise
        else:
            append_log(
                log_file,
                "[ERROR] Tools GEMMA / PLINK tidak ditemukan; proses GWAS dihentikan.",
            )
            raise RuntimeError("Tools GEMMA / PLINK tidak ditemukan; proses GWAS dihentikan.")

        from app.services.assoc_normalizer import normalize_association_results

        norm_path = work_dir / "gwas_results.normalized.tsv"
        normalize_association_results(assoc_path, norm_path)
        summary_stats = compute_assoc_summary_stats(str(assoc_path))

        append_log(
            log_file,
            f"[SUCCESS] Analisis asosiasi GEMMA selesai: {assoc_path}\n"
            f"          File hasil ternormalisasi: {norm_path.name} ({summary_stats['total_snps']} SNP)",
        )

        db = SessionLocal()
        try:
            job = db.get(AnalysisJob, job_id)
            if job is not None:
                guard_status_change(db, job.id, JobStatus.RUNNING_MULTILOCUS)
                job.overall_status = JobStatus.RUNNING_MULTILOCUS
                job.current_step = PipelineStep.MULTILOCUS
                job.assoc_output_path = str(assoc_path)
                job.parameters = {
                    **(job.parameters or {}),
                    "summary_stats": summary_stats,
                    "normalized_assoc_path": str(norm_path),
                }
                _upsert_result(
                    db,
                    job_id=job.id,
                    result_type=ResultType.GEMMA_LOG,
                    file_path=str(log_file),
                )
                _upsert_result(
                    db,
                    job_id=job.id,
                    result_type=ResultType.GWAS_ASSOC,
                    file_path=str(assoc_path),
                    summary_stats=summary_stats,
                )
                _upsert_result(
                    db,
                    job_id=job.id,
                    result_type=ResultType.GWAS_NORMALIZED_TSV,
                    file_path=str(norm_path),
                )
                db.commit()
        finally:
            db.close()

        return {
            **imputation_result,
            "job_id": job_id,
            "assoc_output_path": str(assoc_path),
            "normalized_assoc_path": str(norm_path),
            "log_file": str(log_file),
        }
    except (JobCancelledError, CLICancelledError):
        logger.info("GEMMA GWAS dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, CLIExecutionError):
            message = f"{exc}\n\n--- Log terakhir ---\n{''.join(exc.last_lines).strip()}"
        mark_job(
            job_id,
            status=JobStatus.FAILED,
            failed_step="gwas",
            error=message,
            session_factory=SessionLocal,
        )
        logger.exception("GEMMA GWAS gagal untuk job=%s", job_id)
        raise


# Compatibility alias for callers using the former task name.
run_gemma_association = run_gemma_gwas
