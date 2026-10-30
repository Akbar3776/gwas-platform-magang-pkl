import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, Result, ResultType, User, UserRole
from app.main import _ensure_sqlite_columns
from app.schemas.pipeline import (
    FullPipelineRequest,
    GWASPipelineConfig,
    ImputationPipelineConfig,
    PhenotypeConfig,
    PopulationType,
    QCPipelineConfig,
)


def test_schema_qc_population_type_hwe_validation():
    # Inbred should automatically strip HWE to None
    qc_inbred = QCPipelineConfig(population_type=PopulationType.INBRED, hwe=1e-5)
    assert qc_inbred.hwe is None

    # Outcross with None HWE should default to 1e-6
    qc_outcross = QCPipelineConfig(population_type=PopulationType.OUTCROSS, hwe=None)
    assert qc_outcross.hwe == 1e-6


def test_schema_imputation_mode_validation():
    # mode='none' does not require reference_vcf_path
    imp_none = ImputationPipelineConfig(mode="none")
    assert imp_none.mode == "none"

    # mode='reference' requires reference_vcf_path
    with pytest.raises(ValidationError):
        ImputationPipelineConfig(mode="reference", reference_vcf_path=None)

    # legacy dict with reference_vcf_path infers mode='reference'
    imp_legacy = ImputationPipelineConfig(reference_vcf_path="/path/to/ref.vcf.gz")
    assert imp_legacy.mode == "reference"


def test_full_pipeline_request_backward_compatible_defaults():
    req = FullPipelineRequest()
    assert req.phenotype.trait == "PHENO1"
    assert req.qc.maf == 0.05
    assert req.qc.population_type == PopulationType.INBRED
    assert req.imputation.mode == "none"
    assert req.structure.n_pcs == 10
    assert req.gwas.model == "auto"
    assert req.threshold.method == "bonferroni"


def test_database_sqlite_models_and_column_migration():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    user = User(
        id=uuid.uuid4(),
        email="researcher@test.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="sample.vcf.gz",
        storage_path="/data/storage/sample.vcf.gz",
        phenotype_file_path="/data/storage/pheno.txt",
        annotation_file_path="/data/storage/sample.gff3",
        extra_metadata={"organism": "Arabidopsis"},
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        current_step=PipelineStep.INSPECTION,
        overall_status=JobStatus.RUNNING_INSPECTION,
        parameters={"test": 123},
    )
    res = Result(
        id=uuid.uuid4(),
        job_id=job.id,
        result_type=ResultType.INSPECTION_REPORT,
        file_path="/path/to/report.json",
    )

    session.add_all([user, dataset, job, res])
    session.commit()

    saved_ds = session.get(Dataset, dataset.id)
    assert saved_ds.annotation_file_path == "/data/storage/sample.gff3"
    assert saved_ds.extra_metadata["organism"] == "Arabidopsis"

    saved_job = session.get(AnalysisJob, job.id)
    assert saved_job.current_step == PipelineStep.INSPECTION
    assert saved_job.overall_status == JobStatus.RUNNING_INSPECTION

    saved_res = session.get(Result, res.id)
    assert saved_res.result_type == ResultType.INSPECTION_REPORT
    session.close()
