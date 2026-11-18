import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.api.routes import pipeline as pipeline_routes
from app.db.engine.models import Dataset, ReferencePanel, UploadStatus, User, UserRole
from app.schemas.pipeline import FullPipelineRequest, ImputationPipelineConfig


class FakeSession:
    def __init__(self, dataset: Dataset, panels: list[ReferencePanel] | None = None):
        self.dataset = dataset
        self.panels = {str(p.id): p for p in (panels or [])}
        self.added = []

    def get(self, model, object_id):
        if model is Dataset and str(object_id) == str(self.dataset.id):
            return self.dataset
        if model is ReferencePanel:
            return self.panels.get(str(object_id))
        return None

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()


def test_gwas_config_ignores_client_phenotype_path():
    req = FullPipelineRequest.model_validate({"gwas": {"phenotype_path": "/etc/passwd"}})
    assert "phenotype_path" not in req.model_dump()["gwas"]


def test_imputation_config_requires_reference_panel_id_not_raw_paths():
    assert "reference_vcf_path" not in ImputationPipelineConfig.model_fields
    assert "map_path" not in ImputationPipelineConfig.model_fields
    assert "reference_panel_id" in ImputationPipelineConfig.model_fields

    with pytest.raises(ValidationError):
        ImputationPipelineConfig(mode="reference")

    panel_id = uuid.uuid4()
    cfg = ImputationPipelineConfig(reference_panel_id=panel_id)
    assert cfg.mode == "reference"
    assert cfg.reference_panel_id == panel_id


def test_start_pipeline_rejects_dataset_without_phenotype_even_with_payload_path(tmp_path, monkeypatch):
    other_pheno = tmp_path / "other_user_pheno.txt"
    other_pheno.write_text("FID\tIID\tPHENO1\nS1\tS1\t1.0\n", encoding="utf-8")
    vcf_file = tmp_path / "cohort.vcf.gz"
    vcf_file.write_bytes(b"vcf")

    owner = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(vcf_file),
        phenotype_file_path=None,
        file_size_bytes=3,
        upload_status=UploadStatus.UPLOADED,
    )
    db = FakeSession(dataset)

    monkeypatch.setattr(pipeline_routes, "trigger_full_pipeline", lambda **kwargs: "task-1")
    payload = FullPipelineRequest.model_validate({"gwas": {"phenotype_path": str(other_pheno)}})

    with pytest.raises(HTTPException) as exc:
        pipeline_routes.start_pipeline_endpoint(
            dataset_id=dataset.id,
            payload=payload,
            db=db,
            user=owner,
        )
    assert exc.value.status_code == 409


def test_start_pipeline_resolves_reference_panel_server_side_and_rejects_missing(tmp_path, monkeypatch):
    vcf_file = tmp_path / "cohort.vcf.gz"
    vcf_file.write_bytes(b"vcf")
    pheno_file = tmp_path / "pheno.txt"
    pheno_file.write_text("FID\tIID\tPHENO1\nS1\tS1\t1.0\n", encoding="utf-8")

    owner = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path=str(vcf_file),
        phenotype_file_path=str(pheno_file),
        file_size_bytes=3,
        upload_status=UploadStatus.UPLOADED,
    )

    inactive_panel = ReferencePanel(
        id=uuid.uuid4(),
        name="Inactive",
        population="ALL",
        genome_build="GRCh38",
        vcf_paths={"ALL": "/data/ref/inactive.vcf.gz"},
        genetic_map_paths={},
        is_active=False,
    )
    active_panel = ReferencePanel(
        id=uuid.uuid4(),
        name="Active",
        population="ALL",
        genome_build="GRCh38",
        vcf_paths={"ALL": "/data/ref/active.vcf.gz"},
        genetic_map_paths={"ALL": "/data/ref/active.map"},
        is_active=True,
    )
    db = FakeSession(dataset, panels=[inactive_panel, active_panel])

    captured = {}

    def fake_trigger(**kwargs):
        captured.update(kwargs)
        return "task-ok"

    monkeypatch.setattr(pipeline_routes, "trigger_full_pipeline", fake_trigger)

    missing_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(uuid.uuid4())}}
    )
    with pytest.raises(HTTPException) as exc:
        pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=missing_payload, db=db, user=owner)
    assert exc.value.status_code == 422

    inactive_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(inactive_panel.id)}}
    )
    with pytest.raises(HTTPException) as exc2:
        pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=inactive_payload, db=db, user=owner)
    assert exc2.value.status_code == 422

    valid_payload = FullPipelineRequest.model_validate(
        {"imputation": {"mode": "reference", "reference_panel_id": str(active_panel.id)}}
    )
    resp = pipeline_routes.start_pipeline_endpoint(dataset_id=dataset.id, payload=valid_payload, db=db, user=owner)
    assert resp.celery_task_id == "task-ok"
    assert captured["imputation_params"]["reference_vcf_path"] == "/data/ref/active.vcf.gz"
    assert captured["imputation_params"]["map_path"] == "/data/ref/active.map"
    assert captured["imputation_params"]["reference_panel_id"] == str(active_panel.id)
