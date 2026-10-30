"""Request and response schemas for the full GWAS pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class PopulationType(str, Enum):
    INBRED = "inbred"      # inbred / selfing / klonal / vegetatif -> TANPA HWE
    OUTCROSS = "outcross"  # kawin silang -> dengan HWE


class PhenotypeConfig(BaseModel):
    trait: str = "PHENO1"                                       # nama kolom trait di file fenotipe (default PHENO1; otomatis dialihkan ke kolom numerik pertama jika tidak ditemukan)
    id_column: str = "IID"
    fid_column: str | None = "FID"
    missing_tokens: list[str] = ["", ".", "NA", "-9"]


class QCPipelineConfig(BaseModel):
    population_type: PopulationType = PopulationType.INBRED
    maf: float = Field(default=0.05, ge=0, le=0.5)
    geno: float = Field(default=0.05, ge=0, le=1)
    mind: float = Field(default=0.05, ge=0, le=1)
    hwe: float | None = Field(default=None, ge=0, le=1)        # None bila inbred
    snps_only: bool = True                                      # --snps-only just-acgt
    max_alleles: int = Field(default=2, ge=2, le=2)             # --max-alleles 2
    set_missing_var_ids: bool = True                            # --set-all-var-ids @:#
    memory_mb: int | None = Field(default=None, ge=512)         # --memory
    max_snp_loss_warning: float = 0.5                           # peringatan bila >50% SNP hilang

    @model_validator(mode="before")
    @classmethod
    def infer_pop_type(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "population_type" not in data and data.get("hwe") is not None:
                data["population_type"] = PopulationType.OUTCROSS.value
        return data

    @model_validator(mode="after")
    def validate_population_and_hwe(self) -> "QCPipelineConfig":
        if self.population_type == PopulationType.INBRED:
            self.hwe = None
        elif self.population_type == PopulationType.OUTCROSS and self.hwe is None:
            self.hwe = 1e-6
        return self


class ImputationPipelineConfig(BaseModel):
    mode: Literal["none", "reference", "reference_free"] = "none"
    reference_vcf_path: str | None = None
    nthreads: int = Field(default=2, ge=1, le=64)
    beagle_max_memory: str | None = Field(default=None, pattern=r"^-Xmx\d+[mMgG]$")
    chromosome: str | None = None
    map_path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_mode(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "mode" not in data:
                if data.get("reference_vcf_path"):
                    data["mode"] = "reference"
                else:
                    data["mode"] = "none"
        return data

    @model_validator(mode="after")
    def validate_reference(self) -> "ImputationPipelineConfig":
        if self.mode == "reference" and not self.reference_vcf_path:
            raise ValueError("reference_vcf_path wajib diisi jika mode imputasi adalah 'reference'")
        return self


class StructureConfig(BaseModel):
    prune_window: int = 50
    prune_step: int = 5
    prune_r2: float = Field(default=0.2, gt=0, lt=1)
    n_pcs: int = Field(default=10, ge=2, le=20)
    ld_decay_window_kb: int = Field(default=1000, ge=10, le=5000)
    ld_decay_bin_kb: int = Field(default=10, ge=1, le=100)
    ld_decay_r2_cutoff: float = Field(default=0.2, gt=0, lt=1)
    ld_decay_max_snps: int = Field(default=20000, ge=500)
    ld_decay_fallback_kb: int = 100


class GWASPipelineConfig(BaseModel):
    phenotype_path: str | None = Field(default=None)
    model: Literal["auto", "glm", "lmm", "lm"] = "auto"
    n_pc_covariates: int = Field(default=3, ge=0, le=20)
    enable_multilocus: bool = False
    multilocus_methods: list[Literal["FarmCPU", "BLINK"]] = ["FarmCPU", "BLINK"]


class ThresholdConfig(BaseModel):
    method: Literal["bonferroni", "fdr", "lod"] = "bonferroni"
    alpha: float = Field(default=0.05, gt=0, lt=1)
    lod_score: float = Field(default=5.6, gt=0)
    suggestive_p: float = 1e-5


class ClumpConfig(BaseModel):
    p2: float = 1e-4
    r2: float = 0.2
    kb: int | None = None                                       # None -> pakai ld_decay_kb


class AnnotationConfig(BaseModel):
    enabled: bool = True
    feature_type: str = "gene"
    window_kb: int | None = None                                # None -> pakai ld_decay_kb


class FullPipelineRequest(BaseModel):
    phenotype: PhenotypeConfig = Field(default_factory=PhenotypeConfig)
    qc: QCPipelineConfig = Field(default_factory=QCPipelineConfig)
    imputation: ImputationPipelineConfig = Field(default_factory=ImputationPipelineConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    gwas: GWASPipelineConfig = Field(default_factory=GWASPipelineConfig)
    threshold: ThresholdConfig = Field(default_factory=ThresholdConfig)
    clump: ClumpConfig = Field(default_factory=ClumpConfig)
    annotation: AnnotationConfig = Field(default_factory=AnnotationConfig)


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

