"""Celery chain for the full GWAS workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from celery import chain
from celery.canvas import Signature

from app.core.config import settings
from app.tasks.gemma_tasks import run_gemma_gwas
from app.tasks.imputation_tasks import run_imputation
from app.tasks.normalization_tasks import run_vcf_normalization
from app.tasks.qc_tasks import run_qc_pipeline


def build_full_pipeline(
    *,
    job_id: str,
    dataset_id: str,
    raw_vcf_path: str,
    qc_params: dict[str, Any],
    imputation_params: dict[str, Any],
    gwas_params: dict[str, Any],
) -> Signature:
    """Build normalization -> QC -> imputation -> GEMMA without executing it.

    Celery prepends each task result to the next signature. Therefore
    ``run_imputation`` receives the QC result dict and ``run_gemma_gwas``
    receives the imputation result dict. A raised exception prevents Celery
    from dispatching all remaining signatures in the chain.
    """
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    imputation_config = {**imputation_params, "output_dir": str(job_root / "imputation")}
    gwas_config = {**gwas_params, "output_dir": str(job_root / "gwas")}

    return chain(
        run_vcf_normalization.s(
            job_id=job_id,
            dataset_id=dataset_id,
        ),
        run_qc_pipeline.s(
            job_id=job_id,
            dataset_id=dataset_id,
            qc_params=qc_params,
        ),
        run_imputation.s(
            job_id=job_id,
            reference_vcf_path=imputation_config.pop("reference_vcf_path"),
            params=imputation_config,
        ),
        run_gemma_gwas.s(
            job_id=job_id,
            phenotype_path=gwas_config.pop("phenotype_path"),
            model=gwas_config.pop("model", "lmm"),
            params=gwas_config,
        ),
    )


def trigger_full_pipeline(
    *,
    job_id: str,
    dataset_id: str,
    raw_vcf_path: str,
    qc_params: dict[str, Any],
    imputation_params: dict[str, Any],
    gwas_params: dict[str, Any],
) -> str:
    """Enqueue the full chain and return its root Celery task id."""
    pipeline = build_full_pipeline(
        job_id=job_id,
        dataset_id=dataset_id,
        raw_vcf_path=raw_vcf_path,
        qc_params=qc_params,
        imputation_params=imputation_params,
        gwas_params=gwas_params,
    )
    return pipeline.apply_async().id


# Compatibility names for older callers.
build_pipeline_chain = build_full_pipeline
start_pipeline = trigger_full_pipeline
