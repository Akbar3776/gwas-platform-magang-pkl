"""Authenticated, resumable chunked upload endpoints for large VCF files."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import Dataset, UploadStatus, User
from app.schemas.upload import (
    ChunkUploadResponse,
    FinalizeUploadResponse,
    InitUploadRequest,
    InitUploadResponse,
    UploadStatusResponse,
)
from app.services import upload_service
from app.tasks.upload_tasks import assemble_dataset_task

router = APIRouter(prefix="/upload", tags=["upload"])


def _owned_dataset(file_id: uuid.UUID, user: User, db: Session) -> Dataset:
    dataset = db.get(Dataset, file_id)
    if dataset is None or (dataset.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload tidak ditemukan")
    return dataset


@router.post("/init", response_model=InitUploadResponse, status_code=status.HTTP_201_CREATED)
def init_upload(
    payload: InitUploadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InitUploadResponse:
    dataset = Dataset(
        owner_id=user.id,
        original_filename=payload.original_filename,
        storage_path="",
        file_size_bytes=payload.total_size_bytes,
        genome_build=payload.genome_build,
        upload_status=UploadStatus.UPLOADING,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    upload_service.get_staging_dir(str(dataset.id))
    return InitUploadResponse(file_id=dataset.id, upload_status=dataset.upload_status.value)


@router.post("/chunk", response_model=ChunkUploadResponse)
def upload_chunk(
    file_id: uuid.UUID = Form(...),
    chunk_index: int = Form(..., ge=0),
    total_chunks: int = Form(..., gt=0),
    chunk: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChunkUploadResponse:
    dataset = _owned_dataset(file_id, user, db)
    if dataset.upload_status != UploadStatus.UPLOADING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload tidak sedang menerima chunk")
    if chunk_index >= total_chunks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chunk_index di luar rentang")

    try:
        upload_service.save_chunk(str(file_id), chunk_index, chunk)
    except upload_service.InvalidFileIdError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_id tidak valid") from exc

    received_indices = upload_service.get_received_chunk_indices(str(file_id))
    return ChunkUploadResponse(
        file_id=file_id,
        chunk_index=chunk_index,
        chunks_received=len(received_indices),
        total_chunks=total_chunks,
        is_complete=len(received_indices) == total_chunks,
    )


@router.post("/finalize", response_model=FinalizeUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def finalize_upload(
    file_id: uuid.UUID = Form(...),
    total_chunks: int = Form(..., gt=0),
    expected_sha256: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinalizeUploadResponse:
    dataset = _owned_dataset(file_id, user, db)
    if dataset.upload_status != UploadStatus.UPLOADING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload sudah difinalisasi atau gagal")

    received_indices = upload_service.get_received_chunk_indices(str(file_id))
    if len(received_indices) != total_chunks or received_indices != list(range(total_chunks)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload belum lengkap; kirim ulang chunk yang hilang sebelum finalize",
        )

    dataset.upload_status = UploadStatus.VALIDATING
    db.commit()
    task = assemble_dataset_task.delay(str(file_id), total_chunks, expected_sha256)
    return FinalizeUploadResponse(
        file_id=file_id,
        upload_status=dataset.upload_status.value,
        assembly_task_id=task.id,
    )


@router.get("/{file_id}/status", response_model=UploadStatusResponse)
def get_upload_status(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadStatusResponse:
    dataset = _owned_dataset(file_id, user, db)
    received_indices = []
    if dataset.upload_status == UploadStatus.UPLOADING:
        received_indices = upload_service.get_received_chunk_indices(str(file_id))
    return UploadStatusResponse(
        file_id=dataset.id,
        upload_status=dataset.upload_status.value,
        received_chunk_indices=received_indices,
        storage_path=dataset.storage_path or None,
        file_size_bytes=dataset.file_size_bytes,
    )
