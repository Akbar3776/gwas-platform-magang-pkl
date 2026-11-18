from datetime import datetime, timezone
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest

from app.api.routes import results
from app.core.security import get_current_user, require_job_owner
from app.db.engine.models import AnalysisJob, User, UserRole
from tests import conftest


class FakeSession:
    def __init__(self, jobs=None, users=None):
        self.jobs = {str(j.id): j for j in (jobs or [])}
        self.users = {str(u.id): u for u in (users or [])}

    def get(self, model, object_id):
        if model is AnalysisJob:
            return self.jobs.get(str(object_id))
        if model is User:
            return self.users.get(str(object_id))
        return None


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
