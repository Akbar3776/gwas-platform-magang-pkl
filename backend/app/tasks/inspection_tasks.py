"""Celery task for Phase 1: Data Inspection and Consistency Verification."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
import uuid

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ResultType
from app.services.phenotype_service import (
    inspect_phenotype,
    load_phenotype_table,
    read_vcf_sample_ids,
)
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    write_json,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.inspection_tasks.run_inspection",
    queue="normalization",
    max_retries=0,
)
def run_inspection(
    self,
    ctx: dict[str, Any] | None = None,
    job_id: str | None = None,
    dataset_id: str | None = None,
    phenotype_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute Phase 1 Data Inspection and sample ID reconciliation."""
    ctx = ctx or {}
    job_id = str(job_id or ctx.get("job_id") or "")
    dataset_id = str(dataset_id or ctx.get("dataset_id") or "")
    phenotype_cfg = phenotype_cfg or ctx.get("phenotype_cfg") or {}

    if not job_id:
        raise ValueError("run_inspection membutuhkan job_id")

    ensure_not_cancelled(job_id)

    db = SessionLocal()
    try:
        job = db.get(AnalysisJob, uuid.UUID(job_id))
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        ds_id = dataset_id or (str(job.dataset_id) if job.dataset_id else "")
        dataset = db.get(Dataset, uuid.UUID(ds_id)) if ds_id else None
        if dataset is None:
            raise ValueError(f"Dataset {ds_id} tidak ditemukan")

        vcf_path = dataset.storage_path
        pheno_path = dataset.phenotype_file_path or job.phenotype_file_path or phenotype_cfg.get("phenotype_path")
        if not vcf_path or not Path(vcf_path).is_file():
            raise FileNotFoundError(f"File VCF dataset tidak ditemukan: {vcf_path}")
        if not pheno_path or not Path(pheno_path).is_file():
            raise FileNotFoundError(f"File fenotipe dataset tidak ditemukan: {pheno_path}")

        out_dir = stage_dir(job_id, "inspection")
        log_file = out_dir / "inspection.log"

        mark_job(
            job_id,
            status=JobStatus.RUNNING_INSPECTION,
            current_step=PipelineStep.INSPECTION,
            work_dir=str(out_dir),
            started_at=datetime.now(timezone.utc),
            error_message=None,
            error_log=None,
        )

        append_log(
            log_file,
            f"=== TAHAP 1: INSPEKSI DATA & KONSISTENSI FENOTIPE ===\n"
            f"Waktu mulai    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Input Genotipe : {vcf_path}\n"
            f"Input Fenotipe : {pheno_path}\n"
            f"Trait Target   : {phenotype_cfg.get('trait') or '(otomatis/pertama)'}\n",
        )

        vcf_ids = read_vcf_sample_ids(vcf_path)
        pheno_df = load_phenotype_table(pheno_path)

        trait_col = phenotype_cfg.get("trait") or ""
        id_col = phenotype_cfg.get("id_column", "IID")
        fid_col = phenotype_cfg.get("fid_column", "FID")
        missing_tokens = phenotype_cfg.get("missing_tokens", ["", ".", "NA", "-9"])

        report = inspect_phenotype(
            vcf_ids=vcf_ids,
            df=pheno_df,
            trait=trait_col,
            id_column=id_col,
            fid_column=fid_col,
            missing_tokens=missing_tokens,
        )

        # Write inspection output files
        sampel_vcf_file = out_dir / "sampel_vcf.txt"
        sampel_vcf_file.write_text("\n".join(vcf_ids) + "\n", encoding="utf-8")

        keep_ids_file = out_dir / "keep_ids.txt"
        # Two columns FID IID for PLINK --keep
        keep_lines = [f"{iid} {iid}" for iid in report["matched_ids"]]
        keep_ids_file.write_text("\n".join(keep_lines) + "\n", encoding="utf-8")

        hanya_vcf_file = out_dir / "id_hanya_vcf.txt"
        hanya_vcf_file.write_text("\n".join(report.get("only_in_vcf_sample", [])) + "\n", encoding="utf-8")

        hanya_pheno_file = out_dir / "id_hanya_fenotipe.txt"
        hanya_pheno_file.write_text("\n".join(report.get("only_in_pheno_sample", [])) + "\n", encoding="utf-8")

        # Create pheno_plink.txt: FID IID <TRAIT>
        pheno_plink_file = out_dir / "pheno_plink.txt"
        matched_set = set(report["matched_ids"])
        matched_rows = ["FID\tIID\t" + report["trait"]]

        matched_id_col = report["id_column"]
        matched_trait_col = report["trait"]

        for _, row in pheno_df.iterrows():
            iid = str(row[matched_id_col]).strip()
            if iid in matched_set:
                raw_val = str(row[matched_trait_col]).strip()
                val_str = raw_val if raw_val and raw_val.upper() not in set(t.upper() for t in missing_tokens) else "NA"
                matched_rows.append(f"{iid}\t{iid}\t{val_str}")
        pheno_plink_file.write_text("\n".join(matched_rows) + "\n", encoding="utf-8")

        report_json_file = out_dir / "inspection_report.json"
        write_json(report_json_file, report)

        append_log(
            log_file,
            f"Ringkasan Pemeriksaan:\n"
            f" - Total Sampel VCF       : {report['n_vcf_samples']}\n"
            f" - Total Baris Fenotipe   : {report['n_pheno_rows']}\n"
            f" - Sampel Cocok & Valid   : {report['n_matched']}\n"
            f" - Sampel Hanya di VCF    : {report['only_in_vcf_count']}\n"
            f" - Sampel Hanya di Pheno  : {report['only_in_pheno_count']}\n"
            f" - Nilai Missing          : {report['missing_count']}\n",
        )

        for w in report["warnings"]:
            append_log(log_file, f"[WARN] {w}")

        if report["blocking_errors"]:
            for err in report["blocking_errors"]:
                append_log(log_file, f"[ERROR] {err}")
            raise RuntimeError(f"Tahap inspeksi gagal karena kesalahan fatal: {'; '.join(report['blocking_errors'])}")

        append_log(log_file, f"[SUCCESS] Inspeksi konsistensi data selesai. {report['n_matched']} sampel siap diproses.")

        upsert_result(
            db,
            job_id=job.id,
            result_type=ResultType.INSPECTION_REPORT,
            file_path=str(report_json_file),
            summary_stats=report,
        )
        job.overall_status = JobStatus.RUNNING_NORMALIZATION
        job.current_step = PipelineStep.NORMALIZATION
        db.commit()

        return {
            **ctx,
            "job_id": job_id,
            "dataset_id": ds_id,
            "keep_ids_path": str(keep_ids_file),
            "pheno_path": str(pheno_plink_file),
            "trait": report["trait"],
            "n_matched": report["n_matched"],
            "inspection_report_path": str(report_json_file),
        }
    except JobCancelledError:
        append_log(log_file, "[CANCELLED] Tahap inspeksi dibatalkan pengguna.")
        raise
    except Exception as exc:
        mark_job(job_id, status=JobStatus.FAILED, failed_step="inspection", error=str(exc))
        logger.exception("Inspeksi gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()

