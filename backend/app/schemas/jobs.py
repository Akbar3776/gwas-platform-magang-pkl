"""Schema Pydantic untuk endpoint AnalysisJob (QC, dan tahap pipeline lain)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class QCJobRequest(BaseModel):
    """
    Payload untuk men-trigger tahap QC. Nilai default sudah merupakan
    threshold yang wajar secara ilmiah, sehingga peneliti pemula tidak
    wajib mengubah apa pun jika tidak familiar dengan parameter PLINK.
    """

    dataset_id: uuid.UUID
    maf: float = Field(default=0.01, ge=0, le=1, description="Minor Allele Frequency threshold")
    geno: float = Field(default=0.05, ge=0, le=1, description="Variant missing-rate threshold")
    mind: float = Field(default=0.1, ge=0, le=1, description="Sample missing-rate threshold")
    hwe: float = Field(default=1e-6, ge=0, le=1, description="Hardy-Weinberg equilibrium p-value threshold")


class JobTriggerResponse(BaseModel):
    job_id: uuid.UUID
    overall_status: str
    celery_task_id: str


class StepLogEntry(BaseModel):
    step: str
    label: str
    tool: str
    status: str
    line_count: int = 0
    log_text: str = ""


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    dataset_id: uuid.UUID | None = None
    dataset_filename: str | None = None
    phenotype_filename: str | None = None
    trait: str | None = None
    model: str | None = None
    pipeline_type: str
    current_step: str
    overall_status: str
    error_message: str | None = None
    error_log: str | None = None
    last_log_line: str | None = None
    elapsed_seconds: float | None = None
    step_logs: list[StepLogEntry] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
