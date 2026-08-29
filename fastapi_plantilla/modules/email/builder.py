from typing import Any, Self

from pydantic import BaseModel

from fastapi_plantilla.modules.email.renderer import TemplateRenderer


class EmailPayload(BaseModel):
    """Data transfer object representing an email message."""

    to: list[str]
    subject: str
    html: str | None = None
    text: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    cc: list[str] = []
    bcc: list[str] = []


class EmailBuilder:
    """Fluent builder for constructing email payloads."""

    def __init__(self, renderer: TemplateRenderer) -> None:
        """Initialize builder with template renderer."""
        self.renderer = renderer
        self._to: list[str] = []
        self._subject: str = ""
        self._html: str | None = None
        self._text: str | None = None
        self._from_email: str | None = None
        self._from_name: str | None = None
        self._reply_to: str | None = None
        self._cc: list[str] = []
        self._bcc: list[str] = []

    def to(self, *recipients: str) -> Self:
        """Add recipient email addresses."""
        self._to.extend(recipients)
        return self

    def subject(self, subject: str) -> Self:
        """Set email subject line."""
        self._subject = subject
        return self

    def template(
        self, template_name: str, context: dict[str, Any] | None = None, **kwargs: Any
    ) -> Self:
        """Render and set HTML body from template."""
        merged_context = {**(context or {}), **kwargs}
        self._html = self.renderer.render(
            template_name=template_name, context=merged_context
        )
        return self

    def html(self, html: str) -> Self:
        """Set raw HTML body content."""
        self._html = html
        return self

    def text(self, text: str) -> Self:
        """Set plain text body content."""
        self._text = text
        return self

    def sender(self, email: str, name: str | None = None) -> Self:
        """Set custom sender email and optional name."""
        self._from_email = email
        if name is not None:
            self._from_name = name
        return self

    def reply_to(self, email: str) -> Self:
        """Set reply-to email address."""
        self._reply_to = email
        return self

    def cc(self, *recipients: str) -> Self:
        """Add carbon copy (CC) recipients."""
        self._cc.extend(recipients)
        return self

    def bcc(self, *recipients: str) -> Self:
        """Add blind carbon copy (BCC) recipients."""
        self._bcc.extend(recipients)
        return self

    def build(self) -> EmailPayload:
        """Validate state and construct immutable EmailPayload."""
        if not self._to:
            raise ValueError("At least one recipient is required.")
        if not self._subject.strip():
            raise ValueError("Email subject cannot be empty.")
        if not self._html and not self._text:
            raise ValueError("Email content (HTML or plain text) is required.")

        return EmailPayload(
            to=self._to,
            subject=self._subject,
            html=self._html,
            text=self._text,
            from_email=self._from_email,
            from_name=self._from_name,
            reply_to=self._reply_to,
            cc=self._cc,
            bcc=self._bcc,
        )
