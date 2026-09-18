import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.email.builder import EmailPayload
from fastapi_plantilla.modules.email.jobs import handle_email_send
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.jobs.schema import JobContext, JobResponse


@pytest.mark.anyio
async def test_handle_email_send(dbsession: AsyncSession) -> None:
    """Test background handler sends email via configured transport."""
    payload = EmailPayload(
        to=["test@example.com"],
        subject="Hello from test",
        text="Plain body",
    )
    mock_update_progress = AsyncMock()
    ctx = JobContext(
        job_id=uuid.uuid4(),
        name="emails.send",
        payload=payload,
        entity_type="email",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=mock_update_progress,
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    with patch(
        "fastapi_plantilla.modules.email.jobs.get_email_transport"
    ) as mock_get_trans:
        mock_transport = AsyncMock()
        mock_get_trans.return_value = mock_transport

        result = await handle_email_send(ctx)

        assert result is not None
        assert result["status"] == "sent"
        assert result["to"] == ["test@example.com"]
        assert result["subject"] == "Hello from test"
        mock_transport.send.assert_awaited_once_with(payload=payload)
        mock_update_progress.assert_awaited_once()


@pytest.mark.anyio
async def test_email_service_enqueue_send_enabled(dbsession: AsyncSession) -> None:
    """Test enqueue_send returns JobResponse when background worker is enabled."""
    transport = AsyncMock()
    renderer = TemplateRenderer()
    service = EmailService(transport=transport, renderer=renderer)

    payload = EmailPayload(
        to=["async@example.com"],
        subject="Async email",
        text="Background content",
    )

    with patch.object(settings, "jobs_worker_enabled", True):
        res = await service.enqueue_send(payload, session=dbsession)
        assert isinstance(res, JobResponse)
        assert res.name == "emails.send"
        assert res.payload["to"] == ["async@example.com"]


@pytest.mark.anyio
async def test_email_service_enqueue_send_fallback(dbsession: AsyncSession) -> None:
    """Test enqueue_send degrades gracefully to direct sending if worker is disabled."""
    transport = AsyncMock()
    renderer = TemplateRenderer()
    service = EmailService(transport=transport, renderer=renderer)

    payload = EmailPayload(
        to=["fallback@example.com"],
        subject="Direct email",
        text="Direct content",
    )

    with patch.object(settings, "jobs_worker_enabled", False):
        res = await service.enqueue_send(payload, session=dbsession)
        assert res is True
        transport.send.assert_awaited_once_with(payload=payload)


@pytest.mark.anyio
async def test_auth_and_users_enqueue_send_integration(
    dbsession: AsyncSession,
) -> None:
    """Verify auth and users services dispatch emails via enqueue_send."""
    from fastapi_plantilla.core.crud.repository import BaseRepository
    from fastapi_plantilla.modules.auth.models import User
    from fastapi_plantilla.modules.auth.repository import AuthRepository
    from fastapi_plantilla.modules.auth.schema import ForgotPasswordRequest
    from fastapi_plantilla.modules.auth.service import AuthService
    from fastapi_plantilla.modules.users.repository import UserAdminRepository
    from fastapi_plantilla.modules.users.service import UserAdminService

    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "name": "Integration User",
            "email": "integration@example.com",
            "is_active": True,
            "email_verified": True,
        }
    )

    mock_email_service = AsyncMock(spec=EmailService)
    mock_email_service.create_builder = EmailService(
        transport=AsyncMock(), renderer=TemplateRenderer()
    ).create_builder

    auth_service = AuthService(
        repository=AuthRepository(dbsession),
        email_service=mock_email_service,
        settings_service=None,
    )

    with patch.object(settings, "frontend_url", "http://localhost:3000"):
        success = await auth_service.forget_password(
            ForgotPasswordRequest(email="integration@example.com")
        )
        assert success is True
        mock_email_service.enqueue_send.assert_awaited_once()

    mock_email_service.enqueue_send.reset_mock()
    user_service = UserAdminService(
        repository=UserAdminRepository(dbsession),
        email_service=mock_email_service,
        settings_service=None,
    )
    with patch.object(settings, "frontend_url", "http://localhost:3000"):
        await user_service._send_invitation_email(user)  # noqa: SLF001
        mock_email_service.enqueue_send.assert_awaited_once()


@pytest.mark.anyio
async def test_email_service_enqueue_send_omits_html_when_template_present(
    dbsession: AsyncSession,
) -> None:
    """Test enqueue_send strips raw HTML and persists template info."""
    transport = AsyncMock()
    renderer = TemplateRenderer()
    service = EmailService(transport=transport, renderer=renderer)

    builder = (
        service.create_builder()
        .to("recipient@example.com")
        .subject("Prueba de plantilla")
        .template(
            "auth/verify_email.html",
            name="Bob",
            verify_link="http://localhost:3000/verify?token=123",
            expiry_hours=24,
        )
    )

    with patch.object(settings, "jobs_worker_enabled", True):
        res = await service.enqueue_send(builder, session=dbsession)
        assert isinstance(res, JobResponse)
        assert res.payload["html"] is None
        assert res.payload["template_name"] == "auth/verify_email.html"
        assert res.payload["template_context"]["name"] == "Bob"
        assert res.payload["template_context"]["expiry_hours"] == 24


@pytest.mark.anyio
async def test_handle_email_send_renders_template_and_logs(
    dbsession: AsyncSession,
) -> None:
    """Test background handler renders HTML from template on-the-fly and records log."""
    from fastapi_plantilla.modules.email.repository import EmailLogRepository

    payload = EmailPayload(
        to=["recipient@example.com"],
        subject="Verificación",
        template_name="auth/verify_email.html",
        template_context={
            "name": "Carlos",
            "verify_link": "http://localhost:3000/verify-email?token=xyz",
            "expiry_hours": 48,
        },
        html=None,
    )
    from fastapi_plantilla.modules.jobs.repository import JobRepository

    job_repo = JobRepository(dbsession)
    job = await job_repo.create("emails.send", payload.model_dump(mode="json"))
    job_id = job.id
    ctx = JobContext(
        job_id=job_id,
        name="emails.send",
        payload=payload,
        entity_type="email",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=AsyncMock(),
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    with patch(
        "fastapi_plantilla.modules.email.jobs.get_email_transport"
    ) as mock_get_trans:
        mock_transport = AsyncMock()
        mock_get_trans.return_value = mock_transport

        result = await handle_email_send(ctx)

        assert result is not None
        assert result["status"] == "sent"
        assert payload.html is not None
        assert "Carlos" in payload.html
        assert "http://localhost:3000/verify-email?token=xyz" in payload.html
        mock_transport.send.assert_awaited_once_with(payload=payload)

        # Verify record in sys_email_logs
        repo = EmailLogRepository(dbsession)
        log = await repo.get_by_job_id_and_attempt(job_id, attempt=1)
        assert log is not None
        assert log.status == "SENT"
        assert log.template_name == "auth/verify_email.html"
        assert log.to == ["recipient@example.com"]
        assert log.sent_at is not None


@pytest.mark.anyio
async def test_handle_email_send_failure_propagates_and_records_failed(
    dbsession: AsyncSession,
) -> None:
    """Test handler records FAILED in logs and re-raises exception."""
    from fastapi_plantilla.modules.email.repository import EmailLogRepository

    payload = EmailPayload(
        to=["failing@example.com"],
        subject="Fallo de conexión",
        text="Texto de prueba",
    )
    from fastapi_plantilla.modules.jobs.repository import JobRepository

    job_repo = JobRepository(dbsession)
    job = await job_repo.create("emails.send", payload.model_dump(mode="json"))
    job_id = job.id
    ctx = JobContext(
        job_id=job_id,
        name="emails.send",
        payload=payload,
        entity_type="email",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=AsyncMock(),
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    with patch(
        "fastapi_plantilla.modules.email.jobs.get_email_transport"
    ) as mock_get_trans:
        mock_transport = AsyncMock()
        mock_transport.send.side_effect = ConnectionError("SMTP server timeout")
        mock_get_trans.return_value = mock_transport

        with pytest.raises(ConnectionError, match="SMTP server timeout"):
            await handle_email_send(ctx)

        repo = EmailLogRepository(dbsession)
        log = await repo.get_by_job_id_and_attempt(job_id, attempt=1)
        assert log is not None
        assert log.status == "FAILED"
        assert "SMTP server timeout" in (log.error or "")
