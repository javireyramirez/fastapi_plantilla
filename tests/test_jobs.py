import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi_plantilla.app import get_app
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.jobs.exceptions import (
    JobCancelledError,
    JobLeaseLostError,
)
from fastapi_plantilla.modules.jobs.models import JobStatus
from fastapi_plantilla.modules.jobs.registry import (
    job_registry,
    register_job,
)
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.schema import (
    JobContext,
    JobCreateRequest,
)
from fastapi_plantilla.modules.jobs.service import JobService, calculate_backoff_delay
from fastapi_plantilla.modules.jobs.worker import BackgroundJobWorker


def user_to_response(user: User) -> UserResponse:
    """Map DB User model to Pydantic UserResponse."""
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        is_active=user.is_active,
        is_super_admin=user.is_super_admin,
        is_system=user.is_system,
        email_verified=user.email_verified,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@pytest.fixture
async def job_users(dbsession: AsyncSession) -> tuple[UserResponse, UserResponse]:
    """Create test admin and normal users."""
    user_repo = BaseRepository(User, dbsession)
    admin = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Admin User",
            "email": f"admin-{uuid.uuid4().hex[:8]}@example.com",
            "is_active": True,
            "is_super_admin": True,
        }
    )
    user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular User",
            "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
            "is_active": True,
            "is_super_admin": False,
        }
    )
    await dbsession.commit()
    return user_to_response(admin), user_to_response(user)


@pytest.mark.anyio
async def test_job_model_and_repository_claim(dbsession: AsyncSession) -> None:
    """Test repository create and single claim."""
    repo = JobRepository(dbsession)
    job = await repo.create(
        name="test.simple",
        payload={"foo": "bar"},
        entity_type="company",
        entity_id=generate_uuid7(),
    )
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert job.lease_token == 0

    claimed = await repo.claim_next_job()
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.lease_token == 1
    assert claimed.started_at is not None
    assert claimed.lease_expires_at is not None

    # Claiming again returns None because no pending or expired jobs exist
    claimed_again = await repo.claim_next_job()
    assert claimed_again is None


@pytest.mark.anyio
async def test_job_fencing_token_protection(dbsession: AsyncSession) -> None:
    """Verify that a worker with an outdated lease token cannot update progress."""
    repo = JobRepository(dbsession)
    await repo.create(name="test.fencing", payload={})
    claimed = await repo.claim_next_job()
    assert claimed is not None

    stale_token = claimed.lease_token  # token = 1

    # Simulate another claim (e.g. lease expired or taken over)
    # Manually incrementing the token to simulate loss of lease
    claimed.lease_token = 2
    await dbsession.flush()

    # Attempting to update progress with stale token 1 raises JobLeaseLostError
    with pytest.raises(JobLeaseLostError):
        await repo.update_progress_with_fencing(
            job_id=claimed.id,
            lease_token=stale_token,
            progress=50,
            message="Processing...",
        )


@pytest.mark.anyio
async def test_job_service_execution_flow(dbsession: AsyncSession) -> None:
    """Test full execution of a registered job via JobService."""
    job_registry.clear()

    class MathPayload(BaseModel):
        x: int
        y: int

    @register_job("math.add", payload_model=MathPayload)
    async def add_handler(ctx: JobContext[MathPayload]) -> dict[str, Any]:
        await ctx.update_progress(50, "Halfway done")
        return {"sum": ctx.payload.x + ctx.payload.y}

    repo = JobRepository(dbsession)
    service = JobService(repo)

    req = JobCreateRequest(
        name="math.add",
        payload={"x": 10, "y": 25},
        entity_type="calculation",
        entity_id=generate_uuid7(),
    )
    enqueued = await service.enqueue(req)
    assert enqueued.status == JobStatus.PENDING

    claimed = await repo.claim_next_job()
    assert claimed is not None

    await service.execute_claimed_job(claimed)

    finished = await repo.get_by_id(claimed.id)
    assert finished is not None
    assert finished.status == JobStatus.COMPLETED
    assert finished.progress == 100
    assert finished.result == {"sum": 35}
    assert finished.error is None


@pytest.mark.anyio
async def test_job_cancellation_cooperative(dbsession: AsyncSession) -> None:
    """Test cooperative cancellation when update_progress detects CANCELLED status."""
    job_registry.clear()

    cancelled_detected = False

    @register_job("test.long_task")
    async def long_task_handler(ctx: JobContext[dict[str, Any]]) -> dict[str, Any]:
        nonlocal cancelled_detected
        try:
            await ctx.update_progress(10, "Started")
            # Simulate cancel externally
            cancel_repo = JobRepository(ctx.session)
            await cancel_repo.cancel_job(ctx.job_id)

            await ctx.update_progress(20, "Should abort")
        except JobCancelledError:
            cancelled_detected = True
            raise
        return {"done": True}

    repo = JobRepository(dbsession)
    service = JobService(repo)

    req = JobCreateRequest(name="test.long_task", payload={})
    enqueued = await service.enqueue(req)

    claimed = await repo.claim_next_job()
    assert claimed is not None

    await service.execute_claimed_job(claimed)
    assert cancelled_detected is True

    final_job = await repo.get_by_id(enqueued.id)
    assert final_job is not None
    assert final_job.status == JobStatus.CANCELLED


@pytest.mark.anyio
async def test_job_backoff_and_retry(dbsession: AsyncSession) -> None:
    """Verify exponential backoff calculation and reschedule on failure."""
    d1 = calculate_backoff_delay(1)
    d2 = calculate_backoff_delay(2)
    d3 = calculate_backoff_delay(3)
    assert 5.0 <= d1.total_seconds() <= 7.0
    assert 10.0 <= d2.total_seconds() <= 12.0
    assert 20.0 <= d3.total_seconds() <= 22.0

    job_registry.clear()

    @register_job("test.fail")
    async def fail_handler(ctx: JobContext[dict[str, Any]]) -> None:
        raise RuntimeError("Transient network issue")

    repo = JobRepository(dbsession)
    service = JobService(repo)

    req = JobCreateRequest(name="test.fail", payload={}, max_retries=2)
    await service.enqueue(req)

    # Attempt 1
    claimed = await repo.claim_next_job()
    assert claimed is not None
    assert claimed.attempts == 1
    await service.execute_claimed_job(claimed)

    reloaded = await repo.get_by_id(claimed.id)
    assert reloaded is not None
    assert reloaded.status == JobStatus.PENDING
    assert reloaded.error == "Unhandled error: Transient network issue"
    assert reloaded.scheduled_at > datetime.now(UTC)


@pytest.mark.anyio
async def test_job_idempotency_and_purge(
    dbsession: AsyncSession,
    job_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify deduplication with idempotency_key and purge_old_jobs."""
    repo = JobRepository(dbsession)
    service = JobService(repo)
    user1, user2 = job_users

    req = JobCreateRequest(
        name="test.idempotent",
        payload={"data": 123},
        idempotency_key="unique-key-abc",
    )
    job1 = await service.enqueue(req)
    job2 = await service.enqueue(req)
    assert job1.id == job2.id

    # Namespaced idempotency test (prevent cross-user leakage)
    req_scoped = JobCreateRequest(
        name="test.scoped_idem",
        payload={"foo": "bar"},
        idempotency_key="same-key-123",
    )
    job_u1 = await service.enqueue(req_scoped, created_by_id=user1.id)
    job_u1_again = await service.enqueue(req_scoped, created_by_id=user1.id)
    assert job_u1.id == job_u1_again.id

    job_u2 = await service.enqueue(req_scoped, created_by_id=user2.id)
    assert job_u2.id != job_u1.id  # Completely isolated across users

    # Test purge
    purged = await repo.purge_old_jobs(retention_days=30)
    assert purged == 0


@pytest.mark.anyio
async def test_jobs_api_endpoints(
    dbsession: AsyncSession,
    job_users: tuple[UserResponse, UserResponse],
) -> None:
    """Test full HTTP REST endpoints: enqueue, get, list, cancel, retry."""
    admin, _user = job_users
    app = get_app()
    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_current_active_superuser] = lambda: admin

    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test", timeout=5.0
    ) as client:
        # 1. Enqueue job
        resp = await client.post(
            "/api/jobs",
            json={
                "name": "reports.generate",
                "payload": {"year": 2026},
                "entity_type": "report",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        job_id = data["id"]
        assert data["name"] == "reports.generate"
        assert data["status"] == "PENDING"
        assert data["progress"] == 0

        # 2. Get job
        resp_get = await client.get(f"/api/jobs/{job_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == job_id

        # 3. List jobs
        resp_list = await client.get("/api/jobs?name=reports.generate")
        assert resp_list.status_code == 200
        list_json = resp_list.json()
        assert len(list_json["data"]) >= 1
        assert list_json["meta"]["total"] >= 1

        # 4. Cancel job
        resp_cancel = await client.post(f"/api/jobs/{job_id}/cancel")
        assert resp_cancel.status_code == 200
        assert resp_cancel.json()["status"] == "CANCELLED"

        # 5. Retry job (superuser endpoint)
        resp_retry = await client.post(f"/api/jobs/{job_id}/retry")
        assert resp_retry.status_code == 200
        assert resp_retry.json()["status"] == "PENDING"


@pytest.mark.anyio
async def test_jobs_rbac_own_scope_isolation(
    dbsession: AsyncSession,
    job_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify that a user with OWN scope cannot see or cancel another user's job."""
    admin, user = job_users
    repo = JobRepository(dbsession)
    admin_job = await repo.create(
        name="admin.job",
        payload={},
        created_by_id=admin.id,
    )
    user_job = await repo.create(
        name="user.job",
        payload={},
        created_by_id=user.id,
    )
    await dbsession.commit()

    app = get_app()
    app.dependency_overrides[get_db_session] = lambda: dbsession

    # Mock RBAC service granting OWN scope for user on jobs
    from fastapi_plantilla.core.crud.schema import ScopeType
    from fastapi_plantilla.modules.rbac.dependencies import get_rbac_service
    from fastapi_plantilla.modules.rbac.schema import RbacActions

    class OwnRbacService:
        async def resolve_user_permission(
            self,
            user_id: uuid.UUID,
            module_code: str,
            action: Any,
            is_super_admin: bool,
        ) -> tuple[Any, list[Any], list[Any]]:
            if module_code == "jobs" and action in (
                RbacActions.READ,
                RbacActions.CREATE,
                RbacActions.UPDATE,
                RbacActions.SETTINGS,
            ):
                return ScopeType.OWN, [], []
            return None, [], []

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_rbac_service] = OwnRbacService

    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test", timeout=5.0
    ) as client:
        # User only lists own jobs
        res = await client.get("/api/jobs")
        assert res.status_code == 200
        listed_ids = [item["id"] for item in res.json()["data"]]
        assert str(user_job.id) in listed_ids
        assert str(admin_job.id) not in listed_ids

        # User cannot fetch foreign job (anti-oracle 404)
        res_foreign = await client.get(f"/api/jobs/{admin_job.id}")
        assert res_foreign.status_code == 404

        # User cannot cancel foreign job (anti-oracle 404)
        res_cancel = await client.post(f"/api/jobs/{admin_job.id}/cancel")
        assert res_cancel.status_code == 404

        # User cannot retry foreign job (anti-oracle 404)
        res_retry = await client.post(f"/api/jobs/{admin_job.id}/retry")
        assert res_retry.status_code == 404


@pytest.mark.anyio
async def test_job_worker_lifecycle(dbsession: AsyncSession) -> None:
    """Test BackgroundJobWorker polling, execution, and graceful stop."""
    job_registry.clear()
    executed = False

    @register_job("worker.ping")
    async def ping_handler(ctx: JobContext[dict[str, Any]]) -> dict[str, str]:
        nonlocal executed
        executed = True
        return {"pong": "ok"}

    repo = JobRepository(dbsession)
    await repo.create(name="worker.ping", payload={})
    await dbsession.commit()

    session_maker = async_sessionmaker(dbsession.bind, expire_on_commit=False)
    worker = BackgroundJobWorker(
        session_factory=session_maker,
        max_concurrency=2,
        poll_interval=0.05,
        shutdown_timeout=2.0,
    )

    worker.start()
    # Wait briefly for worker to pick up and process job
    for _ in range(20):
        if executed:
            break
        await asyncio.sleep(0.05)

    await worker.stop()
    assert executed is True
