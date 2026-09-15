import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import jobs
from app.db.engine.models import Dataset, UploadStatus, User, UserRole


class FakeSession:
    def __init__(self, dataset):
        self.dataset = dataset
        self.added = []

    def get(self, model, object_id):
        if model is Dataset and object_id == self.dataset.id:
            return self.dataset
        return next((item for item in self.added if isinstance(item, model)), None)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()


def test_trigger_qc_creates_pending_job_and_enqueues_task(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="test",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/data/storage/datasets/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    session = FakeSession(dataset)
    task_call = {}

    class FakeTask:
        id = "celery-task-123"

    def fake_delay(*args):
        task_call["args"] = args
        return FakeTask()

    monkeypatch.setattr(jobs.run_qc_pipeline, "delay", fake_delay)
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/v1")
    app.dependency_overrides[jobs.get_db] = lambda: session
    app.dependency_overrides[jobs.get_current_user] = lambda: user

    response = TestClient(app).post(
        "/api/v1/jobs/qc",
        json={"dataset_id": str(dataset.id), "maf": 0.01, "geno": 0.05, "mind": 0.1, "hwe": 1e-6},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["overall_status"] == "pending"
    assert body["celery_task_id"] == "celery-task-123"
    assert task_call["args"][1] == body["job_id"]
