import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.app import get_app
from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.events import (
    EventBroadcaster,
    event_broadcaster,
    format_sse,
)
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.jobs.registry import job_registry
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.notifications.jobs import (
    handle_notifications_fan_out,
)
from fastapi_plantilla.modules.notifications.models import (
    NotificationType,
)
from fastapi_plantilla.modules.notifications.repository import (
    NotificationRepository,
)
from fastapi_plantilla.modules.notifications.schema import (
    NotificationFanOutPayload,
    NotificationFilterParams,
)
from fastapi_plantilla.modules.notifications.service import NotificationService


@pytest.fixture(autouse=True)
def clean_broadcaster() -> None:
    """Clear broadcaster queues before each test to maintain isolation."""
    event_broadcaster.clear()


async def _create_test_user(
    dbsession: AsyncSession, is_super: bool = False
) -> UserResponse:
    uid = generate_uuid7()
    user = User(
        id=uid,
        name=f"User {uid}",
        email=f"user_{uid}@example.com",
        email_verified=True,
        is_active=True,
        is_system=False,
        is_super_admin=is_super,
    )
    dbsession.add(user)
    await dbsession.flush()
    now = datetime.now(UTC)
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        is_system=user.is_system,
        is_super_admin=user.is_super_admin,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# 1. Tests de Utilidades y EventBroadcaster
# ---------------------------------------------------------------------------


def test_format_sse() -> None:
    """Test standard W3C SSE text formatting."""
    formatted = format_sse(
        data={"message": "hello", "count": 1},
        event="test_event",
        id="evt-1",
    )
    assert "id: evt-1\n" in formatted
    assert "event: test_event\n" in formatted
    assert 'data: {"message": "hello", "count": 1}\n\n' in formatted


@pytest.mark.anyio
async def test_event_broadcaster_subscribe_and_publish() -> None:
    """Test in-memory pub/sub for a user."""
    broadcaster = EventBroadcaster()
    user_id = generate_uuid7()

    gen = broadcaster.subscribe_user(user_id)
    # Initiate subscription by waiting for first item in background
    consumer_task = asyncio.create_task(anext(gen))

    # Wait a moment for queue registration
    await asyncio.sleep(0.01)

    broadcaster.publish_to_user(
        user_id,
        event="alert",
        data={"text": "warning"},
    )

    msg = await asyncio.wait_for(consumer_task, timeout=2.0)
    assert "event: alert\n" in msg
    assert '"text": "warning"' in msg

    await gen.aclose()


@pytest.mark.anyio
async def test_event_broadcaster_anti_dos_eviction() -> None:
    """Test that exceeding sse_max_queues_per_user evicts oldest queue via FIFO."""
    broadcaster = EventBroadcaster()
    user_id = generate_uuid7()
    max_queues = settings.sse_max_queues_per_user

    generators = [broadcaster.subscribe_user(user_id) for _ in range(max_queues)]
    tasks = [asyncio.create_task(anext(g)) for g in generators]
    await asyncio.sleep(0.02)

    overflow_gen = broadcaster.subscribe_user(user_id)
    overflow_task = asyncio.create_task(anext(overflow_gen))
    await asyncio.sleep(0.02)

    # User active queues count must be exactly capped at max_queues
    assert broadcaster.get_user_queue_count(user_id) == max_queues

    # Oldest generator (FIFO) was evicted with sentinel None, terminating cleanly
    with pytest.raises(StopAsyncIteration):
        await tasks[0]

    from contextlib import suppress

    for t in [*tasks[1:], overflow_task]:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t
    for g in [*generators, overflow_gen]:
        await g.aclose()


@pytest.mark.anyio
async def test_event_broadcaster_job_anti_dos_eviction() -> None:
    """Test that exceeding sse_max_queues_per_job evicts oldest queue via FIFO."""
    broadcaster = EventBroadcaster()
    job_id = generate_uuid7()
    max_queues = settings.sse_max_queues_per_job

    generators = [broadcaster.subscribe_job(job_id) for _ in range(max_queues)]
    tasks = [asyncio.create_task(anext(g)) for g in generators]
    await asyncio.sleep(0.02)

    overflow_gen = broadcaster.subscribe_job(job_id)
    overflow_task = asyncio.create_task(anext(overflow_gen))
    await asyncio.sleep(0.02)

    # Job active queues count must be exactly capped at max_queues
    assert broadcaster.get_job_queue_count(job_id) == max_queues

    # Oldest generator (FIFO) was evicted with sentinel None, terminating cleanly
    with pytest.raises(StopAsyncIteration):
        await tasks[0]

    from contextlib import suppress

    for t in [*tasks[1:], overflow_task]:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t
    for g in [*generators, overflow_gen]:
        await g.aclose()


# ---------------------------------------------------------------------------
# 2. Tests de Repositorio y Servicio de Notificaciones
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notification_repository_crud(dbsession: AsyncSession) -> None:
    """Test notification persistence, unread counting, and marking as read."""
    user = await _create_test_user(dbsession)
    repo = NotificationRepository(dbsession)

    # 1. Create notification
    n1 = await repo.create(
        recipient_id=user.id,
        title="Welcome",
        message="Account created successfully",
        notification_type=NotificationType.SUCCESS,
        entity_type="user",
        entity_id=user.id,
    )
    assert n1.id is not None
    assert n1.notification_type == NotificationType.SUCCESS
    assert n1.read_at is None

    n2 = await repo.create(
        recipient_id=user.id,
        title="Alert",
        message="System warning",
        notification_type=NotificationType.WARNING,
    )

    # 2. Count unread
    unread_count = await repo.count_unread(user.id)
    assert unread_count == 2

    # 3. List with pagination
    items, total = await repo.list_for_user(
        user.id, NotificationFilterParams(page=1, limit=10)
    )
    assert total == 2
    assert len(items) == 2

    # 4. Mark single as read
    marked = await repo.mark_as_read(n1.id, user.id)
    assert marked is not None
    assert marked.read_at is not None

    unread_count_after = await repo.count_unread(user.id)
    assert unread_count_after == 1

    # 5. Filter by unread_only
    unread_items, unread_total = await repo.list_for_user(
        user.id, NotificationFilterParams(unread_only=True)
    )
    assert unread_total == 1
    assert unread_items[0].id == n2.id

    # 6. Mark all as read
    marked_count = await repo.mark_all_as_read(user.id)
    assert marked_count == 1
    assert await repo.count_unread(user.id) == 0

    # 7. Delete notification
    deleted = await repo.delete(n1.id, user.id)
    assert deleted is True
    assert await repo.get_by_id(n1.id, user.id) is None


@pytest.mark.anyio
async def test_notification_service_notify_and_fan_out(
    dbsession: AsyncSession,
) -> None:
    """Test NotificationService dispatching live SSE events."""
    user1 = await _create_test_user(dbsession)
    user2 = await _create_test_user(dbsession)

    broadcaster = EventBroadcaster()
    repo = NotificationRepository(dbsession)
    service = NotificationService(repo, broadcaster=broadcaster)

    # Subscribe user1 to SSE
    gen1 = broadcaster.subscribe_user(user1.id)
    task1 = asyncio.create_task(anext(gen1))
    await asyncio.sleep(0.01)

    # Notify single user
    notif = await service.notify_user(
        recipient_id=user1.id,
        title="Direct notice",
        message="Hello user 1",
        notification_type=NotificationType.INFO,
    )
    assert notif.title == "Direct notice"

    event_msg = await asyncio.wait_for(task1, timeout=2.0)
    assert "event: notification" in event_msg
    assert "Direct notice" in event_msg

    await gen1.aclose()

    # Test fan-out
    count = await service.fan_out(
        user_ids=[user1.id, user2.id],
        title="Broadcast alert",
        message="Maintenance in 10m",
        notification_type=NotificationType.SYSTEM,
    )
    assert count == 2
    assert await repo.count_unread(user1.id) == 2
    assert await repo.count_unread(user2.id) == 1


# ---------------------------------------------------------------------------
# 3. Tests de Endpoints REST de Notificaciones
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app(dbsession: AsyncSession) -> FastAPI:
    """Create test app overriding database and auth dependencies."""
    app = get_app()
    app.dependency_overrides[get_db_session] = lambda: dbsession
    return app


@pytest.mark.anyio
async def test_notifications_rest_endpoints(
    test_app: FastAPI, dbsession: AsyncSession
) -> None:
    """Test /api/notifications REST endpoints."""
    user = await _create_test_user(dbsession)
    test_app.dependency_overrides[get_current_user] = lambda: user

    repo = NotificationRepository(dbsession)
    n1 = await repo.create(
        recipient_id=user.id,
        title="First notice",
        message="Msg 1",
        notification_type=NotificationType.INFO,
    )
    n2 = await repo.create(
        recipient_id=user.id,
        title="Second notice",
        message="Msg 2",
        notification_type=NotificationType.SUCCESS,
    )

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        # 1. Unread count
        resp = await client.get("/api/notifications/unread-count")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {"unread_count": 2}

        # 2. List notifications
        resp = await client.get("/api/notifications?page=1&limit=10")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["meta"]["total"] == 2
        assert len(data["data"]) == 2

        # 3. Mark single as read
        resp = await client.patch(f"/api/notifications/{n1.id}/read")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["is_read"] is True

        # Check updated count
        resp = await client.get("/api/notifications/unread-count")
        assert resp.json()["unread_count"] == 1

        # 4. Mark all as read
        resp = await client.post("/api/notifications/read-all")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["marked_count"] == 1

        # 5. Delete notification
        resp = await client.delete(f"/api/notifications/{n2.id}")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # 6. Delete not found
        resp = await client.delete(f"/api/notifications/{n2.id}")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# 4. Tests de Endpoints SSE Stream
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_sse_stream_endpoint(
    test_app: FastAPI, dbsession: AsyncSession
) -> None:
    """Test /api/notifications/stream delivering real-time events."""
    from fastapi_plantilla.modules.notifications.routes import stream_notifications

    user = await _create_test_user(dbsession)
    response = await stream_notifications(current_user=user)
    assert response.status_code == status.HTTP_200_OK
    assert response.media_type == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Connection"] == "keep-alive"
    assert response.headers["X-Accel-Buffering"] == "no"


# ---------------------------------------------------------------------------
# 5. Tests de Integración Background Jobs & Notificaciones Post-Commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_job_execution_emits_sse_and_creates_notification(
    dbsession: AsyncSession,
) -> None:
    """Test that claimed job execution emits progress and creates notification."""
    user = await _create_test_user(dbsession)
    repo = JobRepository(dbsession)
    service = JobService(repo)

    # Register temporary test job
    job_name = f"test.job.{generate_uuid7()}"

    async def _sample_handler(ctx: JobContext[Any]) -> dict[str, Any]:
        await ctx.update_progress(50, "Halfway done")
        return {"result_key": "success_val"}

    job_registry.register(job_name, _sample_handler, override=True)

    # Enqueue job with user ownership and claim it properly
    created_job = await repo.create(
        name=job_name,
        payload={"foo": "bar"},
        created_by_id=user.id,
    )
    claimed = await repo.claim_next_job()
    assert claimed is not None
    assert claimed.id == created_job.id

    # Listen to job events on broadcaster
    job_events: list[str] = []
    job_gen = event_broadcaster.subscribe_job(claimed.id)

    async def _collect_job_events() -> None:
        async for item in job_gen:
            job_events.append(item)
            if "job_completed" in item:
                break

    collector_task = asyncio.create_task(_collect_job_events())
    for _ in range(50):
        if event_broadcaster.has_job_listener(claimed.id):
            break
        await asyncio.sleep(0.01)

    # Execute claimed job
    post_commit_actions = await service.execute_claimed_job(claimed)

    # Verify notification was created inside transaction
    notif_repo = NotificationRepository(dbsession)
    items, total = await notif_repo.list_for_user(user.id, NotificationFilterParams())
    assert total == 1
    assert items[0].notification_type == NotificationType.JOB_COMPLETED
    assert f"Tarea '{job_name}' finalizada" in items[0].title

    # Simulate worker post-commit dispatch
    for action in post_commit_actions:
        action()

    await asyncio.wait_for(collector_task, timeout=3.0)
    await job_gen.aclose()

    # Verify both progress and completion events were captured
    all_events_text = "".join(job_events)
    assert "job_progress" in all_events_text
    assert "Halfway done" in all_events_text
    assert "job_completed" in all_events_text


@pytest.mark.anyio
async def test_job_terminal_failure_creates_notification(
    dbsession: AsyncSession,
) -> None:
    """Test that a job exceeding max retries creates a failure notification."""
    user = await _create_test_user(dbsession)
    repo = JobRepository(dbsession)
    service = JobService(repo)

    job_name = f"test.failing.{generate_uuid7()}"

    async def _failing_handler(ctx: JobContext[Any]) -> dict[str, Any]:
        raise RuntimeError("Fatal unrecoverable error")

    job_registry.register(job_name, _failing_handler, override=True)

    # Create job and claim it
    await repo.create(
        name=job_name,
        payload={},
        max_retries=1,
        created_by_id=user.id,
    )
    claimed = await repo.claim_next_job()
    assert claimed is not None
    claimed.attempts = 1  # Will reach max retries on this execution

    post_actions = await service.execute_claimed_job(claimed)
    for action in post_actions:
        action()

    notif_repo = NotificationRepository(dbsession)
    items, total = await notif_repo.list_for_user(user.id, NotificationFilterParams())
    assert total == 1
    assert items[0].notification_type == NotificationType.JOB_FAILED
    assert "Fatal unrecoverable error" in items[0].message


# ---------------------------------------------------------------------------
# 6. Test de Job notifications.fan_out
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_fan_out_job_handler(
    dbsession: AsyncSession,
) -> None:
    """Test background execution of notifications.fan_out handler."""
    user1 = await _create_test_user(dbsession)
    user2 = await _create_test_user(dbsession)

    payload = NotificationFanOutPayload(
        user_ids=[user1.id, user2.id],
        title="Important Update",
        message="System patched",
        type=NotificationType.SYSTEM,
    )

    progress_reports: list[int] = []

    async def _mock_progress(
        job_id: uuid.UUID, lease: int, progress: int, message: str | None
    ) -> None:
        progress_reports.append(progress)

    async def _mock_cancelled(job_id: uuid.UUID, lease: int) -> bool:
        return False

    ctx = JobContext(
        job_id=generate_uuid7(),
        name="notifications.fan_out",
        payload=payload,
        entity_type=None,
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=_mock_progress,
        _check_cancelled_fn=_mock_cancelled,
    )

    result = await handle_notifications_fan_out(ctx)
    assert result == {"dispatched_count": 2}
    assert 100 in progress_reports

    notif_repo = NotificationRepository(dbsession)
    assert await notif_repo.count_unread(user1.id) == 1
    assert await notif_repo.count_unread(user2.id) == 1
