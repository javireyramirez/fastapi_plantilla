import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.audit.jobs import (
    AuditPurgeJobPayload,
    handle_audit_purge,
)
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.jobs.models import Job, JobStatus
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.trash.jobs import (
    TrashPurgeJobPayload,
    handle_trash_purge,
)
from fastapi_plantilla.modules.trash.models import TrashItem


@pytest.mark.anyio
async def test_handle_trash_purge(dbsession: AsyncSession) -> None:
    """Test trash.purge removes expired items and chains next scheduled run."""
    past = datetime.now(UTC) - timedelta(days=40)
    item = TrashItem(
        name="Old Company",
        entity_type="companies",
        entity_id=uuid.uuid4(),
        expires_at=past,
    )
    dbsession.add(item)
    await dbsession.flush()

    ctx = JobContext(
        job_id=uuid.uuid4(),
        name="trash.purge",
        payload=TrashPurgeJobPayload(limit=100),
        entity_type="trash",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=AsyncMock(),
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    result = await handle_trash_purge(ctx)

    assert result is not None
    assert result["status"] == "completed"
    assert result["purged_count"] >= 1
    assert "next_scheduled_at" in result

    # Check next scheduled job in DB
    stmt = select(Job).where(
        Job.name == "trash.purge",
        Job.status == JobStatus.PENDING,
    )
    res = await dbsession.execute(stmt)
    next_job = res.scalar_one_or_none()
    assert next_job is not None
    assert next_job.idempotency_key is not None
    assert next_job.idempotency_key.startswith("trash.purge:")


@pytest.mark.anyio
async def test_handle_audit_purge(dbsession: AsyncSession) -> None:
    """Test audit.purge removes expired audit logs and chains next scheduled run."""
    past = datetime.now(UTC) - timedelta(days=400)
    log = AuditLog(
        action="LOGIN",
        entity_type="users",
        entity_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        details="Old audit log entry",
        created_at=past,
    )
    dbsession.add(log)
    await dbsession.flush()

    ctx = JobContext(
        job_id=uuid.uuid4(),
        name="audit.purge",
        payload=AuditPurgeJobPayload(limit=100),
        entity_type="audit",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=AsyncMock(),
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    result = await handle_audit_purge(ctx)

    assert result is not None
    assert result["status"] == "completed"
    assert "next_scheduled_at" in result

    # Check next scheduled job in DB
    stmt = select(Job).where(
        Job.name == "audit.purge",
        Job.status == JobStatus.PENDING,
    )
    res = await dbsession.execute(stmt)
    next_job = res.scalar_one_or_none()
    assert next_job is not None
    assert next_job.idempotency_key is not None
    assert next_job.idempotency_key.startswith("audit.purge:")
