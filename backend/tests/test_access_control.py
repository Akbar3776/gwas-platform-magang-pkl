from datetime import datetime, timezone
import io
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import UploadFile

from app.api.routes import export as export_routes
from app.api.routes import jobs as jobs_routes
from app.api.routes import pipeline as pipeline_routes
from app.api.routes import report as report_routes
from app.api.routes import results
from app.api.routes import upload as upload_routes
from app.core.security import get_current_user, require_job_owner
from app.db.engine.models import AnalysisJob, Dataset, UploadStatus, User, UserRole
from app.schemas.pipeline import FullPipelineRequest
from app.tasks import pipeline_orchestrator
from tests import conftest


class FakeSession:
    def __init__(self, jobs=None, users=None, datasets=None):
        self.jobs = {str(j.id): j for j in (jobs or [])}
        self.users = {str(u.id): u for u in (users or [])}
        self.datasets = {str(d.id): d for d in (datasets or [])}

    def get(self, model, object_id):
        if model is AnalysisJob:
            return self.jobs.get(str(object_id))
        if model is User:
            return self.users.get(str(object_id))
        if model is Dataset:
            return self.datasets.get(str(object_id))
        return None

    def query(self, model):
        jobs_list = list(self.jobs.values())

        class _Query:
            def filter(self, *args, **kwargs):
                return self

            def first(self):
                return jobs_list[0] if (model is AnalysisJob and jobs_list) else None

            def all(self):
                return list(jobs_list) if model is AnalysisJob else []

        return _Query()


def _make_user(role=UserRole.RESEARCHER) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        role=role,
        is_active=True,
    )


def _make_job(owner_id: uuid.UUID, deleted_at=None) -> AnalysisJob:
    return AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        triggered_by=owner_id,
        pipeline_type="full_pipeline",
        deleted_at=deleted_at,
    )


def test_every_results_route_requires_job_owner():
    routes = [r for r in results.router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 14
    for r in routes:
        assert any(d.dependency is require_job_owner for d in r.dependencies), r.path


def test_require_job_owner_blocks_non_owner():
    user_a = _make_user()
    user_b = _make_user()
    job_a = _make_job(user_a.id)
    db = FakeSession(jobs=[job_a])

    with pytest.raises(HTTPException) as exc:
        require_job_owner(job_id=job_a.id, user=user_b, db=db)
    assert exc.value.status_code == 404


def test_require_job_owner_allows_owner():
    user_a = _make_user()
    job_a = _make_job(user_a.id)
    db = FakeSession(jobs=[job_a])

    assert require_job_owner(job_id=job_a.id, user=user_a, db=db) is job_a


def test_require_job_owner_rejects_soft_deleted_job():
    user_a = _make_user()
    job_a = _make_job(user_a.id, deleted_at=datetime.now(timezone.utc))
    db = FakeSession(jobs=[job_a])

    with pytest.raises(HTTPException) as exc:
        require_job_owner(job_id=job_a.id, user=user_a, db=db)
    assert exc.value.status_code == 404


def test_require_job_owner_allows_admin_on_other_users_job():
    user_a = _make_user()
    admin = _make_user(role=UserRole.ADMIN)
    job_a = _make_job(user_a.id)
    db = FakeSession(jobs=[job_a])

    assert require_job_owner(job_id=job_a.id, user=admin, db=db) is job_a


def test_get_current_user_without_token_returns_401():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        get_current_user(token=None, db=db)
    assert exc.value.status_code == 401


@pytest.mark.skipif(not conftest._LOOPBACK_OK, reason="loopback diblokir")
def test_results_endpoint_unauthenticated_returns_401():
    test_app = FastAPI()
    test_app.include_router(results.router, prefix="/api/v1")
    resp = TestClient(test_app).get(f"/api/v1/results/{uuid.uuid4()}/lead-snps")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "route_name",
    [
        "get_job_status",
        "get_job_logs",
        "list_job_files",
        "get_job_file_content",
        "get_job_results_summary",
        "cancel_job",
        "retry_job",
        "delete_job",
        "get_upload_status",
        "download_uploaded_dataset",
        "delete_uploaded_dataset",
        "upload_phenotype_file",
        "delete_phenotype_file",
        "upload_annotation_file",
        "delete_annotation_file",
        "inspect_dataset_and_phenotype",
        "download_inspection_file",
        "upload_chunk",
        "finalize_upload",
        "start_pipeline_endpoint",
        "export_job_results",
        "download_summary_report",
    ],
)
def test_cross_user_isolation_blocks_other_user_across_all_routes(route_name, monkeypatch):
    user_a = _make_user()
    user_b = _make_user()
    dataset_a = Dataset(
        id=uuid.uuid4(),
        owner_id=user_a.id,
        original_filename="cohort.vcf.gz",
        storage_path="/tmp/cohort.vcf.gz",
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    job_a = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset_a.id,
        triggered_by=user_a.id,
        pipeline_type="full_pipeline",
    )
    db = FakeSession(jobs=[job_a], users=[user_a, user_b], datasets=[dataset_a])

    monkeypatch.setattr(jobs_routes.run_qc_pipeline, "delay", lambda *a, **kw: None)
    monkeypatch.setattr(upload_routes.assemble_dataset_task, "delay", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline_routes, "trigger_full_pipeline", lambda *a, **kw: "noop")
    monkeypatch.setattr(pipeline_orchestrator, "trigger_retry_pipeline", lambda *a, **kw: ("noop", 0, "inspection"))

    dummy_file = UploadFile(file=io.BytesIO(b"x"), filename="dummy.txt")

    invokers = {
        "get_job_status": lambda: jobs_routes.get_job_status(job_id=job_a.id, db=db, user=user_b),
        "get_job_logs": lambda: jobs_routes.get_job_logs(job_id=job_a.id, db=db, user=user_b),
        "list_job_files": lambda: jobs_routes.list_job_files(job_id=job_a.id, db=db, user=user_b),
        "get_job_file_content": lambda: jobs_routes.get_job_file_content(
            job_id=job_a.id, file_key="x", preview=False, db=db, user=user_b
        ),
        "get_job_results_summary": lambda: jobs_routes.get_job_results_summary(job_id=job_a.id, db=db, user=user_b),
        "cancel_job": lambda: jobs_routes.cancel_job(job_id=job_a.id, db=db, user=user_b),
        "retry_job": lambda: jobs_routes.retry_job(job_id=job_a.id, from_scratch=False, db=db, user=user_b),
        "delete_job": lambda: jobs_routes.delete_job(job_id=job_a.id, db=db, user=user_b),
        "get_upload_status": lambda: upload_routes.get_upload_status(file_id=dataset_a.id, db=db, user=user_b),
        "download_uploaded_dataset": lambda: upload_routes.download_uploaded_dataset(
            file_id=dataset_a.id, preview=False, db=db, user=user_b
        ),
        "delete_uploaded_dataset": lambda: upload_routes.delete_uploaded_dataset(
            file_id=dataset_a.id, db=db, user=user_b
        ),
        "upload_phenotype_file": lambda: upload_routes.upload_phenotype_file(
            file_id=dataset_a.id, file=dummy_file, db=db, user=user_b
        ),
        "delete_phenotype_file": lambda: upload_routes.delete_phenotype_file(
            file_id=dataset_a.id, db=db, user=user_b
        ),
        "upload_annotation_file": lambda: upload_routes.upload_annotation_file(
            file_id=dataset_a.id, file=dummy_file, db=db, user=user_b
        ),
        "delete_annotation_file": lambda: upload_routes.delete_annotation_file(
            file_id=dataset_a.id, db=db, user=user_b
        ),
        "inspect_dataset_and_phenotype": lambda: upload_routes.inspect_dataset_and_phenotype(
            file_id=dataset_a.id, trait=None, id_column="IID", fid_column="FID", db=db, user=user_b
        ),
        "download_inspection_file": lambda: upload_routes.download_inspection_file(
            file_id=dataset_a.id, name="keep_ids.txt", db=db, user=user_b
        ),
        "upload_chunk": lambda: upload_routes.upload_chunk(
            file_id=dataset_a.id, chunk_index=0, total_chunks=1, chunk=dummy_file, db=db, user=user_b
        ),
        "finalize_upload": lambda: upload_routes.finalize_upload(
            file_id=dataset_a.id, total_chunks=1, expected_sha256=None, db=db, user=user_b
        ),
        "start_pipeline_endpoint": lambda: pipeline_routes.start_pipeline_endpoint(
            dataset_id=dataset_a.id, payload=FullPipelineRequest(), db=db, user=user_b
        ),
        "export_job_results": lambda: export_routes.export_job_results(job_id=str(job_a.id), db=db, user=user_b),
        "download_summary_report": lambda: report_routes.download_summary_report(
            job_id=job_a.id, db=db, user=user_b
        ),
    }

    with pytest.raises(HTTPException) as exc:
        invokers[route_name]()
    assert exc.value.status_code in {403, 404}
