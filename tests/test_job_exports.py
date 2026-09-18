import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.app import get_app
from fastapi_plantilla.core.crud.export_job import handle_exports_generate
from fastapi_plantilla.core.crud.exporter import ExportJobPayload
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.jobs.schema import JobContext


def _user_to_response(user: User) -> UserResponse:
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


@pytest.mark.anyio
async def test_handle_exports_generate(dbsession: AsyncSession) -> None:
    """Test background handler generates export file and uploads it to storage."""
    # Create sample company
    user = User(name="Owner", email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
    dbsession.add(user)
    await dbsession.flush()

    company = Company(
        name="Acme Corp",
        nif=f"B{uuid.uuid4().hex[:7].upper()}",
        sector="Technology",
        owner_id=user.id,
    )
    dbsession.add(company)
    await dbsession.flush()

    payload = ExportJobPayload(
        resource_name="companies",
        request={"format": "csv"},
        scope={
            "scope": "GLOBAL",
            "user_id": str(user.id),
            "is_super_admin": True,
        },
    )

    mock_update_progress = AsyncMock()
    ctx = JobContext(
        job_id=uuid.uuid4(),
        name="exports.generate",
        payload=payload,
        entity_type="companies",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=mock_update_progress,
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    result = await handle_exports_generate(ctx)

    assert result is not None
    assert "file_key" in result
    assert result["filename"].startswith("company")
    assert result["filename"].endswith(".csv")
    assert result["media_type"] == "text/csv; charset=utf-8"
    assert result["size_bytes"] > 0
    assert "download_url" in result
    mock_update_progress.assert_awaited()


@pytest.mark.anyio
async def test_api_export_async_job(dbsession: AsyncSession) -> None:
    """Test POST /export?async_job=true returns 202 Accepted with JobResponse."""

    user = User(
        name="SuperAdmin",
        email=f"super-{uuid.uuid4().hex[:8]}@example.com",
        is_super_admin=True,
    )
    dbsession.add(user)
    await dbsession.flush()

    user_resp = _user_to_response(user)
    app = get_app()
    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: user_resp
    app.dependency_overrides[get_current_active_superuser] = lambda: user_resp

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/companies/export?async_job=true",
                json={"format": "csv"},
            )
            assert resp.status_code == 202
            body = resp.json()
            assert body["name"] == "exports.generate"
            assert body["status"] == "PENDING"
            assert body["entity_type"] == "companies"
            assert "payload" in body
            assert body["payload"]["resource_name"] == "companies"
    finally:
        app.dependency_overrides.clear()
