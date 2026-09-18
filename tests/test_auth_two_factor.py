# ruff: noqa: S105
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.models import User, Verification
from fastapi_plantilla.modules.auth.utils import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_backup_codes,
)


@pytest.mark.anyio
async def test_crypto_totp_encryption_and_backup_codes() -> None:
    """Test AES-GCM encryption/decryption and backup code generation."""
    secret = pyotp.random_base32()
    encrypted = encrypt_totp_secret(secret, settings.auth_secret)
    assert encrypted != secret

    decrypted = decrypt_totp_secret(encrypted, settings.auth_secret)
    assert decrypted == secret

    # Rotating auth_secret prevents decryption of old secrets
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        decrypt_totp_secret(encrypted, "different_secret_key_1234567890")

    # Backup codes test
    plain, hashed = generate_backup_codes(8)
    assert len(plain) == 8
    assert len(hashed) == 8
    for c in plain:
        assert len(c) == 9  # XXXX-XXXX
        assert "-" in c
        # Ambiguous characters are excluded
        for bad_char in ("0", "O", "1", "I", "l"):
            assert bad_char not in c


@pytest.mark.anyio
async def test_two_factor_setup_and_enable(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test 2FA setup, QR generation, validation and enablement."""
    email = f"2fa_user_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Register & login
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA User", "email": email, "password": pwd},
    )
    assert reg_resp.status_code == 200

    # 2. Setup 2FA
    setup_resp = await client.post("/api/auth/two-factor/setup")
    assert setup_resp.status_code == 200
    setup_data = setup_resp.json()
    assert "secret" in setup_data
    assert "otpauth_url" in setup_data
    assert setup_data["qr_code"].startswith("data:image/svg+xml")

    totp_secret = setup_data["secret"]

    # 3. Enable with invalid code fails
    bad_enable = await client.post(
        "/api/auth/two-factor/enable",
        json={"code": "000000"},
    )
    assert bad_enable.status_code == 400

    # 4. Enable with valid code succeeds
    valid_code = pyotp.TOTP(totp_secret).now()
    good_enable = await client.post(
        "/api/auth/two-factor/enable",
        json={"code": valid_code},
    )
    assert good_enable.status_code == 200
    enable_data = good_enable.json()
    assert "backup_codes" in enable_data
    assert len(enable_data["backup_codes"]) == 8

    # 5. Check user in DB
    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.find_first(User.email == email)
    assert user is not None
    assert user.two_factor_enabled is True
    assert user.two_factor_secret is not None
    assert user.two_factor_backup_codes is not None
    assert len(user.two_factor_backup_codes) == 8


@pytest.mark.anyio
async def test_two_factor_login_interception_and_completion(
    client: AsyncClient,
) -> None:
    """Test password login intercept with challenge and 2FA completion."""
    email = f"2fa_login_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Register
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA Login User", "email": email, "password": pwd},
    )

    # 2. Setup & Enable 2FA
    setup_resp = await client.post("/api/auth/two-factor/setup")
    secret = setup_resp.json()["secret"]
    enable_code = pyotp.TOTP(secret).now()
    await client.post("/api/auth/two-factor/enable", json={"code": enable_code})

    # Clear cookies
    client.cookies.clear()

    # 3. Password login intercepts with 2FA challenge
    login_resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["two_factor_required"] is True
    assert login_data["two_factor_token"] is not None
    assert login_data["session"] is None
    # No session cookie was issued
    assert settings.session_cookie_name not in login_resp.cookies

    challenge_token = login_data["two_factor_token"]

    # 4. Attempt login with bad 2FA code fails (401)
    bad_2fa = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": challenge_token, "code": "000000"},
    )
    assert bad_2fa.status_code == 401

    # 5. Complete login with valid TOTP code
    good_code = pyotp.TOTP(secret).now()
    good_2fa = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": challenge_token, "code": good_code},
    )
    assert good_2fa.status_code == 200
    auth_data = good_2fa.json()
    assert auth_data["session"] is not None
    assert settings.session_cookie_name in good_2fa.cookies

    # 6. Challenge token reuse is prevented
    reuse_resp = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": challenge_token, "code": good_code},
    )
    assert reuse_resp.status_code == 400


@pytest.mark.anyio
async def test_two_factor_backup_code_login_and_single_use(
    client: AsyncClient,
) -> None:
    """Test login with backup recovery code and single-use consumption."""
    email = f"2fa_backup_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Register & Enable 2FA
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA Backup User", "email": email, "password": pwd},
    )
    setup_resp = await client.post("/api/auth/two-factor/setup")
    secret = setup_resp.json()["secret"]
    enable_resp = await client.post(
        "/api/auth/two-factor/enable",
        json={"code": pyotp.TOTP(secret).now()},
    )
    backup_codes = enable_resp.json()["backup_codes"]
    used_code = backup_codes[0]

    client.cookies.clear()

    # 2. Login step 1 -> challenge
    login_resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    token1 = login_resp.json()["two_factor_token"]

    # 3. Login step 2 -> with backup code
    backup_resp = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": token1, "code": used_code},
    )
    assert backup_resp.status_code == 200
    assert backup_resp.json()["session"] is not None

    client.cookies.clear()

    # 4. Login step 1 again -> challenge
    login2 = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    token2 = login2.json()["two_factor_token"]

    # 5. Reuse of same backup code must fail
    reuse_backup = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": token2, "code": used_code},
    )
    assert reuse_backup.status_code == 401


@pytest.mark.anyio
async def test_two_factor_magic_link_interception(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test magic link verification intercepts with 2FA when enabled."""
    email = f"2fa_magic_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Register & Enable 2FA
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA Magic User", "email": email, "password": pwd},
    )
    setup = await client.post("/api/auth/two-factor/setup")
    secret = setup.json()["secret"]
    await client.post(
        "/api/auth/two-factor/enable",
        json={"code": pyotp.TOTP(secret).now()},
    )

    client.cookies.clear()

    # 2. Request magic link
    await client.post("/api/auth/sign-in/magic-link", json={"email": email})
    ver_repo = BaseRepository(Verification, dbsession)
    ver = await ver_repo.find_first(
        Verification.identifier == email,
        order_by=Verification.created_at.desc(),
    )
    assert ver is not None

    # 3. Verify magic link -> intercepts with 2FA
    verify_resp = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": ver.value},
    )
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["two_factor_required"] is True
    assert data["two_factor_token"] is not None
    assert data["session"] is None
    assert settings.session_cookie_name not in verify_resp.cookies

    # 4. Complete via 2FA
    totp_code = pyotp.TOTP(secret).now()
    fin_resp = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": data["two_factor_token"], "code": totp_code},
    )
    assert fin_resp.status_code == 200
    assert fin_resp.json()["session"] is not None


@pytest.mark.anyio
async def test_two_factor_disable_and_recovery_codes(
    client: AsyncClient,
) -> None:
    """Test regenerating recovery codes and disabling 2FA."""
    email = f"2fa_disable_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    # 1. Register & Enable 2FA
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA Disable User", "email": email, "password": pwd},
    )
    setup = await client.post("/api/auth/two-factor/setup")
    secret = setup.json()["secret"]
    await client.post(
        "/api/auth/two-factor/enable",
        json={"code": pyotp.TOTP(secret).now()},
    )

    # 2. Regenerate recovery codes
    regen_resp = await client.post(
        "/api/auth/two-factor/recovery-codes",
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert regen_resp.status_code == 200
    new_codes = regen_resp.json()["backup_codes"]
    assert len(new_codes) == 8

    # 3. Disable 2FA with password
    disable_resp = await client.post(
        "/api/auth/two-factor/disable",
        json={"password": pwd},
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json() is True

    client.cookies.clear()

    # 4. Subsequent login does not require 2FA
    direct_login = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    assert direct_login.status_code == 200
    assert direct_login.json()["two_factor_required"] is False
    assert direct_login.json()["session"] is not None


@pytest.mark.anyio
async def test_two_factor_expired_challenge(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Expired 2FA challenge tokens are rejected with 400."""
    email = f"2fa_exp_{uuid.uuid4().hex[:8]}@example.com"
    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Expired 2FA User",
            "email": email,
            "is_active": True,
            "two_factor_enabled": True,
            "two_factor_secret": encrypt_totp_secret(
                pyotp.random_base32(), settings.auth_secret
            ),
        }
    )

    ver_repo = BaseRepository(Verification, dbsession)
    exp_token = "expired_challenge_token"
    await ver_repo.create(
        {
            "id": generate_uuid7(),
            "identifier": f"2fa_challenge:{user.id}",
            "value": exp_token,
            "expires_at": datetime.now(UTC) - timedelta(minutes=1),
        }
    )

    resp = await client.post(
        "/api/auth/sign-in/two-factor",
        json={"two_factor_token": exp_token, "code": "123456"},
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_two_factor_rate_limiting(client: AsyncClient) -> None:
    """Verify rate limiter applies to 2FA verification endpoint."""
    rate_limit_mod = "fastapi_plantilla.core.middlewares.rate_limit.settings"
    with (
        patch(f"{rate_limit_mod}.rate_limit_enabled", True),
        patch(f"{rate_limit_mod}.rate_limit_auth_requests", 2),
        patch(f"{rate_limit_mod}.rate_limit_auth_window_seconds", 60),
    ):
        # 1st attempt
        r1 = await client.post(
            "/api/auth/sign-in/two-factor",
            json={"two_factor_token": "fake_token", "code": "123456"},
        )
        assert r1.status_code == 400

        # 2nd attempt
        r2 = await client.post(
            "/api/auth/sign-in/two-factor",
            json={"two_factor_token": "fake_token", "code": "123456"},
        )
        assert r2.status_code == 400

        # 3rd attempt -> 429
        r3 = await client.post(
            "/api/auth/sign-in/two-factor",
            json={"two_factor_token": "fake_token", "code": "123456"},
        )
        assert r3.status_code == 429
        assert "Demasiadas peticiones" in r3.json()["detail"]


@pytest.mark.anyio
async def test_two_factor_disable_validator(client: AsyncClient) -> None:
    """Disabling 2FA without code or password fails with 422 validation error."""
    email = f"2fa_val_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "SecurePassword123!"

    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "2FA Val User", "email": email, "password": pwd},
    )

    resp = await client.post("/api/auth/two-factor/disable", json={})
    assert resp.status_code == 422
    assert "Debe proporcionar un código 2FA o su contraseña actual" in resp.text


@pytest.mark.anyio
async def test_two_factor_oauth_interception(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """OAuth login intercepts with 2FA challenge when user has 2FA enabled."""
    email = f"2fa_oauth_{uuid.uuid4().hex[:8]}@example.com"
    user_repo = BaseRepository(User, dbsession)
    _user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "OAuth 2FA User",
            "email": email,
            "is_active": True,
            "two_factor_enabled": True,
            "two_factor_secret": encrypt_totp_secret(
                pyotp.random_base32(), settings.auth_secret
            ),
        }
    )

    from fastapi_plantilla.modules.auth.repository import AuthRepository
    from fastapi_plantilla.modules.auth.schema import OAuthUserInfo
    from fastapi_plantilla.modules.auth.service import AuthService

    repo = AuthRepository(dbsession)
    service = AuthService(repository=repo)
    user_info = OAuthUserInfo(
        provider_id="google",
        account_id="google_sub_123456",
        email=email,
        name="OAuth 2FA User",
        email_verified=True,
    )

    auth_resp = await service._handle_oauth_user(user_info=user_info)  # noqa: SLF001
    assert auth_resp.two_factor_required is True
    assert auth_resp.two_factor_token is not None
    assert auth_resp.session is None
