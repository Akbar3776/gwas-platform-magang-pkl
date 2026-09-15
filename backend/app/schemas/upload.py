"""Schema Pydantic untuk endpoint chunked upload."""

import uuid

from pydantic import BaseModel, Field


class InitUploadRequest(BaseModel):
    """Payload untuk memulai sesi upload baru (sebelum chunk pertama dikirim)."""

    original_filename: str = Field(..., examples=["cohort_batch1.vcf.gz"])
    total_size_bytes: int = Field(..., gt=0)
    genome_build: str | None = Field(default=None, examples=["GRCh38"])


class InitUploadResponse(BaseModel):
    """
    `file_id` di sini SAMA DENGAN `dataset.id` — dipakai sebagai identifier
    tunggal oleh frontend untuk seluruh request chunk berikutnya.
    """

    file_id: uuid.UUID
    upload_status: str


class ChunkUploadResponse(BaseModel):
    file_id: uuid.UUID
    chunk_index: int
    chunks_received: int
    total_chunks: int
    is_complete: bool


class FinalizeUploadRequest(BaseModel):
    total_chunks: int = Field(..., gt=0)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[a-fA-F0-9]{64}$")


class FinalizeUploadResponse(BaseModel):
    file_id: uuid.UUID
    upload_status: str
    assembly_task_id: str


class UploadStatusResponse(BaseModel):
    file_id: uuid.UUID
    upload_status: str
    received_chunk_indices: list[int]
    storage_path: str | None = None
    file_size_bytes: int | None = None
