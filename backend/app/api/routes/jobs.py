"""
Endpoint untuk men-trigger dan memantau AnalysisJob.

`POST /jobs/qc`         -> membuat job baru & men-trigger Celery task PLINK QC
`GET  /jobs/{job_id}/status` -> dipoll oleh React untuk menampilkan progress bar
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User
from app.core.security import get_current_user
from app.schemas.jobs import JobStatusResponse, JobTriggerResponse, QCJobRequest
from app.tasks.qc_tasks import run_qc_pipeline

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/qc", response_model=JobTriggerResponse, status_code=202)
def trigger_qc_job(
    payload: QCJobRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobTriggerResponse:
    """
    Membuat `AnalysisJob` baru berstatus PENDING dan langsung men-trigger
    task Celery `run_plink_qc` secara asinkron.

    Response HTTP 202 (Accepted) dikembalikan SEGERA — endpoint ini tidak
    menunggu PLINK selesai, karena proses QC pada file GB-an bisa memakan
    waktu lama (lihat Development Rule #1).
    """
    dataset = db.get(Dataset, payload.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan.")

    if dataset.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan.")

    if dataset.upload_status != UploadStatus.UPLOADED and dataset.upload_status != UploadStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dataset berstatus '{dataset.upload_status.value}'. "
                "Pastikan upload sudah selesai (status uploaded/ready) sebelum menjalankan QC."
            ),
        )

    job = AnalysisJob(
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="qc_only",
        current_step=PipelineStep.QC,
        overall_status=JobStatus.PENDING,
        parameters={
            "maf": payload.maf,
            "geno": payload.geno,
            "mind": payload.mind,
            "hwe": payload.hwe,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    task = run_qc_pipeline.delay(None, str(job.id), str(dataset.id), job.parameters)

    return JobTriggerResponse(
        job_id=job.id,
        overall_status=job.overall_status.value,
        celery_task_id=task.id,
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobStatusResponse:
    """
    Endpoint polling untuk frontend. React disarankan memanggil ini setiap
    2-3 detik selagi `overall_status` masih PENDING/RUNNING, lalu berhenti
    polling begitu status menjadi COMPLETED/FAILED/CANCELLED.
    """
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if job.triggered_by != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    params = job.parameters or {}

    return JobStatusResponse(
        job_id=job.id,
        pipeline_type=job.pipeline_type,
        current_step=job.current_step.value,
        overall_status=job.overall_status.value,
        error_message=job.error_message,
        error_log=job.error_log,
        last_log_line=params.get("last_log_line"),
        elapsed_seconds=params.get("elapsed_seconds"),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
