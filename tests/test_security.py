"""Tests for Phase 8.1: Security Headers, Rate Limiting and Proxy Hardening."""

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.middlewares import (
    get_client_ip,
    reset_rate_limiters,
)
from fastapi_plantilla.core.middlewares.rate_limit import (
    get_effective_bool,
    get_effective_limit,
)
from fastapi_plantilla.modules.settings.service import SystemSettingService


@pytest.fixture(autouse=True)
def _cleanup_rate_limits() -> Generator[None, None, None]:
    """Reset rate limiters and enable rate limiting for security test suite."""
    reset_rate_limiters()
    SystemSettingService.invalidate_cache()
    with patch.object(settings, "rate_limit_enabled", True):
        yield
    reset_rate_limiters()
    SystemSettingService.invalidate_cache()


# --------------------------------------------------------------------------
# 1. Security Headers Tests
# --------------------------------------------------------------------------


async def test_security_headers_present_on_api_responses(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify OWASP-recommended security headers are injected into HTTP responses."""
    url = fastapi_app.url_path_for("live_probe")
    response = await client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-xss-protection") == "0"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers.get("permissions-policy", "")
    assert "default-src" in response.headers.get("content-security-policy", "")


async def test_security_headers_docs_csp_compatibility(
    client: AsyncClient,
) -> None:
    """Verify CSP policy on docs allows Swagger UI inline scripts and styles."""
    response = await client.get("/api/docs")
    assert response.status_code == status.HTTP_200_OK
    csp = response.headers.get("content-security-policy", "")
    assert "'unsafe-inline'" in csp
    assert "'unsafe-eval'" in csp


async def test_security_hsts_header_in_production(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify Strict-Transport-Security is injected when running in production."""
    url = fastapi_app.url_path_for("live_probe")
    with patch.object(settings, "environment", "production"):
        response = await client.get(url)
        assert response.status_code == status.HTTP_200_OK
        hsts = response.headers.get("strict-transport-security", "")
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts


async def test_security_headers_disabled(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify headers are not injected when security_headers_enabled is False."""
    url = fastapi_app.url_path_for("live_probe")
    with patch.object(settings, "security_headers_enabled", False):
        response = await client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert "x-frame-options" not in response.headers


# --------------------------------------------------------------------------
# 2. Client IP Extraction & Anti-Spoofing Tests
# --------------------------------------------------------------------------


def test_get_client_ip_direct_without_proxy() -> None:
    """Verify direct peer IP is returned when not coming through a proxy."""
    scope: dict[str, Any] = {
        "client": ("192.168.1.50", 54321),
        "headers": [],
    }
    ip = get_client_ip(scope, trusted_proxies=["127.0.0.1", "::1"])
    assert ip == "192.168.1.50"


def test_get_client_ip_trusted_proxy_parses_xff() -> None:
    """Verify X-Forwarded-For is parsed safely when peer is a trusted proxy."""
    scope: dict[str, Any] = {
        "client": ("127.0.0.1", 8080),
        "headers": [
            (b"x-forwarded-for", b"203.0.113.195, 10.0.0.1"),
        ],
    }
    ip = get_client_ip(scope, trusted_proxies=["127.0.0.1", "::1"])
    assert ip == "203.0.113.195"


def test_get_client_ip_untrusted_proxy_ignores_xff() -> None:
    """Verify spoofed X-Forwarded-For header is ignored from untrusted client."""
    scope: dict[str, Any] = {
        "client": ("198.51.100.5", 54321),
        "headers": [
            (b"x-forwarded-for", b"1.1.1.1"),
        ],
    }
    ip = get_client_ip(scope, trusted_proxies=["127.0.0.1", "::1"])
    # Untrusted client cannot spoof IP
    assert ip == "198.51.100.5"


# --------------------------------------------------------------------------
# 3. SSOT Hierarchy Resolver Tests
# --------------------------------------------------------------------------


def test_get_effective_limit_hierarchy() -> None:
    """Verify sys_settings in-memory cache overrides config defaults."""
    default_val = 120
    key = "security.rate_limit_global_requests"

    # 1. Fallback to default when cache is empty
    assert get_effective_limit(key, default_val) == default_val

    # 2. In-memory cache overrides config default
    import time

    SystemSettingService.cache[key] = (250, time.monotonic())
    assert get_effective_limit(key, default_val) == 250

    # 3. Boolean resolver
    bool_key = "security.rate_limit_enabled"
    SystemSettingService.cache[bool_key] = (False, time.monotonic())
    assert get_effective_bool(bool_key, True) is False


# --------------------------------------------------------------------------
# 4. Global Rate Limiting Tests & Short-Circuit 429
# --------------------------------------------------------------------------


async def test_global_rate_limit_headers_on_success(
    client: AsyncClient,
) -> None:
    """Verify standard rate limit headers on allowed requests."""
    response = await client.get("/api/settings/export-formats")
    assert response.status_code == status.HTTP_200_OK
    assert "x-ratelimit-limit" in response.headers
    assert "x-ratelimit-remaining" in response.headers
    assert "x-ratelimit-reset" in response.headers


async def test_global_rate_limit_short_circuit_with_security_headers(
    client: AsyncClient,
) -> None:
    """Verify 429 response includes X-Request-ID, security headers, and Retry-After."""
    with (
        patch.object(settings, "rate_limit_global_requests", 2),
        patch.object(settings, "rate_limit_global_window_seconds", 60),
    ):
        # 1st request -> OK
        r1 = await client.get("/api/settings/export-formats")
        assert r1.status_code == status.HTTP_200_OK

        # 2nd request -> OK
        r2 = await client.get("/api/settings/export-formats")
        assert r2.status_code == status.HTTP_200_OK

        # 3rd request -> 429 Too Many Requests
        r3 = await client.get("/api/settings/export-formats")
        assert r3.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        data = r3.json()
        assert "detail" in data
        assert "retry_after" in data

        # Crucial Correction #3: 429 must include X-Request-ID and Security Headers!
        assert "x-request-id" in r3.headers
        assert r3.headers.get("x-content-type-options") == "nosniff"
        assert r3.headers.get("x-frame-options") == "DENY"
        assert r3.headers.get("x-xss-protection") == "0"
        assert "retry-after" in r3.headers
        assert r3.headers.get("x-ratelimit-remaining") == "0"


async def test_global_rate_limit_excluded_paths(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify healthcheck and metrics paths are exempt from global rate limiting."""
    url = fastapi_app.url_path_for("live_probe")
    with patch.object(settings, "rate_limit_global_requests", 1):
        for _ in range(5):
            res = await client.get(url)
            assert res.status_code == status.HTTP_200_OK


# --------------------------------------------------------------------------
# 5. Route-Level Sensitive Auth Rate Limiting Tests
# --------------------------------------------------------------------------


async def test_auth_signin_rate_limiting(client: AsyncClient) -> None:
    """Verify per-IP rate limiting on /api/auth/sign-in/email."""
    with (
        patch.object(settings, "rate_limit_auth_requests", 3),
        patch.object(settings, "rate_limit_auth_window_seconds", 60),
    ):
        payload = {
            "email": "ratelimit_test@example.com",
            "password": "WrongPassword123!",
        }

        for _ in range(3):
            res = await client.post("/api/auth/sign-in/email", json=payload)
            # Should fail auth credentials (401), but NOT rate limit (429)
            assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # 4th attempt -> 429 Too Many Requests
        res_blocked = await client.post("/api/auth/sign-in/email", json=payload)
        assert res_blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Retry-After" in res_blocked.headers
        assert res_blocked.headers.get("X-RateLimit-Remaining") == "0"


async def test_auth_forget_password_rate_limiting(client: AsyncClient) -> None:
    """Verify per-IP rate limiting on /api/auth/forget-password."""
    with (
        patch.object(settings, "rate_limit_auth_requests", 2),
        patch.object(settings, "rate_limit_auth_window_seconds", 60),
    ):
        payload = {"email": "user@example.com"}

        # Attempt 1 -> 200
        r1 = await client.post("/api/auth/forget-password", json=payload)
        assert r1.status_code == status.HTTP_200_OK

        # Attempt 2 -> 200
        r2 = await client.post("/api/auth/forget-password", json=payload)
        assert r2.status_code == status.HTTP_200_OK

        # Attempt 3 -> 429 Too Many Requests
        r3 = await client.post("/api/auth/forget-password", json=payload)
        assert r3.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Retry-After" in r3.headers
