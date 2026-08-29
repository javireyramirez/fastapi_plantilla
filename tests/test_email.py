from unittest.mock import AsyncMock, patch

import pytest

from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.dependencies import (
    get_email_service,
    get_email_transport,
    get_template_renderer,
)
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import ResendTransport, SmtpTransport
from fastapi_plantilla.settings import EmailBackend, settings


def test_template_renderer_renders_html_with_context() -> None:
    """Test template rendering with variable interpolation and base inheritance."""
    renderer = TemplateRenderer()
    html = renderer.render(
        "auth/reset_password.html",
        {"name": "Carlos", "reset_link": "https://example.com/reset?token=123"},
    )
    assert "Carlos" in html
    assert "https://example.com/reset?token=123" in html
    assert "FastAPI Plantilla" in html
    assert "<!DOCTYPE html>" in html


def test_template_renderer_handles_empty_context() -> None:
    """Test rendering template with None or empty context."""
    renderer = TemplateRenderer()
    html = renderer.render("base.html")
    assert "<!DOCTYPE html>" in html
    assert "FastAPI Plantilla" in html


def test_email_builder_fluent_chain() -> None:
    """Test building complete EmailPayload using fluent builder chaining."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)

    payload = (
        builder.to("user1@test.com", "user2@test.com")
        .subject("Welcome Subject")
        .text("Plain text body")
        .sender("admin@test.com", "Admin Name")
        .reply_to("support@test.com")
        .cc("cc1@test.com", "cc2@test.com")
        .bcc("bcc@test.com")
        .build()
    )

    assert payload.to == ["user1@test.com", "user2@test.com"]
    assert payload.subject == "Welcome Subject"
    assert payload.text == "Plain text body"
    assert payload.from_email == "admin@test.com"
    assert payload.from_name == "Admin Name"
    assert payload.reply_to == "support@test.com"
    assert payload.cc == ["cc1@test.com", "cc2@test.com"]
    assert payload.bcc == ["bcc@test.com"]


def test_email_builder_with_template() -> None:
    """Test builder rendering Jinja2 template into HTML payload."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)

    payload = (
        builder.to("verify@test.com")
        .subject("Confirm Account")
        .template(
            "auth/verify_email.html",
            name="Laura",
            verify_link="https://example.com/verify?token=abc",
        )
        .build()
    )

    assert payload.html is not None
    assert "Laura" in payload.html
    assert "https://example.com/verify?token=abc" in payload.html


def test_email_builder_validations() -> None:
    """Test validation errors for missing required fields in builder."""
    renderer = TemplateRenderer()

    # Missing recipients
    with pytest.raises(ValueError, match="At least one recipient"):
        EmailBuilder(renderer).subject("Subject").text("Body").build()

    # Missing subject
    with pytest.raises(ValueError, match="subject cannot be empty"):
        EmailBuilder(renderer).to("test@test.com").subject("   ").text("Body").build()

    # Missing content (no HTML and no text)
    with pytest.raises(ValueError, match=r"content.*required"):
        EmailBuilder(renderer).to("test@test.com").subject("Subject").build()


async def test_smtp_transport_send_success() -> None:
    """Test SMTP transport constructing and dispatching EmailMessage."""
    payload = EmailPayload(
        to=["recipient@test.com"],
        subject="SMTP Test Subject",
        html="<p>HTML Content</p>",
        text="Text Content",
        reply_to="reply@test.com",
        cc=["cc@test.com"],
        bcc=["bcc@test.com"],
    )

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        transport = SmtpTransport()
        await transport.send(payload)

        mock_send.assert_awaited_once()
        sent_msg = mock_send.call_args[0][0]
        assert sent_msg["Subject"] == "SMTP Test Subject"
        assert sent_msg["To"] == "recipient@test.com"
        assert sent_msg["Reply-To"] == "reply@test.com"
        assert sent_msg["Cc"] == "cc@test.com"
        assert sent_msg["Bcc"] == "bcc@test.com"


async def test_resend_transport_send_success() -> None:
    """Test Resend transport mapping payload to API parameters."""
    payload = EmailPayload(
        to=["resend@test.com"],
        subject="Resend Test Subject",
        html="<p>Resend HTML</p>",
        text="Resend Text",
    )

    with (
        patch.object(settings, "resend_api_key", "re_test_dummy_key_123"),
        patch("resend.Emails.send_async", new_callable=AsyncMock) as mock_resend,
    ):
        transport = ResendTransport()
        await transport.send(payload)

        mock_resend.assert_awaited_once()
        params = mock_resend.call_args[0][0]
        assert params["to"] == ["resend@test.com"]
        assert params["subject"] == "Resend Test Subject"
        assert params["html"] == "<p>Resend HTML</p>"
        assert params["text"] == "Resend Text"


async def test_resend_transport_missing_api_key() -> None:
    """Test Resend transport raising error when API key is not configured."""
    payload = EmailPayload(to=["test@test.com"], subject="Subj", text="Body")

    with patch.object(settings, "resend_api_key", None):
        transport = ResendTransport()
        with pytest.raises(ValueError, match="Resend API key is not configured"):
            await transport.send(payload)


async def test_email_service_delegation() -> None:
    """Test EmailService delegating to transport with payload and builder."""
    mock_transport = AsyncMock()
    renderer = TemplateRenderer()
    service = EmailService(transport=mock_transport, renderer=renderer)

    # Test 1: Send via EmailPayload
    payload = EmailPayload(to=["p@test.com"], subject="Direct Payload", text="Hello")
    await service.send(payload)
    mock_transport.send.assert_awaited_once_with(payload=payload)

    # Test 2: Send directly via EmailBuilder (lazy execution)
    mock_transport.reset_mock()
    builder = (
        service.create_builder().to("b@test.com").subject("From Builder").text("Hi")
    )
    await service.send(builder)

    mock_transport.send.assert_awaited_once()
    sent_payload = mock_transport.send.call_args[1]["payload"]
    assert sent_payload.to == ["b@test.com"]
    assert sent_payload.subject == "From Builder"


def test_dependencies_resolution() -> None:
    """Test dependency injection providers and singleton caching."""
    renderer1 = get_template_renderer()
    renderer2 = get_template_renderer()
    assert renderer1 is renderer2

    get_email_transport.cache_clear()
    with patch.object(settings, "email_backend", EmailBackend.SMTP):
        transport_smtp = get_email_transport()
        assert isinstance(transport_smtp, SmtpTransport)

    get_email_transport.cache_clear()
    with patch.object(settings, "email_backend", EmailBackend.RESEND):
        transport_resend = get_email_transport()
        assert isinstance(transport_resend, ResendTransport)

    service = get_email_service(renderer=renderer1, transport=transport_smtp)
    assert isinstance(service, EmailService)
    assert service.renderer is renderer1
    assert service.transport is transport_smtp
