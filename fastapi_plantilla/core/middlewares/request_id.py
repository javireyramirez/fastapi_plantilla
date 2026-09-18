"""Pure ASGI Middleware for unique Request ID injection and propagation."""

from contextvars import ContextVar

import uuid_utils
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Retrieve the current request ID from the context."""
    return request_id_ctx_var.get()


def set_request_id(request_id: str) -> None:
    """Store the request ID in the current context."""
    request_id_ctx_var.set(request_id)


class RequestIDMiddleware:
    """
    Pure ASGI middleware to inject and propagate a unique Request ID.

    Operates without body buffering to ensure full compatibility with
    Server-Sent Events (SSE) and large streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process ASGI HTTP request, generating and attaching X-Request-ID."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        incoming_id: str | None = None
        for k, v in headers:
            if k.lower() == b"x-request-id":
                val = v.decode("latin-1").strip()
                if val:
                    incoming_id = val
                break

        request_id = incoming_id or str(uuid_utils.uuid7())
        set_request_id(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers", [])
                )
                resp_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = resp_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
