from contextvars import ContextVar

import uuid_utils
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Retrieve the current request ID from the context."""
    return request_id_ctx_var.get()


def set_request_id(request_id: str) -> None:
    """Store the request ID in the current context."""
    request_id_ctx_var.set(request_id)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to inject and propagate a unique Request ID for each HTTP request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request, generating and attaching a unique Request ID."""
        request_id = request.headers.get("X-Request-ID")
        if not request_id or not request_id.strip():
            request_id = str(uuid_utils.uuid7())
        else:
            request_id = request_id.strip()

        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
