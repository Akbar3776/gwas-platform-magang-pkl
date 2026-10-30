"""SQLAlchemy models for the GWAS platform."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    DateTime,
    Enum,
    ForeignKey,
    JSON,
    String,
    Text,
    TypeDecorator,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UUID(TypeDecorator):
    """Platform-independent UUID type supporting PostgreSQL UUID and SQLite CHAR(36) with str/UUID coercion."""

    impl = CHAR(36)
    cache_ok = True

    def __init__(self, as_uuid: bool = True):
        super().__init__()
        self.as_uuid = as_uuid

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=self.as_uuid))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not self.as_uuid:
            return str(value)
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class UserRole(str, enum.Enum):
    RESEARCHER = "researcher"
    ADMIN = "admin"


class UploadStatus(str, enum.Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"


class PipelineStep(str, enum.Enum):
    INSPECTION = "inspection"
    NORMALIZATION = "normalization"
    QC = "qc"
    IMPUTATION = "imputation"
    STRUCTURE = "structure"
    GWAS = "gwas"
    MULTILOCUS = "multilocus"
    POSTGWAS = "postgwas"
    VISUALIZATION = "visualization"
    ANNOTATION = "annotation"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    RUNNING_INSPECTION = "running_inspection"
    RUNNING_NORMALIZATION = "running_normalization"
    RUNNING_QC = "running_qc"
    RUNNING_IMPUTATION = "running_imputation"
    RUNNING_STRUCTURE = "running_structure"
    RUNNING_GWAS = "running_gwas"
    RUNNING_MULTILOCUS = "running_multilocus"
    RUNNING_POSTGWAS = "running_postgwas"
    RUNNING_PARSING = "running_parsing"
    RUNNING_ANNOTATION = "running_annotation"
    FAILED = "failed"
    COMPLETED = "completed"
    SUCCESS = "success"
    CANCELLED = "cancelled"


class ResultType(str, enum.Enum):
    PLINK_QC_LOG = "plink_qc_log"
    GEMMA_LOG = "gemma_log"
    GWAS_ASSOC = "gwas_assoc"
    GWAS_NORMALIZED_TSV = "gwas_normalized_tsv"
    IMPUTED_VCF = "imputed_vcf"
    ASSOC_RAW = "assoc_raw"
    MANHATTAN_DATA = "manhattan_data"
    QQ_DATA = "qq_data"
    SUMMARY_REPORT = "summary_report"
    SUMMARY_REPORT_PDF = "summary_report_pdf"
    INSPECTION_REPORT = "inspection_report"
    QC_COMPARISON = "qc_comparison"
    PCA_DATA = "pca_data"
    KINSHIP_DATA = "kinship_data"
    LD_DECAY = "ld_decay"
    MODEL_RECOMMENDATION = "model_recommendation"
    THRESHOLDS = "thresholds"
    LEAD_SNPS = "lead_snps"
    MULTILOCUS_RESULTS = "multilocus_results"
    PEAK_COMPARISON = "peak_comparison"
    CANDIDATE_GENES = "candidate_genes"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.RESEARCHER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    refresh_token_version: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    triggered_jobs: Mapped[list["AnalysisJob"]] = relationship(back_populates="triggered_by_user")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"), default=UploadStatus.UPLOADING, nullable=False
    )
    genome_build: Mapped[str | None] = mapped_column(String(50), nullable=True)
    normalized_vcf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    phenotype_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    annotation_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped["User | None"] = relationship(back_populates="datasets")
    analysis_jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_type: Mapped[str] = mapped_column(String(100), default="full_pipeline", nullable=False)
    current_step: Mapped[PipelineStep] = mapped_column(
        Enum(PipelineStep, name="pipeline_step"), default=PipelineStep.NORMALIZATION, nullable=False
    )
    overall_status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.PENDING, nullable=False
    )
    parameters: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    work_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qc_output_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    imputed_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    assoc_output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    phenotype_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="analysis_jobs")
    triggered_by_user: Mapped["User | None"] = relationship(back_populates="triggered_jobs")
    results: Mapped[list["Result"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Result(Base):
    __tablename__ = "results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False
    )
    result_type: Mapped[ResultType] = mapped_column(
        Enum(ResultType, name="result_type"), nullable=False
    )
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    summary_stats: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["AnalysisJob"] = relationship(back_populates="results")


class ReferencePanel(Base):
    __tablename__ = "reference_panels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    population: Mapped[str] = mapped_column(String(50), nullable=False)
    genome_build: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vcf_paths: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    genetic_map_paths: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def available_chromosomes(self) -> list[str]:
        return sorted((self.vcf_paths or {}).keys())
