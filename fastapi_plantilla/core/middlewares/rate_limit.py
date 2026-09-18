"""In-Memory Sliding Window Rate Limiting and Global Rate Limit Middleware.

Note on In-Memory Limiting with Multi-Worker Architecture:
This rate limiter maintains sliding window queues in process memory.
When running with multi-worker Gunicorn/Uvicorn, each worker process tracks its
own queue. Therefore, the effective global limit is approximately
(limit * workers_count). This conforms strictly to the Ponytail philosophy
(zero Redis, zero external dependencies).
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import ClassVar, Final

from fastapi import HTTPException, Request, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.settings.service import SystemSettingService

EXCLUDED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/metrics",
        "/api/health/live",
        "/api/health/ready",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
    }
)


def get_client_ip(scope: Scope, trusted_proxies: list[str]) -> str:
    """
    Safely extract client IP address respecting trusted reverse proxies.

    Prevents IP spoofing: X-Forwarded-For is only parsed if the direct peer
    (client.host) is explicitly listed in trusted_proxies.
    """
    client = scope.get("client")
    direct_ip = client[0] if client else "127.0.0.1"

    if direct_ip in trusted_proxies:
        # Check X-Forwarded-For header
        headers = scope.get("headers", [])
        for k, v in headers:
            if k.lower() == b"x-forwarded-for":
                raw_xff = v.decode("latin-1")
                parts = [ip.strip() for ip in raw_xff.split(",") if ip.strip()]
                if parts:
                    return parts[0]
                break

    return direct_ip


def get_effective_limit(key: str, default: int) -> int:
    """
    SSOT hierarchy resolver: sys_settings in-memory cache takes precedence.

    Zero-SQL hot-path: evaluates process memory cache without DB roundtrips.
    Falls back to config.py defaults when cache is cold or expired until
    SystemSettingService loads it.
    """
    if key in SystemSettingService.cache:
        val, cached_at = SystemSettingService.cache[key]
        if (time.monotonic() - cached_at) < SystemSettingService.ttl_seconds:
            try:
                return int(val)
            except (ValueError, TypeError):
                return default
    return default


def get_effective_bool(key: str, default: bool) -> bool:
    """SSOT boolean setting resolver with in-memory cache precedence."""
    if key in SystemSettingService.cache:
        val, cached_at = SystemSettingService.cache[key]
        if (time.monotonic() - cached_at) < SystemSettingService.ttl_seconds:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
    return default


class SlidingWindowLimiter:
    """
    High-performance in-memory sliding window rate limiter.

    Uses collections.deque for O(1) timestamp additions and removals,
    with lazy per-request pruning and periodic cleanup to prevent memory leaks.
    """

    cleanup_interval_seconds: ClassVar[float] = 300.0

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

    async def check(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int, int]:
        """
        Check whether request is allowed under the sliding window.

        :return: Tuple of (is_allowed, remaining, reset_seconds, retry_after)
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            # 1. Lazy cleanup of current key's expired timestamps
            timestamps = self._windows[key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            # 2. Check limit
            count = len(timestamps)
            if count < max_requests:
                timestamps.append(now)
                remaining = max_requests - count - 1
                oldest = timestamps[0]
                reset_seconds = max(1, int(oldest + window_seconds - now))
                retry_after = 0
                is_allowed = True
            else:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now))
                reset_seconds = retry_after
                remaining = 0
                is_allowed = False

            # 3. Periodic cleanup of completely stale keys
            if (now - self._last_cleanup) > self.cleanup_interval_seconds:
                self._purge_stale_keys(now)
                self._last_cleanup = now

            return is_allowed, remaining, reset_seconds, retry_after

    def _purge_stale_keys(self, now: float) -> None:
        """Evict keys that have no timestamps within the last hour."""
        cutoff = now - 3600.0
        stale = [k for k, deq in self._windows.items() if not deq or deq[-1] < cutoff]
        for k in stale:
            del self._windows[k]

    def reset(self) -> None:
        """Reset all rate limiting windows. Used in test suites."""
        self._windows.clear()
        self._last_cleanup = time.monotonic()


# Global in-memory limiters
global_rate_limiter = SlidingWindowLimiter()
auth_rate_limiter = SlidingWindowLimiter()


def reset_rate_limiters() -> None:
    """Reset all active rate limiters. Ensures isolated test executions."""
    global_rate_limiter.reset()
    auth_rate_limiter.reset()


class RateLimitMiddleware:
    """
    Pure ASGI middleware enforcing global request rate limits per client IP.

    Preserves SSE / streaming responses without buffering.
    Injects RFC-compliant rate limit response headers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process HTTP request and apply global IP rate limits."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_enabled = get_effective_bool(
            "security.rate_limit_enabled", settings.rate_limit_enabled
        )
        if not is_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        clean_path = path.rstrip("/") or "/"
        if clean_path in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        client_ip = get_client_ip(scope, settings.trusted_proxies)
        key = f"global:{client_ip}"

        max_req = get_effective_limit(
            "security.rate_limit_global_requests",
            settings.rate_limit_global_requests,
        )
        window_sec = get_effective_limit(
            "security.rate_limit_global_window_seconds",
            settings.rate_limit_global_window_seconds,
        )

        allowed, remaining, reset_sec, retry_after = await global_rate_limiter.check(
            key, max_req, window_sec
        )

        if not allowed:
            body = (
                f'{{"detail":"Demasiadas peticiones. Por favor, espere '
                f'{retry_after} segundos antes de reintentar.",'
                f'"retry_after":{retry_after}}}'
            ).encode()
            response_headers: list[tuple[bytes, bytes]] = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"retry-after", str(retry_after).encode("latin-1")),
                (b"x-ratelimit-limit", str(max_req).encode("latin-1")),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", str(reset_sec).encode("latin-1")),
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": response_headers,
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                existing_names = {k.lower() for k, _ in headers}
                if b"x-ratelimit-limit" not in existing_names:
                    headers.append(
                        (b"x-ratelimit-limit", str(max_req).encode("latin-1"))
                    )
                    headers.append(
                        (b"x-ratelimit-remaining", str(remaining).encode("latin-1"))
                    )
                    headers.append(
                        (b"x-ratelimit-reset", str(reset_sec).encode("latin-1"))
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RateLimiter:
    """
    FastAPI dependency for granular per-IP rate limiting on sensitive routes.

    Example:
        @router.post("/sign-in/email", dependencies=[Depends(RateLimiter(5, 60))])
    """

    def __init__(
        self,
        times: int | None = None,
        seconds: int | None = None,
        scope_prefix: str = "auth",
    ) -> None:
        self.times = times
        self.seconds = seconds
        self.scope_prefix = scope_prefix

    async def __call__(self, request: Request) -> None:
        """Evaluate route-specific rate limit against client IP."""
        is_enabled = get_effective_bool(
            "security.rate_limit_enabled", settings.rate_limit_enabled
        )
        if not is_enabled:
            return

        max_req = (
            self.times
            if self.times is not None
            else get_effective_limit(
                "security.rate_limit_auth_requests",
                settings.rate_limit_auth_requests,
            )
        )
        window_sec = (
            self.seconds
            if self.seconds is not None
            else get_effective_limit(
                "security.rate_limit_auth_window_seconds",
                settings.rate_limit_auth_window_seconds,
            )
        )

        client_ip = get_client_ip(request.scope, settings.trusted_proxies)
        key = f"{self.scope_prefix}:{request.url.path}:{client_ip}"

        allowed, _remaining, reset_sec, retry_after = await auth_rate_limiter.check(
            key, max_req, window_sec
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Demasiadas peticiones. Por favor, espere "
                    f"{retry_after} segundos antes de reintentar."
                ),
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_req),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_sec),
                },
            )
