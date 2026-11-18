"""Endpoint for starting the full GWAS pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user, is_admin
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ReferencePanel, UploadStatus, User
from app.schemas.pipeline import FullPipelineRequest, PipelineStartResponse
from app.tasks.pipeline_orchestrator import trigger_full_pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post(
    "/{dataset_id}/start",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mulai pipeline normalisasi -> QC -> imputasi -> GWAS",
)
def start_pipeline_endpoint(
    dataset_id: uuid.UUID,
    payload: FullPipelineRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PipelineStartResponse:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or (dataset.owner_id != user.id and not is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset tidak ditemukan")
    if dataset.upload_status not in {UploadStatus.UPLOADED, UploadStatus.READY}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset belum siap diproses; status saat ini '{dataset.upload_status.value}'",
        )
    if not dataset.storage_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path VCF dataset kosong")

    effective_pheno_path = dataset.phenotype_file_path
    if not effective_pheno_path or not Path(effective_pheno_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File fenotipe wajib untuk analisis GWAS. Silakan unggah file fenotipe pada dataset terlebih dahulu.",
        )

    imputation_params = payload.imputation.model_dump()
    if imputation_params.get("reference_panel_id") is not None:
        imputation_params["reference_panel_id"] = str(imputation_params["reference_panel_id"])
    if payload.imputation.mode == "reference":
        panel = db.get(ReferencePanel, payload.imputation.reference_panel_id)
        if panel is None or not panel.is_active:
            raise HTTPException(status_code=422, detail="Reference panel tidak ditemukan")
        key = payload.imputation.chromosome or "ALL"
        ref_path = (panel.vcf_paths or {}).get(key) or next(iter((panel.vcf_paths or {}).values()), None)
        if not ref_path:
            raise HTTPException(status_code=422, detail="Reference panel tidak ditemukan")
        imputation_params["reference_vcf_path"] = ref_path
        map_path = (panel.genetic_map_paths or {}).get(key)
        if map_path:
            imputation_params["map_path"] = map_path

    job_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    parameters = {
        "phenotype": payload.phenotype.model_dump(),
        "qc": payload.qc.model_dump(),
        "imputation": imputation_params,
        "structure": payload.structure.model_dump(),
        "gwas": payload.gwas.model_dump(),
        "threshold": payload.threshold.model_dump(),
        "clump": payload.clump.model_dump(),
        "annotation": payload.annotation.model_dump(),
        "run_id": run_id,
    }
    job = AnalysisJob(
        id=job_id,
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.INSPECTION,
        overall_status=JobStatus.PENDING,
        parameters=parameters,
        work_dir=str(job_root),
        phenotype_file_path=str(effective_pheno_path),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    is_multilocus_model = str(payload.gwas.model).lower() in {"farmcpu", "blink", "mlmm"}
    methods = list(payload.gwas.multilocus_methods)
    if is_multilocus_model:
        model_name = {"farmcpu": "FarmCPU", "blink": "BLINK", "mlmm": "MLMM"}[str(payload.gwas.model).lower()]
        if model_name not in methods:
            methods.append(model_name)
    multilocus_config = {
        "enabled": payload.gwas.enable_multilocus or is_multilocus_model,
        "methods": methods,
    }

    try:
        celery_task_id = trigger_full_pipeline(
            job_id=str(job.id),
            dataset_id=str(dataset.id),
            phenotype_params=payload.phenotype.model_dump(),
            qc_params=payload.qc.model_dump(),
            imputation_params=imputation_params,
            structure_params=payload.structure.model_dump(),
            gwas_params=payload.gwas.model_dump(),
            threshold_params=payload.threshold.model_dump(),
            clump_params=payload.clump.model_dump(),
            multilocus_params=multilocus_config,
            annotation_params=payload.annotation.model_dump(),
            run_id=run_id,
        )
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job.id)
        if job is not None:
            job.overall_status = JobStatus.FAILED
            job.error_message = f"Gagal enqueue pipeline: {exc}"
            job.error_log = str(exc)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal memulai pipeline analisis",
        ) from exc

    job.celery_task_id = celery_task_id
    db.commit()
    return PipelineStartResponse(
        job_id=str(job.id),
        celery_task_id=celery_task_id,
        status=job.overall_status.value,
    )
