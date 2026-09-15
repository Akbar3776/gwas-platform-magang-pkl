"""Request and response schemas for the full GWAS pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QCPipelineConfig(BaseModel):
    maf: float = Field(default=0.01, ge=0, le=1)
    geno: float = Field(default=0.05, ge=0, le=1)
    mind: float = Field(default=0.1, ge=0, le=1)
    hwe: float = Field(default=1e-6, ge=0, le=1)


class ImputationPipelineConfig(BaseModel):
    reference_vcf_path: str = Field(..., min_length=1)
    nthreads: int = Field(default=2, ge=1, le=64)
    beagle_max_memory: str | None = Field(default=None, pattern=r"^-Xmx\d+[mMgG]$")
    chromosome: str | None = None
    map_path: str | None = None


class GWASPipelineConfig(BaseModel):
    phenotype_path: str = Field(..., min_length=1)
    model: Literal["lmm", "lm"] = "lmm"


class FullPipelineRequest(BaseModel):
    qc: QCPipelineConfig = Field(default_factory=QCPipelineConfig)
    imputation: ImputationPipelineConfig
    gwas: GWASPipelineConfig


class PipelineStartResponse(BaseModel):
    job_id: str
    celery_task_id: str
    status: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "b3f1e2b0-4c1a-4e9e-8b0a-2f6a1c9d0e11",
                "celery_task_id": "a7e2c9b4-1234-4d5e-9f6a-abcdef123456",
                "status": "pending",
            }
        }
    }
