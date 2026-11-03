"""Celery chain for the full GWAS workflow (10 Phases).

Workflow:
1. run_inspection: sample reconciliation, missing/duplicate ID checks, phenotype stats
2. run_vcf_normalization: validate, sort, normalize (bcftools), index
3. run_qc_pipeline: PLINK 2 / 1.9 Quality Control (inbred vs outcross)
4. run_imputation: Beagle 5.4 imputation (none, reference, reference-free)
5. run_structure_analysis: LD pruning, PCA, Centered Kinship, LD decay curve, Model recommendation
6. run_gemma_gwas: GLM/LMM association analysis with covariates + canonical normalizer
7. run_multilocus_gwas: Multi-locus FarmCPU/BLINK via R (or honest skip)
8. run_postgwas_analysis: unclamped Lambda GC, dynamic thresholds, LD-aware clumping & lead SNPs
9. run_visualization: high-performance Parquet cache for Manhattan & QQ plots
10. run_annotation: GFF3 candidate gene mapping, classification (Genik/Near-gen/Intergenik), GO list
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.models import JobStatus, PipelineStep
from app.tasks.annotation_tasks import run_annotation
from app.tasks.celery_app import Signature, chain
from app.tasks.gemma_tasks import run_gemma_gwas
from app.tasks.imputation_tasks import run_imputation
from app.tasks.inspection_tasks import run_inspection
from app.tasks.multilocus_tasks import run_multilocus_gwas
from app.tasks.normalization_tasks import run_vcf_normalization
from app.tasks.postgwas_tasks import run_postgwas_analysis
from app.tasks.qc_tasks import run_qc_pipeline
from app.tasks.structure_tasks import run_structure_analysis
from app.tasks.visualization_tasks import run_visualization

STAGE_SEQUENCE: list[str] = [
    "inspection",
    "normalization",
    "qc",
    "imputation",
    "structure",
    "gwas",
    "multilocus",
    "postgwas",
    "visualization",
    "annotation",
]

STAGE_TO_JOB_STATE: dict[str, tuple[JobStatus, PipelineStep]] = {
    "inspection": (JobStatus.RUNNING_INSPECTION, PipelineStep.INSPECTION),
    "normalization": (JobStatus.RUNNING_NORMALIZATION, PipelineStep.NORMALIZATION),
    "qc": (JobStatus.RUNNING_QC, PipelineStep.QC),
    "imputation": (JobStatus.RUNNING_IMPUTATION, PipelineStep.IMPUTATION),
    "structure": (JobStatus.RUNNING_STRUCTURE, PipelineStep.STRUCTURE),
    "gwas": (JobStatus.RUNNING_GWAS, PipelineStep.GWAS),
    "multilocus": (JobStatus.RUNNING_MULTILOCUS, PipelineStep.MULTILOCUS),
    "postgwas": (JobStatus.RUNNING_POSTGWAS, PipelineStep.POSTGWAS),
    "visualization": (JobStatus.RUNNING_PARSING, PipelineStep.VISUALIZATION),
    "annotation": (JobStatus.RUNNING_ANNOTATION, PipelineStep.ANNOTATION),
}


def _build_stage_signatures(
    *,
    job_id: str,
    dataset_id: str,
    phenotype_params: dict[str, Any] | None = None,
    qc_params: dict[str, Any] | None = None,
    imputation_params: dict[str, Any] | None = None,
    structure_params: dict[str, Any] | None = None,
    gwas_params: dict[str, Any] | None = None,
    threshold_params: dict[str, Any] | None = None,
    clump_params: dict[str, Any] | None = None,
    multilocus_params: dict[str, Any] | None = None,
    annotation_params: dict[str, Any] | None = None,
) -> list[Signature]:
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    imputation_config = dict(imputation_params or {})
    imputation_config.setdefault("output_dir", str(job_root / "imputation"))
    ref_vcf = imputation_config.pop("reference_vcf_path", "") or ""

    gwas_config = dict(gwas_params or {})
    gwas_config.setdefault("output_dir", str(job_root / "gwas"))
    pheno_path = gwas_config.pop("phenotype_path", "") or ""
    gwas_model = gwas_config.pop("model", "lmm")

    return [
        # 1. Inspeksi Fenotipe & Sampel
        run_inspection.s(
            job_id=job_id,
            dataset_id=dataset_id,
            phenotype_cfg=phenotype_params or {},
        ),
        # 2. Normalisasi VCF
        run_vcf_normalization.s(
            job_id=job_id,
            dataset_id=dataset_id,
        ),
        # 3. Quality Control (PLINK 2 / 1.9)
        run_qc_pipeline.s(
            job_id=job_id,
            dataset_id=dataset_id,
            qc_params=qc_params or {},
        ),
        # 4. Imputasi Genotipe (Beagle 5.4 / Skip)
        run_imputation.s(
            job_id=job_id,
            reference_vcf_path=ref_vcf,
            params=imputation_config,
        ),
        # 5. Struktur Populasi, Kinship & LD Decay
        run_structure_analysis.s(
            job_id=job_id,
            dataset_id=dataset_id,
            structure_params=structure_params or {},
        ),
        # 6. Analisis Asosiasi GWAS (GEMMA / GLM)
        run_gemma_gwas.s(
            job_id=job_id,
            phenotype_path=pheno_path,
            model=gwas_model,
            params=gwas_config,
        ),
        # 7. Multi-Locus GWAS (FarmCPU / BLINK via R)
        run_multilocus_gwas.s(
            job_id=job_id,
            dataset_id=dataset_id,
            params=multilocus_params or {},
        ),
        # 8. Pasca-GWAS, Lambda GC, Dynamic Thresholds & Clumping
        run_postgwas_analysis.s(
            job_id=job_id,
            dataset_id=dataset_id,
            threshold_params=threshold_params or {},
            clump_params=clump_params or {},
        ),
        # 9. Visualisasi & Plot Cache (Parquet)
        run_visualization.s(
            job_id=job_id,
            dataset_id=dataset_id,
        ),
        # 10. Anotasi Gen Kandidat GFF3 & GO Enrichment List
        run_annotation.s(
            job_id=job_id,
            dataset_id=dataset_id,
            annotation_params=annotation_params or {},
        ),
    ]


def discover_resume_state(
    *,
    job_id: str,
    dataset_id: str,
    job_parameters: dict[str, Any] | None = None,
    failed_step: str | None = None,
) -> tuple[int, str, dict[str, Any]]:
    """
    Periksa artefak tiap tahap di direktori kerja job untuk menentukan tahap
    mana yang sudah selesai dengan utuh dan tahap mana yang harus dilanjutkan (resume).
    Mengembalikan (start_index, start_stage_name, context_dict).
    """
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    params = job_parameters or {}
    ctx: dict[str, Any] = {
        "job_id": str(job_id),
        "dataset_id": str(dataset_id),
    }

    stage_checks: list[tuple[str, bool, dict[str, Any]]] = [
        (
            "inspection",
            (job_root / "inspection" / "pheno_plink.txt").is_file()
            and (job_root / "inspection" / "keep_ids.txt").is_file(),
            {
                "pheno_path": str((job_root / "inspection" / "pheno_plink.txt").resolve()),
                "keep_samples_path": str((job_root / "inspection" / "keep_ids.txt").resolve()),
                "inspection_report_path": str((job_root / "inspection" / "inspection_report.json").resolve()),
            },
        ),
        (
            "normalization",
            (job_root / "normalization" / "normalized.vcf.gz").is_file(),
            {
                "normalized_vcf_path": str((job_root / "normalization" / "normalized.vcf.gz").resolve()),
                "log_file": str((job_root / "normalization" / "bcftools.log").resolve()),
            },
        ),
        (
            "qc",
            (job_root / "qc" / "plink_qc.bed").is_file()
            and (job_root / "qc" / "plink_qc.vcf.gz").is_file(),
            {
                "qc_vcf_path": str((job_root / "qc" / "plink_qc.vcf.gz").resolve()),
                "genotype_vcf_path": str((job_root / "qc" / "plink_qc.vcf.gz").resolve()),
                "output_vcf": str((job_root / "qc" / "plink_qc.vcf.gz").resolve()),
                "qc_bfile_prefix": str((job_root / "qc" / "plink_qc").resolve()),
                "genotype_bfile_prefix": str((job_root / "qc" / "plink_qc").resolve()),
                "output_prefix": str((job_root / "qc" / "plink_qc").resolve()),
            },
        ),
        (
            "imputation",
            (job_root / "imputation" / "imputed.vcf.gz").is_file()
            or (job_root / "imputation" / "skipped.json").is_file(),
            {
                "output_vcf": str(
                    (
                        (job_root / "imputation" / "imputed.vcf.gz")
                        if (job_root / "imputation" / "imputed.vcf.gz").is_file()
                        else (job_root / "qc" / "plink_qc.vcf.gz")
                    ).resolve()
                ),
                "genotype_vcf_path": str(
                    (
                        (job_root / "imputation" / "imputed.vcf.gz")
                        if (job_root / "imputation" / "imputed.vcf.gz").is_file()
                        else (job_root / "qc" / "plink_qc.vcf.gz")
                    ).resolve()
                ),
                "imputation_skipped": not (job_root / "imputation" / "imputed.vcf.gz").is_file(),
            },
        ),
        (
            "structure",
            (job_root / "structure" / "kinship.cXX.txt").is_file()
            and (job_root / "structure" / "model_recommendation.json").is_file(),
            {
                "eigenvec_path": str((job_root / "structure" / "pca_results.eigenvec").resolve()),
                "eigenval_path": str((job_root / "structure" / "pca_results.eigenval").resolve()),
                "kinship_cxx_path": str((job_root / "structure" / "kinship.cXX.txt").resolve()),
                "gemma_covariates_path": str((job_root / "structure" / "covariates.txt").resolve()),
                "plink_covariates_path": str((job_root / "structure" / "covariates_plink.txt").resolve()),
                "recommended_model": params.get("recommended_model", "lmm"),
                "ld_decay_kb": params.get("chosen_ld_decay_kb", 100),
            },
        ),
        (
            "gwas",
            (job_root / "gwas" / "gwas_results.normalized.tsv").is_file()
            and (job_root / "gwas" / "output" / "association.assoc.txt").is_file(),
            {
                "assoc_output_path": str((job_root / "gwas" / "output" / "association.assoc.txt").resolve()),
                "normalized_assoc_path": str((job_root / "gwas" / "gwas_results.normalized.tsv").resolve()),
            },
        ),
        (
            "multilocus",
            (job_root / "multilocus" / "multilocus_summary.json").is_file()
            or (job_root / "multilocus" / "multilocus.log").is_file(),
            {
                "multilocus_summary_path": str((job_root / "multilocus" / "multilocus_summary.json").resolve()),
            },
        ),
        (
            "postgwas",
            (job_root / "postgwas" / "thresholds.json").is_file()
            and (job_root / "postgwas" / "lead_snps.json").is_file(),
            {
                "thresholds_json_path": str((job_root / "postgwas" / "thresholds.json").resolve()),
                "lead_snps_json_path": str((job_root / "postgwas" / "lead_snps.json").resolve()),
                "lead_snps_path": str((job_root / "postgwas" / "lead_snps.tsv").resolve()),
                "thresholds": params.get("thresholds") or {},
            },
        ),
        (
            "visualization",
            (job_root / "visualization" / "manhattan_cache.parquet").is_file(),
            {
                "manhattan_cache_path": str((job_root / "visualization" / "manhattan_cache.parquet").resolve()),
            },
        ),
        (
            "annotation",
            (job_root / "annotation" / "candidate_genes.json").is_file(),
            {
                "candidate_genes_json_path": str((job_root / "annotation" / "candidate_genes.json").resolve()),
            },
        ),
    ]

    normalized_failed = str(failed_step or "").strip().lower()
    start_idx = 0
    for idx, (stage_name, is_done, stage_ctx) in enumerate(stage_checks):
        if normalized_failed and stage_name == normalized_failed:
            start_idx = idx
            break
        if not is_done:
            start_idx = idx
            break
        ctx.update(stage_ctx)
        start_idx = idx + 1

    if start_idx >= len(STAGE_SEQUENCE):
        start_idx = 0

    return start_idx, STAGE_SEQUENCE[start_idx], ctx


def build_full_pipeline(
    *,
    job_id: str,
    dataset_id: str,
    raw_vcf_path: str,
    phenotype_params: dict[str, Any] | None = None,
    qc_params: dict[str, Any] | None = None,
    imputation_params: dict[str, Any] | None = None,
    structure_params: dict[str, Any] | None = None,
    gwas_params: dict[str, Any] | None = None,
    threshold_params: dict[str, Any] | None = None,
    clump_params: dict[str, Any] | None = None,
    multilocus_params: dict[str, Any] | None = None,
    annotation_params: dict[str, Any] | None = None,
) -> Signature:
    """Build the complete 10-step Celery chain without executing it."""
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    imputation_config = dict(imputation_params or {})
    imputation_config.setdefault("output_dir", str(job_root / "imputation"))
    ref_vcf = imputation_config.pop("reference_vcf_path", "")

    gwas_config = dict(gwas_params or {})
    gwas_config.setdefault("output_dir", str(job_root / "gwas"))
    pheno_path = gwas_config.pop("phenotype_path", "") or ""
    gwas_model = gwas_config.pop("model", "lmm")

    return chain(
        *_build_stage_signatures(
            job_id=job_id,
            dataset_id=dataset_id,
            phenotype_params=phenotype_params,
            qc_params=qc_params,
            imputation_params=imputation_params,
            structure_params=structure_params,
            gwas_params=gwas_params,
            threshold_params=threshold_params,
            clump_params=clump_params,
            multilocus_params=multilocus_params,
            annotation_params=annotation_params,
        )
    )


def trigger_retry_pipeline(
    *,
    job_id: str,
    dataset_id: str,
    raw_vcf_path: str,
    job_parameters: dict[str, Any] | None = None,
    failed_step: str | None = None,
    from_scratch: bool = False,
) -> tuple[str, int, str]:
    """
    Menjalankan ulang (retry) atau melanjutkan (resume) AnalysisJob:
    - Jika from_scratch=True, hapus subdirektori tahap lama dan jalankan dari Tahap 1 (inspection).
    - Jika from_scratch=False (default), periksa artefak tahap yang sudah selesai dan
      lanjutkan langsung dari tahap yang gagal/terhenti.
    Mengembalikan (celery_task_id, start_stage_index, start_stage_name).
    """
    params = dict(job_parameters or {})
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)

    gwas_cfg = dict(params.get("gwas") or {})
    multilocus_cfg = dict(params.get("multilocus") or {})
    if "enabled" not in multilocus_cfg and "enable_multilocus" in gwas_cfg:
        multilocus_cfg = {
            "enabled": bool(gwas_cfg.get("enable_multilocus", False)),
            "methods": gwas_cfg.get("multilocus_methods", ["FarmCPU", "BLINK"]),
        }

    all_sigs = _build_stage_signatures(
        job_id=job_id,
        dataset_id=dataset_id,
        phenotype_params=params.get("phenotype"),
        qc_params=params.get("qc"),
        imputation_params=params.get("imputation"),
        structure_params=params.get("structure"),
        gwas_params=gwas_cfg,
        threshold_params=params.get("threshold"),
        clump_params=params.get("clump"),
        multilocus_params=multilocus_cfg,
        annotation_params=params.get("annotation"),
    )

    if from_scratch:
        start_idx = 0
        start_stage = STAGE_SEQUENCE[0]
        initial_ctx: dict[str, Any] = {}
        if job_root.is_dir():
            for stage_name in STAGE_SEQUENCE:
                shutil.rmtree(job_root / stage_name, ignore_errors=True)
    else:
        start_idx, start_stage, initial_ctx = discover_resume_state(
            job_id=job_id,
            dataset_id=dataset_id,
            job_parameters=params,
            failed_step=failed_step,
        )
        # Bersihkan direktori tahap yang akan dijalankan ulang dan tahap setelahnya
        for stage_name in STAGE_SEQUENCE[start_idx:]:
            shutil.rmtree(job_root / stage_name, ignore_errors=True)

    remaining_sigs = list(all_sigs[start_idx:])
    if start_idx > 0 and remaining_sigs:
        first_sig = remaining_sigs[0]
        first_sig.args = (initial_ctx, *first_sig.args)

    task_id = chain(*remaining_sigs).apply_async().id
    return task_id, start_idx, start_stage


def trigger_full_pipeline(
    *,
    job_id: str,
    dataset_id: str,
    raw_vcf_path: str,
    phenotype_params: dict[str, Any] | None = None,
    qc_params: dict[str, Any] | None = None,
    imputation_params: dict[str, Any] | None = None,
    structure_params: dict[str, Any] | None = None,
    gwas_params: dict[str, Any] | None = None,
    threshold_params: dict[str, Any] | None = None,
    clump_params: dict[str, Any] | None = None,
    multilocus_params: dict[str, Any] | None = None,
    annotation_params: dict[str, Any] | None = None,
) -> str:
    """Enqueue the full 10-step chain and return its root Celery task id."""
    pipeline = build_full_pipeline(
        job_id=job_id,
        dataset_id=dataset_id,
        raw_vcf_path=raw_vcf_path,
        phenotype_params=phenotype_params,
        qc_params=qc_params,
        imputation_params=imputation_params,
        structure_params=structure_params,
        gwas_params=gwas_params,
        threshold_params=threshold_params,
        clump_params=clump_params,
        multilocus_params=multilocus_params,
        annotation_params=annotation_params,
    )
    return pipeline.apply_async().id


# Compatibility aliases
build_pipeline_chain = build_full_pipeline
start_pipeline = trigger_full_pipeline
