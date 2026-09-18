import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.email.email_log_service import EmailLogService
from fastapi_plantilla.modules.email.repository import EmailLogRepository
from fastapi_plantilla.modules.email.schema import EmailLogStatus
from fastapi_plantilla.modules.jobs.repository import JobRepository


@pytest.mark.anyio
async def test_email_log_repository_and_service(dbsession: AsyncSession) -> None:
    """Test EmailLogService records and updates execution attempts."""
    job_repo = JobRepository(dbsession)
    job = await job_repo.create("emails.send", {"test": True})

    repo = EmailLogRepository(dbsession)
    service = EmailLogService(repo)

    log = await service.record_attempt(
        to=["test@example.com"],
        subject="Bienvenido",
        template_name="auth/welcome.html",
        job_id=job.id,
        attempt=1,
        status=EmailLogStatus.PENDING,
    )
    assert log.id is not None
    assert log.status == "PENDING"
    assert log.job_id == job.id
    assert log.attempt == 1

    # Update attempt to SENT
    updated = await service.record_attempt(
        to=["test@example.com"],
        subject="Bienvenido",
        template_name="auth/welcome.html",
        job_id=job.id,
        attempt=1,
        status=EmailLogStatus.SENT,
    )
    assert updated.id == log.id
    assert updated.status == "SENT"
    assert updated.sent_at is not None

    # Next attempt calculation
    next_attempt = await repo.get_next_attempt(job.id)
    assert next_attempt == 2


@pytest.mark.anyio
async def test_email_logs_api_endpoint(
    dbsession: AsyncSession,
    fastapi_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Test /api/email-logs list endpoint with super admin access."""
    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "name": "Super Admin",
            "email": "superadmin@example.com",
            "is_active": True,
            "email_verified": True,
            "is_super_admin": True,
        }
    )

    # Seed an email log
    repo = EmailLogRepository(dbsession)
    service = EmailLogService(repo)
    await service.record_attempt(
        to=["audit@example.com"],
        subject="Registro de auditoría",
        template_name="auth/verify_email.html",
        status=EmailLogStatus.SENT,
    )

    admin_response = UserResponse.model_validate(admin_user)
    fastapi_app.dependency_overrides[get_current_user] = lambda: admin_response

    res = await client.get("/api/email-logs")
    assert res.status_code == 200
    data = res.json()
    assert "data" in data
    assert len(data["data"]) >= 1
    found = any(item["subject"] == "Registro de auditoría" for item in data["data"])
    assert found is True
