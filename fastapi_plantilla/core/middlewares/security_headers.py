"""Pure ASGI Middleware for OWASP Recommended Security Headers."""

from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_plantilla.core.config import settings

DOCS_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
    }
)


def _build_security_headers(
    existing: set[bytes],
    is_docs: bool,
) -> list[tuple[bytes, bytes]]:
    """Build missing security headers according to current settings."""
    headers: list[tuple[bytes, bytes]] = []

    if b"x-content-type-options" not in existing:
        headers.append(
            (
                b"x-content-type-options",
                settings.security_content_type_options.encode("latin-1"),
            )
        )

    if b"x-frame-options" not in existing:
        headers.append(
            (
                b"x-frame-options",
                settings.security_frame_options.encode("latin-1"),
            )
        )

    # Modern OWASP standard is 0 to disable legacy buggy browser XSS filter
    if b"x-xss-protection" not in existing:
        headers.append((b"x-xss-protection", b"0"))

    if b"referrer-policy" not in existing:
        headers.append(
            (
                b"referrer-policy",
                settings.security_referrer_policy.encode("latin-1"),
            )
        )

    if b"permissions-policy" not in existing:
        headers.append(
            (
                b"permissions-policy",
                settings.security_permissions_policy.encode("latin-1"),
            )
        )

    if (
        b"strict-transport-security" not in existing
        and settings.security_hsts_enabled
        and not settings.is_dev
    ):
        hsts_value = f"max-age={settings.security_hsts_max_age}"
        if settings.security_hsts_include_subdomains:
            hsts_value += "; includeSubDomains"
        headers.append((b"strict-transport-security", hsts_value.encode("latin-1")))

    if b"content-security-policy" not in existing:
        csp = settings.security_csp_policy
        if is_docs:
            csp = (
                "default-src 'self' https: data: 'unsafe-inline' 'unsafe-eval'; "
                "img-src 'self' data: https:; "
                "frame-ancestors 'none';"
            )
        headers.append((b"content-security-policy", csp.encode("latin-1")))

    return headers


class SecurityHeadersMiddleware:
    """
    Pure ASGI middleware injecting OWASP-recommended security headers.

    Operates without body buffering to ensure full compatibility with
    Server-Sent Events (SSE) and large streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Intercept response start message and append security headers."""
        if scope["type"] != "http" or not settings.security_headers_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        clean_path = path.rstrip("/") or "/"
        is_docs = clean_path in DOCS_PATHS

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                msg_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers", [])
                )
                existing = {k.lower() for k, _ in msg_headers}
                new_headers = _build_security_headers(existing, is_docs)
                msg_headers.extend(new_headers)
                message["headers"] = msg_headers

            await send(message)

        await self.app(scope, receive, send_wrapper)
