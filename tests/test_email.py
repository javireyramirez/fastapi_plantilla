from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from fastapi_plantilla.core.config import EmailBackend, settings
from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.dependencies import (
    get_email_service,
    get_email_transport,
    get_template_renderer,
)
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import (
    ConsoleTransport,
    MemoryTransport,
    ResendTransport,
    SmtpTransport,
)


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


async def test_console_transport_send() -> None:
    """Test console transport outputting email to logs/stdout without error."""
    transport = ConsoleTransport()
    payload = EmailPayload(
        to=["console@example.com"],
        subject="Console Test",
        text="Hello Console",
        html="<p>Hello Console</p>",
    )
    # Should execute cleanly without raising
    await transport.send(payload)


async def test_memory_transport_send_and_clear() -> None:
    """Test in-memory transport recording delivered messages."""
    transport = MemoryTransport()
    assert transport.outbox == []

    payload1 = EmailPayload(to=["mem1@test.com"], subject="S1", text="T1")
    payload2 = EmailPayload(to=["mem2@test.com"], subject="S2", text="T2")

    await transport.send(payload1)
    await transport.send(payload2)

    assert len(transport.outbox) == 2
    assert transport.outbox[0].to == ["mem1@test.com"]
    assert transport.outbox[1].subject == "S2"

    transport.clear()
    assert transport.outbox == []


async def test_email_service_send_fail_silently_behavior() -> None:
    """Test EmailService error suppression with fail_silently parameter."""
    mock_transport = AsyncMock()
    mock_transport.send.side_effect = RuntimeError("SMTP connection timeout")
    service = EmailService(transport=mock_transport, renderer=TemplateRenderer())
    payload = EmailPayload(to=["err@test.com"], subject="Err", text="Fail test")

    # When fail_silently is True, return False without raising
    success = await service.send(payload, fail_silently=True)
    assert success is False

    # When fail_silently is False (default), re-raise the exception
    with pytest.raises(RuntimeError, match="SMTP connection timeout"):
        await service.send(payload, fail_silently=False)


def test_email_builder_sanitization() -> None:
    """Test builder whitespace stripping and empty recipient filtration."""
    builder = EmailBuilder(renderer=TemplateRenderer())
    payload = (
        builder.to("  user@test.com  ", "", "   ", "second@test.com")
        .subject("  Clean Subject   ")
        .sender("  admin@test.com  ", "  Admin Name  ")
        .reply_to("  reply@test.com  ")
        .cc("  cc@test.com  ", "")
        .bcc("  bcc@test.com  ", "  ")
        .text("Body")
        .build()
    )

    assert payload.to == ["user@test.com", "second@test.com"]
    assert payload.subject == "Clean Subject"
    assert payload.from_email == "admin@test.com"
    assert payload.from_name == "Admin Name"
    assert payload.reply_to == "reply@test.com"
    assert payload.cc == ["cc@test.com"]
    assert payload.bcc == ["bcc@test.com"]


def test_dependencies_resolution_console_and_memory() -> None:
    """Test dependency injection for Console and Memory backends and fallback."""
    get_email_transport.cache_clear()
    with patch.object(settings, "email_backend", EmailBackend.CONSOLE):
        transport_console = get_email_transport()
        assert isinstance(transport_console, ConsoleTransport)

    get_email_transport.cache_clear()
    with patch.object(settings, "email_backend", EmailBackend.MEMORY):
        transport_memory = get_email_transport()
        assert isinstance(transport_memory, MemoryTransport)

    # Fallback to console in dev/test when smtp_host is missing and backend is None
    get_email_transport.cache_clear()
    with (
        patch.object(settings, "email_backend", None),
        patch.object(settings, "smtp_host", None),
        patch.object(settings, "environment", "development"),
    ):
        transport_fallback = get_email_transport()
        assert isinstance(transport_fallback, ConsoleTransport)


def test_email_module_public_facade() -> None:
    """Verify email package exposes complete public facade via __all__."""
    import fastapi_plantilla.modules.email as email_pkg

    expected_exports = {
        "BaseTransport",
        "ConsoleTransport",
        "EmailBuilder",
        "EmailPayload",
        "EmailService",
        "MemoryTransport",
        "ResendTransport",
        "SmtpTransport",
        "TemplateRenderer",
        "get_email_service",
        "get_email_transport",
        "get_template_renderer",
    }
    assert set(email_pkg.__all__) == expected_exports
    for attr in expected_exports:
        assert hasattr(email_pkg, attr)


def test_template_renderer_globals_and_support_email() -> None:
    """Verify TemplateRenderer globals and support_email rendering in base."""
    with (
        patch.object(settings, "app_name", "Acme Platform"),
        patch.object(settings, "support_email", "ayuda@acme.com"),
    ):
        renderer = TemplateRenderer()
        html = renderer.render("base.html")
        assert "Acme Platform" in html
        assert "ayuda@acme.com" in html
        assert str(datetime.now(UTC).year) in html


def test_email_builder_defaults_from_settings() -> None:
    """Verify builder populates from_name and from_email defaults from settings."""
    with (
        patch.object(settings, "app_name", "My App"),
        patch.object(settings, "emails_from_name", None),
        patch.object(settings, "emails_from_email", "noreply@myapp.com"),
    ):
        builder = EmailBuilder(renderer=TemplateRenderer())
        payload = builder.to("test@example.com").subject("Hi").text("Body").build()
        assert payload.from_name == "My App"
        assert payload.from_email == "noreply@myapp.com"


def test_resolve_smtp_port_hierarchy() -> None:
    """Test port resolution for explicit port, SSL, TLS, dev, and fallback."""
    from fastapi_plantilla.modules.email.transports import _resolve_smtp_port

    with patch.object(settings, "smtp_port", 2525):
        assert _resolve_smtp_port() == 2525

    with (
        patch.object(settings, "smtp_port", None),
        patch.object(settings, "smtp_ssl", True),
    ):
        assert _resolve_smtp_port() == 465

    with (
        patch.object(settings, "smtp_port", None),
        patch.object(settings, "smtp_ssl", False),
        patch.object(settings, "smtp_tls", True),
    ):
        assert _resolve_smtp_port() == 587

    with (
        patch.object(settings, "smtp_port", None),
        patch.object(settings, "smtp_ssl", False),
        patch.object(settings, "smtp_tls", False),
        patch.object(settings, "environment", "development"),
    ):
        assert _resolve_smtp_port() == 1025

    with (
        patch.object(settings, "smtp_port", None),
        patch.object(settings, "smtp_ssl", False),
        patch.object(settings, "smtp_tls", False),
        patch.object(settings, "environment", "production"),
    ):
        assert _resolve_smtp_port() == 25


async def test_smtp_transport_raises_without_host_in_production() -> None:
    """Ensure SmtpTransport raises descriptive error when host missing in prod."""
    transport = SmtpTransport()
    payload = EmailPayload(to=["u@t.com"], subject="S", text="T")
    with (
        patch.object(settings, "smtp_host", None),
        patch.object(settings, "environment", "production"),
        pytest.raises(ValueError, match="SMTP host is not configured"),
    ):
        await transport.send(payload)


def test_email_str_validation_raises_error_on_invalid_addresses() -> None:
    """Ensure invalid email format triggers Pydantic ValidationError in builder."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)

    with pytest.raises(ValidationError):
        builder.to("not-a-valid-email").subject("Hello").text("World").build()

    with pytest.raises(ValidationError):
        builder.to("valid@test.com").cc("bad-cc-address").subject("Hi").text(
            "W"
        ).build()


def test_header_sanitization_prevents_crlf_injection() -> None:
    """Ensure carriage return and newline characters are stripped from headers."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)
    payload = (
        builder.to("dest@test.com")
        .subject("Subject Line\r\nBcc: attacker@evil.com\nInjected")
        .sender("admin@test.com", "Admin\r\nName\nInjected")
        .text("Body")
        .build()
    )
    assert "\r" not in payload.subject
    assert "\n" not in payload.subject
    assert payload.subject == "Subject LineBcc: attacker@evil.comInjected"
    assert "\r" not in (payload.from_name or "")
    assert "\n" not in (payload.from_name or "")
    assert payload.from_name == "AdminNameInjected"


def test_email_builder_deduplication() -> None:
    """Verify recipients are deduplicated case-insensitively in order."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)
    payload = (
        builder.to("A@test.com", "b@test.com", "a@TEST.com")
        .cc("C@test.com", "c@test.com", "d@test.com")
        .bcc("E@test.com", "e@TEST.com")
        .subject("Dedup Test")
        .text("Hello")
        .build()
    )
    assert payload.to == ["a@test.com", "b@test.com"]
    assert payload.cc == ["c@test.com", "d@test.com"]
    assert payload.bcc == ["e@test.com"]


def test_template_generates_plain_text_fallback() -> None:
    """Verify builder generates plain text fallback from template when not set."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)
    payload = (
        builder.to("dest@test.com")
        .subject("Restablecer")
        .template("auth/reset_password.html", reset_link="https://app.com/reset")
        .build()
    )
    assert payload.html is not None
    assert payload.text is not None
    assert "Restablecer tu contraseña" in payload.text
    assert "https://app.com/reset" in payload.text
    assert "<h2" not in payload.text
    assert "<table" not in payload.text


def test_raw_html_generates_plain_text_fallback() -> None:
    """Verify raw html() without text() autogenerates plain text fallback in build()."""
    renderer = TemplateRenderer()
    builder = EmailBuilder(renderer=renderer)
    payload = (
        builder.to("dest@test.com")
        .subject("Raw HTML")
        .html("<p>Hello <a href='https://example.com'>World</a></p>")
        .build()
    )
    assert payload.html == "<p>Hello <a href='https://example.com'>World</a></p>"
    assert payload.text is not None
    assert "Hello World (https://example.com)" in payload.text


def test_lifespan_email_configuration_validation() -> None:
    """Verify lifespan validates email settings in production and allows dev."""
    from fastapi_plantilla.core.lifespan import _validate_email_configuration

    # Production with SMTP but missing host
    with (
        patch.object(settings, "environment", "production"),
        patch.object(settings, "email_backend", EmailBackend.SMTP),
        patch.object(settings, "smtp_host", None),
        pytest.raises(ValueError, match="SMTP_HOST"),
    ):
        _validate_email_configuration()

    # Production with Resend but missing key
    with (
        patch.object(settings, "environment", "production"),
        patch.object(settings, "email_backend", EmailBackend.RESEND),
        patch.object(settings, "resend_api_key", None),
        pytest.raises(ValueError, match="RESEND_API_KEY"),
    ):
        _validate_email_configuration()

    # Development passes without host or key
    with (
        patch.object(settings, "environment", "development"),
        patch.object(settings, "email_backend", EmailBackend.SMTP),
        patch.object(settings, "smtp_host", None),
    ):
        _validate_email_configuration()
