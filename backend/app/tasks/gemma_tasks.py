"""Celery task for GEMMA linear and linear mixed model GWAS analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import os
from pathlib import Path
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
    chr_label,
    ensure_not_cancelled,
    guard_status_change,
    is_job_cancelled,
    mark_job,
    which_tool,
    write_stage_summary,
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


_chr_label = chr_label


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
                with assoc_path.open("r", encoding="utf-8", errors="replace") as assoc_fh:
                    nonempty_lines = sum(1 for raw_line in assoc_fh if raw_line.strip())
                if nonempty_lines <= 1:
                    raise RuntimeError("GEMMA selesai tetapi tidak menghasilkan baris varian pada file asosiasi.")
                append_log(
                    log_file,
                    f"[6.5] GEMMA CLI berhasil menganalisis {nonempty_lines - 1} SNP -> {assoc_path}",
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
                write_stage_summary(
                    job_id,
                    "gwas",
                    {
                        "status": "completed",
                        "model": model,
                        "engine": "gemma",
                        "lambda_gc": float(summary_stats.get("lambda_gc", 1.0)),
                        "counts": {
                            "total_snps": int(summary_stats.get("total_snps", 0)),
                            "significant_snps": int(summary_stats.get("significant_snps", 0)),
                        },
                    },
                    db=db,
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
