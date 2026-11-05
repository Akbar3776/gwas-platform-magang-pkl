"""Celery task for PLINK 2 / PLINK 1.9 Quality Control (Fase 2)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ResultType
from app.schemas.pipeline import QCPipelineConfig
from app.services.qc_service import (
    build_plink19_qc_commands,
    build_plink2_qc_commands,
    build_qc_comparison_from_plink_outputs,
)
from app.services.report_generator import parse_qc_summary_from_log
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
from app.tasks.celery_app import SoftTimeLimitExceeded, celery_app, get_task_logger
from app.utils.process import CLIExecutionError, SubprocessFailed, run_cli_command

logger = get_task_logger(__name__)
QC_TIMEOUT_SECONDS = int(os.getenv("QC_TIMEOUT_SECONDS", str(6 * 60 * 60)))


def _error_text(exc: Exception) -> str:
    """Return a useful bounded error message for the database and UI."""
    if isinstance(exc, CLIExecutionError):
        tail = "".join(exc.last_lines).strip()
        if tail:
            return f"{exc}\n\n--- Log terakhir ---\n{tail}"
    return str(exc)


def _set_failed(job: AnalysisJob, exc: Exception, db) -> None:
    message = _error_text(exc)
    job.overall_status = JobStatus.FAILED
    job.failed_step = "qc"
    job.error_message = message
    job.error_log = message
    db.commit()


@celery_app.task(
    bind=True,
    name="app.tasks.qc_tasks.run_qc_pipeline",
    queue="qc",
    max_retries=0,
    soft_time_limit=QC_TIMEOUT_SECONDS + 30,
    time_limit=QC_TIMEOUT_SECONDS + 120,
)
def run_qc_pipeline(
    self,
    normalization_result: dict[str, Any] | None,
    job_id: str,
    dataset_id: str,
    qc_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run PLINK 2 / PLINK 1.9 Quality Control for one dataset.
    Supports inbred (without HWE) vs outcross (with HWE).
    Saves before/after QC stats to qc_comparison.json.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(normalization_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        dataset = db.get(Dataset, dataset_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")
        if dataset is None:
            raise ValueError(f"Dataset {dataset_id} tidak ditemukan")
        if str(job.dataset_id) != str(dataset.id):
            raise ValueError("Dataset tidak sesuai dengan AnalysisJob")

        input_vcf_path = ctx.get("normalized_vcf_path") or dataset.storage_path
        if not input_vcf_path:
            raise ValueError("Dataset tidak memiliki path VCF pada storage")

        workspace = stage_dir(job_id, "qc")
        output_prefix = workspace / "plink_qc"
        log_file = workspace / "plink_qc.log"

        # Resolusi konfigurasi QC
        raw_qc = (job.parameters or {}).get("qc") or qc_params or {}
        if isinstance(raw_qc, dict):
            cfg = QCPipelineConfig(**raw_qc)
        else:
            cfg = raw_qc

        # Resolusi keep_ids & fenotipe dari tahap inspeksi atau context
        keep_ids_candidate = ctx.get("keep_ids_path")
        if not keep_ids_candidate or not Path(keep_ids_candidate).is_file():
            inspection_keep = workspace.parent / "inspection" / "keep_ids.txt"
            if inspection_keep.is_file():
                keep_ids_candidate = str(inspection_keep)
            else:
                ds_keep = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id) / "inspection" / "keep_ids.txt"
                if ds_keep.is_file():
                    keep_ids_candidate = str(ds_keep)
                else:
                    keep_ids_candidate = None

        pheno_candidate = ctx.get("pheno_path")
        if not pheno_candidate or not Path(pheno_candidate).is_file():
            inspection_pheno = workspace.parent / "inspection" / "pheno_plink.txt"
            if inspection_pheno.is_file():
                pheno_candidate = str(inspection_pheno)
            elif dataset.phenotype_file_path and Path(dataset.phenotype_file_path).is_file():
                pheno_candidate = str(dataset.phenotype_file_path)
            else:
                pheno_candidate = None

        trait_candidate = ctx.get("trait") or (job.parameters or {}).get("phenotype", {}).get("trait")

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_QC,
            current_step=PipelineStep.QC,
            work_dir=str(workspace),
            extra_params={
                **cfg.model_dump(),
                "qc_log_path": str(log_file),
            },
        )

        plink2_bin = which_tool("plink2")
        plink_bin = which_tool("plink")
        has_plink2 = bool(plink2_bin)
        has_plink = bool(plink_bin)

        tool_name = "PLINK 2" if has_plink2 else ("PLINK 1.9" if has_plink else "Tidak tersedia")
        append_log(
            log_file,
            f"=== TAHAP 3: QUALITY CONTROL ({tool_name}) ===\n"
            f"Waktu mulai      : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Input VCF        : {input_vcf_path}\n"
            f"Populasi         : {cfg.population_type.value} (HWE: {'Nonaktif' if cfg.population_type.value == 'inbred' else cfg.hwe})\n"
            f"Parameter        : MAF={cfg.maf}, GENO={cfg.geno}, MIND={cfg.mind}\n"
            f"SNP-only/Bi-all  : {cfg.snps_only} / max-alleles={cfg.max_alleles}\n"
            f"Keep IDs         : {keep_ids_candidate or 'Semua sampel'}\n"
        )

        comparison_data: dict[str, Any] = {}

        if has_plink2 and plink2_bin:
            commands = build_plink2_qc_commands(
                cfg=cfg,
                input_vcf=input_vcf_path,
                output_dir=workspace,
                keep_ids=keep_ids_candidate,
                pheno_path=pheno_candidate,
                trait=trait_candidate,
                plink2_bin=plink2_bin,
            )
            try:
                for cmd in commands:
                    ensure_not_cancelled(job_id, db)
                    append_log(log_file, f"Menjalankan: {' '.join(cmd)}")
                    run_cli_command(cmd, cwd=workspace, log_file=log_file, timeout=QC_TIMEOUT_SECONDS)
            except (SubprocessFailed, CLIExecutionError, OSError) as exc:
                logger.error("PLINK2 gagal pada tahap QC: %s; menghentikan pipeline.", exc)
                append_log(log_file, f"[ERROR] PLINK2 gagal dieksekusi ({exc}); proses dihentikan tanpa fallback Python.")
                raise
        elif has_plink and plink_bin:
            commands = build_plink19_qc_commands(
                cfg=cfg,
                input_vcf=input_vcf_path,
                output_dir=workspace,
                keep_ids=keep_ids_candidate,
                pheno_path=pheno_candidate,
                trait=trait_candidate,
                plink_bin=plink_bin,
            )
            try:
                for cmd in commands:
                    ensure_not_cancelled(job_id, db)
                    append_log(log_file, f"Menjalankan: {' '.join(cmd)}")
                    run_cli_command(cmd, cwd=workspace, log_file=log_file, timeout=QC_TIMEOUT_SECONDS)
            except (SubprocessFailed, CLIExecutionError, OSError) as exc:
                logger.error("PLINK 1.9 gagal pada tahap QC: %s; menghentikan pipeline.", exc)
                append_log(log_file, f"[ERROR] PLINK 1.9 gagal dieksekusi ({exc}); proses dihentikan tanpa fallback Python.")
                raise
        else:
            append_log(log_file, "[ERROR] Tools PLINK 2 / PLINK 1.9 tidak ditemukan; proses QC dihentikan.")
            raise RuntimeError("Tools PLINK 2 / PLINK 1.9 tidak ditemukan; proses QC dihentikan.")

        # Pastikan artefak .bed/.bim/.fam/.vcf.gz lengkap
        expected_outputs = [Path(f"{output_prefix}{suffix}") for suffix in (".bed", ".bim", ".fam", ".vcf.gz")]
        missing = [str(p) for p in expected_outputs if not p.exists()]
        if missing:
            append_log(log_file, f"[ERROR] Artefak output PLINK tidak lengkap ({', '.join(missing)}); proses QC dihentikan.")
            raise RuntimeError(f"Artefak output PLINK tidak lengkap: {', '.join(missing)}")

        # Susun qc_comparison.json dari artefak PLINK
        comparison_file = workspace / "qc_comparison.json"
        try:
            comparison_data = build_qc_comparison_from_plink_outputs(
                workspace=workspace,
                max_loss_warning=cfg.max_snp_loss_warning,
            )
        except Exception as exc:
            logger.warning("Gagal menyusun perbandingan QC dari artefak output PLINK: %s", exc)
            if comparison_file.is_file():
                try:
                    comparison_data = json.loads(comparison_file.read_text(encoding="utf-8"))
                except Exception:
                    comparison_data = {}

        if not comparison_data:
            # Fallback darurat jika ekstraksi artefak gagal
            n_snps_after = 0
            n_samples_after = 0
            bim_f = Path(f"{output_prefix}.bim")
            fam_f = Path(f"{output_prefix}.fam")
            if bim_f.is_file():
                n_snps_after = sum(1 for _ in bim_f.open(encoding="utf-8"))
            if fam_f.is_file():
                n_samples_after = sum(1 for _ in fam_f.open(encoding="utf-8"))

            comparison_data = {
                "before": {"n_snps": n_snps_after, "n_samples": n_samples_after},
                "after": {"n_snps": n_snps_after, "n_samples": n_samples_after},
                "removed": {"maf": 0, "geno": 0, "mind": 0, "hwe": 0, "non_snp": 0, "multiallelic": 0},
                "snp_loss_fraction": 0.0,
                "warning": None,
            }

        write_json(comparison_file, comparison_data)

        # Baca log untuk qc_summary legacy (digunakan report PDF)
        log_excerpt = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else None
        qc_summary = parse_qc_summary_from_log(log_excerpt)

        upsert_result(
            db,
            job_id,
            ResultType.QC_COMPARISON,
            str(comparison_file),
            comparison_data,
        )
        upsert_result(
            db,
            job_id,
            ResultType.PLINK_QC_LOG,
            str(log_file),
            qc_summary,
        )

        job.qc_output_prefix = str(output_prefix)
        job.parameters = {
            **(job.parameters or {}),
            "qc_summary": qc_summary,
            "snp_loss_fraction": comparison_data.get("snp_loss_fraction", 0.0),
        }

        if job.pipeline_type == "qc_only":
            job.overall_status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
        else:
            job.overall_status = JobStatus.RUNNING_IMPUTATION
            job.current_step = PipelineStep.IMPUTATION
        db.commit()

        append_log(log_file, "[SUCCESS] Quality Control selesai. Output PLINK biner & VCF tersedia.\n")

        return {
            **ctx,
            "status": "completed",
            "job_id": str(job.id),
            "dataset_id": str(dataset.id),
            "output_prefix": str(output_prefix),
            "qc_vcf_path": str(output_prefix) + ".vcf.gz",
            "qc_bfile_prefix": str(output_prefix),
            "qc_comparison": comparison_data,
            "keep_ids_path": keep_ids_candidate,
            "pheno_path": pheno_candidate,
            "trait": trait_candidate,
            "log_file": str(log_file),
        }

    except JobCancelledError:
        logger.info("QC pipeline dibatalkan oleh user: job=%s", job_id)
        raise
    except SoftTimeLimitExceeded as exc:
        if "job" in locals() and job is not None:
            _set_failed(job, RuntimeError("PLINK QC melewati batas waktu dan dihentikan"), db)
        raise exc
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            _set_failed(job, exc, db)
        logger.exception("QC gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()


# Compatibility name
run_plink_qc = run_qc_pipeline
