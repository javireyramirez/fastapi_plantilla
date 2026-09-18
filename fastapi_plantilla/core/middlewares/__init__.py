from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.middlewares.rate_limit import (
    RateLimiter,
    RateLimitMiddleware,
    get_client_ip,
    reset_rate_limiters,
)
from fastapi_plantilla.core.middlewares.request_id import (
    RequestIDMiddleware,
    get_request_id,
    set_request_id,
)
from fastapi_plantilla.core.middlewares.security_headers import (
    SecurityHeadersMiddleware,
)

__all__ = [
    "RateLimitMiddleware",
    "RateLimiter",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "get_client_ip",
    "get_request_id",
    "reset_rate_limiters",
    "set_request_id",
    "setup_middlewares",
]


def setup_middlewares(app: FastAPI) -> None:
    """
    Register all application middlewares in proper execution order.

    Note on Starlette's add_middleware:
    Starlette inserts each middleware at index 0 and wraps in reversed order.
    Therefore, the LAST middleware added is the OUTERMOST in the execution stack.

    Execution order on incoming request:
    1. RequestIDMiddleware (outermost: assigns & propagates X-Request-ID to
       all responses, including 429)
    2. SecurityHeadersMiddleware (injects OWASP security headers into all
       responses, including 429)
    3. CORSMiddleware (handles CORS headers and preflights)
    4. RateLimitMiddleware (innermost: enforces rate limiting; when short-circuiting
       429, response passes through SecurityHeaders and RequestID on output)
    """
    app.add_middleware(RateLimitMiddleware)

    origins = list(settings.cors_origins)
    if settings.frontend_url and settings.frontend_url not in origins:
        origins.append(settings.frontend_url)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)
