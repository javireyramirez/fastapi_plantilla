# ruff: noqa: S105
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.mixins import RecordStatus, generate_uuid7
from fastapi_plantilla.modules.auth.models import User, Verification
from fastapi_plantilla.modules.auth.utils import is_safe_callback_url


@pytest.mark.anyio
async def test_is_safe_callback_url() -> None:
    """Test safe callback URL validation against open redirect attacks."""
    assert is_safe_callback_url("/dashboard") is True
    assert is_safe_callback_url("/account/settings?tab=security") is True
    assert is_safe_callback_url("//attacker.com") is False
    assert is_safe_callback_url("//attacker.com/path") is False
    assert is_safe_callback_url("https://attacker.com/steal") is False
    assert is_safe_callback_url("javascript:alert(1)") is False
    assert is_safe_callback_url("") is False
    assert is_safe_callback_url(None) is False

    # Matches frontend_url origin
    with patch.object(settings, "frontend_url", "http://localhost:3000"):
        assert is_safe_callback_url("http://localhost:3000/auth/callback") is True
        assert is_safe_callback_url("http://localhost:3000") is True
        assert is_safe_callback_url("http://localhost:3001/auth/callback") is False
        assert is_safe_callback_url("https://localhost:3000/auth/callback") is False


@pytest.mark.anyio
async def test_magic_link_flow_happy_path(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test full magic link authentication lifecycle."""
    unique_email = f"magic_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Create a user (initially email_verified=False)
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={
            "name": "Magic User",
            "email": unique_email,
            "password": pwd,
        },
    )
    assert reg_resp.status_code == 200
    user_data = reg_resp.json()["user"]
    assert user_data["email_verified"] is False

    # 2. Request magic link
    req_resp = await client.post(
        "/api/auth/sign-in/magic-link",
        json={"email": unique_email, "callback_url": "/dashboard"},
    )
    assert req_resp.status_code == 200
    assert req_resp.json() is True

    # 3. Retrieve token from database
    ver_repo = BaseRepository(Verification, dbsession)
    ver = await ver_repo.find_first(
        Verification.identifier == unique_email,
        order_by=Verification.created_at.desc(),
    )
    assert ver is not None
    token = ver.value
    assert len(token) > 20

    # 4. Verify magic link
    verify_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": token},
    )
    assert verify_resp.status_code == 200
    auth_data = verify_resp.json()
    assert auth_data["user"]["email"] == unique_email
    assert auth_data["user"]["email_verified"] is True
    assert auth_data["session"]["token"] is not None

    # Verify session cookie was set
    assert settings.session_cookie_name in verify_resp.cookies

    # 5. Token is consumed (single-use)
    reuse_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": token},
    )
    assert reuse_resp.status_code == 400
    assert "inválido o ha expirado" in reuse_resp.json()["detail"]


@pytest.mark.anyio
async def test_magic_link_anti_enumeration(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Non-existent email returns 200 True without creating token."""
    non_existent_email = f"ghost_{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/auth/sign-in/magic-link",
        json={"email": non_existent_email},
    )
    assert resp.status_code == 200
    assert resp.json() is True

    ver_repo = BaseRepository(Verification, dbsession)
    ver = await ver_repo.find_first(Verification.identifier == non_existent_email)
    assert ver is None


@pytest.mark.anyio
async def test_magic_link_invalidates_previous_tokens(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Multiple requests invalidate prior tokens so only the latest is valid."""
    email = f"multi_{uuid.uuid4().hex[:8]}@example.com"
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Multi User", "email": email, "password": "Password123!"},
    )

    # First request
    await client.post("/api/auth/sign-in/magic-link", json={"email": email})
    ver_repo = BaseRepository(Verification, dbsession)
    ver1 = await ver_repo.find_first(Verification.identifier == email)
    assert ver1 is not None
    token1 = ver1.value

    # Second request
    await client.post("/api/auth/sign-in/magic-link", json={"email": email})
    dbsession.expire_all()
    ver2 = await ver_repo.find_first(Verification.identifier == email)
    assert ver2 is not None
    token2 = ver2.value

    assert token1 != token2

    # Token 1 must fail
    fail_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": token1},
    )
    assert fail_resp.status_code == 400

    # Token 2 must succeed
    success_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": token2},
    )
    assert success_resp.status_code == 200


@pytest.mark.anyio
async def test_magic_link_expired_token(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Expired magic link tokens are rejected with 400."""
    email = f"expired_{uuid.uuid4().hex[:8]}@example.com"
    user_repo = BaseRepository(User, dbsession)
    await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Expired User",
            "email": email,
            "is_active": True,
        }
    )

    ver_repo = BaseRepository(Verification, dbsession)
    expired_token = "expired_token_12345"
    await ver_repo.create(
        {
            "id": generate_uuid7(),
            "identifier": email,
            "value": expired_token,
            "expires_at": datetime.now(UTC) - timedelta(minutes=5),
        }
    )

    resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": expired_token},
    )
    assert resp.status_code == 400
    assert "inválido o ha expirado" in resp.json()["detail"]


@pytest.mark.anyio
async def test_magic_link_inactive_and_trashed_user(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Inactive or trashed users cannot log in via magic link."""
    # 1. Inactive user request -> safe 200, but no token generated
    inactive_email = f"inactive_{uuid.uuid4().hex[:8]}@example.com"
    user_repo = BaseRepository(User, dbsession)
    await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Inactive User",
            "email": inactive_email,
            "is_active": False,
        }
    )

    req_resp = await client.post(
        "/api/auth/sign-in/magic-link",
        json={"email": inactive_email},
    )
    assert req_resp.status_code == 200
    assert req_resp.json() is True

    ver_repo = BaseRepository(Verification, dbsession)
    ver = await ver_repo.find_first(Verification.identifier == inactive_email)
    assert ver is None

    # 2. If token exists and user is marked TRASHED, verify returns 403
    trashed_email = f"trashed_{uuid.uuid4().hex[:8]}@example.com"
    await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Trashed User",
            "email": trashed_email,
            "is_active": True,
            "status": RecordStatus.TRASHED,
        }
    )
    valid_token = "token_for_trashed_user"
    await ver_repo.create(
        {
            "id": generate_uuid7(),
            "identifier": trashed_email,
            "value": valid_token,
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        }
    )

    verify_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": valid_token},
    )
    assert verify_resp.status_code == 403
    assert "inactivo o suspendido" in verify_resp.json()["detail"]


@pytest.mark.anyio
async def test_magic_link_rate_limiting(client: AsyncClient) -> None:
    """Verify rate limiter applies to magic link endpoints."""
    rate_limit_mod = "fastapi_plantilla.core.middlewares.rate_limit.settings"
    with (
        patch(f"{rate_limit_mod}.rate_limit_enabled", True),
        patch(f"{rate_limit_mod}.rate_limit_auth_requests", 2),
        patch(f"{rate_limit_mod}.rate_limit_auth_window_seconds", 60),
    ):
        email = "ratelimit_magic@example.com"

        # 1st attempt: OK
        r1 = await client.post("/api/auth/sign-in/magic-link", json={"email": email})
        assert r1.status_code == 200

        # 2nd attempt: OK
        r2 = await client.post("/api/auth/sign-in/magic-link", json={"email": email})
        assert r2.status_code == 200

        # 3rd attempt: 429
        r3 = await client.post("/api/auth/sign-in/magic-link", json={"email": email})
        assert r3.status_code == 429
        assert "Demasiadas peticiones" in r3.json()["detail"]
