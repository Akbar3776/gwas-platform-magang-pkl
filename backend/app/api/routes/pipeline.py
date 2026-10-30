"""Endpoint for starting the full GWAS pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User
from app.schemas.pipeline import FullPipelineRequest, PipelineStartResponse
from app.tasks.pipeline_orchestrator import trigger_full_pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _is_admin(user: User) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_val == "admin"


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
    if dataset is None or (dataset.owner_id != user.id and not _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset tidak ditemukan")
    if dataset.upload_status not in {UploadStatus.UPLOADED, UploadStatus.READY}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset belum siap diproses; status saat ini '{dataset.upload_status.value}'",
        )
    if not dataset.storage_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path VCF dataset kosong")

    effective_pheno_path = dataset.phenotype_file_path or payload.gwas.phenotype_path
    if not effective_pheno_path or not Path(effective_pheno_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File fenotipe wajib untuk analisis GWAS. Silakan unggah file fenotipe pada dataset terlebih dahulu.",
        )

    job_id = uuid.uuid4()
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    parameters = {
        "phenotype": payload.phenotype.model_dump(),
        "qc": payload.qc.model_dump(),
        "imputation": payload.imputation.model_dump(),
        "structure": payload.structure.model_dump(),
        "gwas": payload.gwas.model_dump(),
        "threshold": payload.threshold.model_dump(),
        "clump": payload.clump.model_dump(),
        "annotation": payload.annotation.model_dump(),
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

    try:
        celery_task_id = trigger_full_pipeline(
            job_id=str(job.id),
            dataset_id=str(dataset.id),
            raw_vcf_path=dataset.storage_path,
            phenotype_params=payload.phenotype.model_dump(),
            qc_params=payload.qc.model_dump(),
            imputation_params=payload.imputation.model_dump(),
            structure_params=payload.structure.model_dump(),
            gwas_params=payload.gwas.model_dump(),
            threshold_params=payload.threshold.model_dump(),
            clump_params=payload.clump.model_dump(),
            multilocus_params={
                "enabled": payload.gwas.enable_multilocus,
                "methods": payload.gwas.multilocus_methods,
            },
            annotation_params=payload.annotation.model_dump(),
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
