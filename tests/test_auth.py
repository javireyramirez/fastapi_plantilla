# ruff: noqa: S105
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.mixins import RecordStatus, generate_uuid7
from fastapi_plantilla.modules.auth.models import Account, User
from fastapi_plantilla.modules.auth.schema import MIN_PASSWORD_LENGTH
from fastapi_plantilla.modules.auth.service import ph


@pytest.mark.anyio
async def test_min_password_length_constant() -> None:
    """Verify minimum password length constant is properly defined as 8."""
    assert MIN_PASSWORD_LENGTH == 8


@pytest.mark.anyio
async def test_register_and_login_flow(client: AsyncClient) -> None:
    """Test standard registration and login flow via email/password."""
    unique_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    valid_password = "SecurePassword123!"

    # 1. Register
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={
            "name": "Test User",
            "email": unique_email,
            "password": valid_password,
        },
    )
    assert reg_resp.status_code == 200
    reg_data = reg_resp.json()
    assert reg_data["user"]["email"] == unique_email
    assert reg_data["user"]["name"] == "Test User"
    assert reg_data["session"]["token"] is not None

    # 2. Duplicate registration fails
    dup_resp = await client.post(
        "/api/auth/sign-up/email",
        json={
            "name": "Duplicate User",
            "email": unique_email,
            "password": valid_password,
        },
    )
    assert dup_resp.status_code == 400

    # 3. Weak password fails validation
    weak_resp = await client.post(
        "/api/auth/sign-up/email",
        json={
            "name": "Weak User",
            "email": f"weak_{uuid.uuid4().hex[:8]}@example.com",
            "password": "short",
        },
    )
    assert weak_resp.status_code == 422

    # 4. Login with correct credentials
    login_resp = await client.post(
        "/api/auth/sign-in/email",
        json={
            "email": unique_email,
            "password": valid_password,
        },
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    token = login_data["session"]["token"]

    # 5. Get current session
    sess_resp = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sess_resp.status_code == 200
    assert sess_resp.json()["user"]["email"] == unique_email

    # 6. Logout
    logout_resp = await client.post(
        "/api/auth/sign-out",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200

    # 7. Session is now invalid
    invalid_resp = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert invalid_resp.status_code == 401


@pytest.mark.anyio
async def test_login_invalid_credentials(client: AsyncClient) -> None:
    """Test login rejection for unknown email or wrong password."""
    # Unknown email
    resp = await client.post(
        "/api/auth/sign-in/email",
        json={
            "email": "nonexistent@example.com",
            "password": "Password123!",
        },
    )
    assert resp.status_code == 401

    # Existing user, wrong password
    email = f"wrong_pwd_{uuid.uuid4().hex[:8]}@example.com"
    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "User", "email": email, "password": "Password123!"},
    )
    wrong_resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": "WrongPassword123!"},
    )
    assert wrong_resp.status_code == 401


@pytest.mark.anyio
async def test_login_inactive_user_rejected(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Inactive users must be rejected upon login and session validation."""
    email = f"inactive_{uuid.uuid4().hex[:8]}@example.com"
    password = "Password123!"

    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Inactive User",
            "email": email,
            "is_active": False,
        }
    )
    acct_repo = BaseRepository(Account, dbsession)
    await acct_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": user.id,
            "provider_id": "credential",
            "account_id": email,
            "password": ph.hash(password),
        }
    )

    resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 403
    assert "inactivo" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_login_trashed_user_rejected(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Soft-deleted (trashed) users must NOT be allowed to login."""
    email = f"trashed_{uuid.uuid4().hex[:8]}@example.com"
    password = "Password123!"

    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Trashed User",
            "email": email,
            "is_active": True,
            "status": RecordStatus.TRASHED,
        }
    )
    acct_repo = BaseRepository(Account, dbsession)
    await acct_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": user.id,
            "provider_id": "credential",
            "account_id": email,
            "password": ph.hash(password),
        }
    )

    resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 403
    assert "inactivo o suspendido" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_change_password_flow(client: AsyncClient) -> None:
    """Test password update, checking same password rejection and session revocation."""
    email = f"chg_pwd_{uuid.uuid4().hex[:8]}@example.com"
    old_pwd = "OldPassword123!"
    new_pwd = "NewPassword123!"

    # Register
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Change Pwd User", "email": email, "password": old_pwd},
    )
    token1 = reg_resp.json()["session"]["token"]

    # Rejects same password
    same_resp = await client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "current_password": old_pwd,
            "new_password": old_pwd,
            "revoke_other_sessions": False,
        },
    )
    assert same_resp.status_code == 400

    # Rejects wrong current password
    wrong_curr = await client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "current_password": "WrongOldPassword123!",
            "new_password": new_pwd,
            "revoke_other_sessions": False,
        },
    )
    assert wrong_curr.status_code == 400

    # Successfully changes password
    success_resp = await client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "current_password": old_pwd,
            "new_password": new_pwd,
            "revoke_other_sessions": False,
        },
    )
    assert success_resp.status_code == 200
    assert success_resp.json() is True

    # Login with old password fails
    old_login = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": old_pwd},
    )
    assert old_login.status_code == 401

    # Login with new password succeeds
    new_login = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": new_pwd},
    )
    assert new_login.status_code == 200


@pytest.mark.anyio
async def test_session_management_endpoints(client: AsyncClient) -> None:
    """Test listing sessions, revoking specific session, and revoking all sessions."""
    email = f"sess_mgr_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "Password123!"

    reg = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Sess Mgr", "email": email, "password": pwd},
    )
    token1 = reg.json()["session"]["token"]

    # Login a second device
    login2 = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    token2 = login2.json()["session"]["token"]
    session2_id = login2.json()["session"]["id"]

    # List sessions from device 1
    list_resp = await client.get(
        "/api/auth/list-sessions",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert len(sessions) >= 2

    current_count = sum(1 for s in sessions if s["is_current"])
    assert current_count == 1

    # Revoke session 2 by ID
    rev_resp = await client.post(
        "/api/auth/revoke-session",
        headers={"Authorization": f"Bearer {token1}"},
        json={"session_id": session2_id},
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json() is True

    # Device 2 session is now invalid
    sess2_check = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert sess2_check.status_code == 401

    # Device 1 session still valid
    sess1_check = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert sess1_check.status_code == 200

    # Revoke all sessions
    rev_all = await client.post(
        "/api/auth/revoke-sessions",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert rev_all.status_code == 200

    # Device 1 is now invalid
    sess1_after = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert sess1_after.status_code == 401


@pytest.mark.anyio
async def test_password_reset_flow(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test forgot password, reset password, and invalid token rejection."""
    email = f"reset_test_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "OldPassword123!"
    new_pwd = "ResetPassword123!"

    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Reset Test", "email": email, "password": pwd},
    )

    # Request reset link
    forgot_resp = await client.post(
        "/api/auth/forget-password",
        json={"email": email},
    )
    assert forgot_resp.status_code == 200

    # Query the generated verification token from DB
    from fastapi_plantilla.modules.auth.models import Verification

    ver_repo = BaseRepository(Verification, dbsession)
    ver_record = await ver_repo.find_first(
        Verification.identifier == email,
        order_by=Verification.created_at.desc(),
    )
    assert ver_record is not None
    token = ver_record.value

    # Reset with invalid token fails
    bad_reset = await client.post(
        "/api/auth/reset-password",
        json={"token": "invalid_fake_token", "new_password": new_pwd},
    )
    assert bad_reset.status_code == 400

    # Reset with valid token succeeds
    good_reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": new_pwd},
    )
    assert good_reset.status_code == 200
    assert good_reset.json() is True

    # Token is consumed (one-time use)
    reuse_reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": new_pwd},
    )
    assert reuse_reset.status_code == 400

    # Login with new password
    login_new = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": new_pwd},
    )
    assert login_new.status_code == 200


@pytest.mark.anyio
async def test_delete_user_guards(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test deletion guards for system user and last active superadmin."""
    user_repo = BaseRepository(User, dbsession)
    acct_repo = BaseRepository(Account, dbsession)

    pwd = "Password123!"

    # 1. System user protection
    sys_email = f"sys_{uuid.uuid4().hex[:8]}@example.com"
    sys_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "System User",
            "email": sys_email,
            "is_system": True,
            "is_active": True,
        }
    )
    await acct_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": sys_user.id,
            "provider_id": "credential",
            "account_id": sys_email,
            "password": ph.hash(pwd),
        }
    )
    login_sys = await client.post(
        "/api/auth/sign-in/email",
        json={"email": sys_email, "password": pwd},
    )
    sys_token = login_sys.json()["session"]["token"]

    del_sys = await client.post(
        "/api/auth/delete-user",
        headers={"Authorization": f"Bearer {sys_token}"},
        json={"password": pwd},
    )
    assert del_sys.status_code == 403
    assert "sistema" in del_sys.json()["detail"].lower()

    # 2. Last superadmin protection
    admin_email = f"sole_admin_{uuid.uuid4().hex[:8]}@example.com"
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Sole Admin",
            "email": admin_email,
            "is_super_admin": True,
            "is_active": True,
        }
    )
    await acct_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "provider_id": "credential",
            "account_id": admin_email,
            "password": ph.hash(pwd),
        }
    )
    login_admin = await client.post(
        "/api/auth/sign-in/email",
        json={"email": admin_email, "password": pwd},
    )
    admin_token = login_admin.json()["session"]["token"]

    del_admin = await client.post(
        "/api/auth/delete-user",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": pwd},
    )
    assert del_admin.status_code == 400
    assert "superadministrador" in del_admin.json()["detail"].lower()


@pytest.mark.anyio
async def test_oauth_open_redirect_rejected(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth callback_url must reject external/unsafe URLs to prevent open redirect."""
    from fastapi_plantilla.core.config import settings

    monkeypatch.setattr(settings, "google_client_id", "mock-google-client-id")

    # Disallowed external domains
    resp_evil = await client.get(
        "/api/auth/sign-in/social/google?callback_url=https://evil.com/steal-session",
        follow_redirects=False,
    )
    assert resp_evil.status_code == 400
    assert "no permitida" in resp_evil.json()["detail"]

    # Disallowed protocol-relative URLs
    resp_proto = await client.get(
        "/api/auth/sign-in/social/google?callback_url=//evil.com",
        follow_redirects=False,
    )
    assert resp_proto.status_code == 400

    # Allowed relative paths
    resp_safe = await client.get(
        "/api/auth/sign-in/social/google?callback_url=/dashboard",
        follow_redirects=False,
    )
    assert resp_safe.status_code == 307
    assert "oauth_callback_url" in resp_safe.cookies


@pytest.mark.anyio
async def test_verification_tokens_purged_on_repeat_request(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Requesting a reset token multiple times must purge previous tokens."""
    from fastapi_plantilla.modules.auth.models import Verification

    email = f"purge_test_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "Password123!"

    await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Purge Test", "email": email, "password": pwd},
    )

    # First reset request
    await client.post("/api/auth/forget-password", json={"email": email})
    # Second reset request
    await client.post("/api/auth/forget-password", json={"email": email})

    ver_repo = BaseRepository(Verification, dbsession)
    tokens = await ver_repo.find_many(Verification.identifier == email, limit=10)
    # Exactly one token should exist in database
    assert len(tokens) == 1


@pytest.mark.anyio
async def test_delete_user_soft_deletes_to_trash(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Self-deletion must soft-delete to TRASH and invalidate sessions."""
    email = f"delete_me_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "Password123!"

    reg = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Delete Me", "email": email, "password": pwd},
    )
    user_id = uuid.UUID(reg.json()["user"]["id"])
    token = reg.json()["session"]["token"]

    # Delete account
    del_resp = await client.post(
        "/api/auth/delete-user",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": pwd},
    )
    assert del_resp.status_code == 200
    assert del_resp.json() is True

    # Check DB record is TRASHED with deleted_by and deleted_at populated
    user_repo = BaseRepository(User, dbsession)
    user_db = await user_repo.get_by_id(user_id)
    assert user_db is not None
    assert user_db.status == RecordStatus.TRASHED
    assert user_db.is_active is False
    assert user_db.deleted_at is not None
    assert user_db.deleted_by == email

    # Active session invalidated
    sess_check = await client.get(
        "/api/auth/get-session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sess_check.status_code == 401

    # Cannot log in again
    login_attempt = await client.post(
        "/api/auth/sign-in/email",
        json={"email": email, "password": pwd},
    )
    assert login_attempt.status_code == 403

    # Verify TrashItem created in sys_trash_bin
    from fastapi_plantilla.modules.trash.repository import TrashRepository

    trash_repo = TrashRepository(dbsession)
    trash_item = await trash_repo.get_by_entity("user", user_id)
    assert trash_item is not None
    assert trash_item.entity_id == user_id
    assert trash_item.entity_type == "user"


@pytest.mark.anyio
async def test_auth_audit_events_emitted(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Auth lifecycle actions must emit corresponding audit log entries."""
    from fastapi_plantilla.modules.audit.models import AuditLog

    email = f"audit_auth_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "Password123!"
    new_pwd = "NewPassword123!"

    # 1. Register (emits CREATE + LOGIN)
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Audit User", "email": email, "password": pwd},
    )
    token = reg_resp.json()["session"]["token"]
    user_id = uuid.UUID(reg_resp.json()["user"]["id"])

    # 2. Change password (emits PASSWORD_CHANGE)
    await client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": pwd,
            "new_password": new_pwd,
            "revoke_other_sessions": False,
        },
    )

    # 3. Logout (emits LOGOUT)
    await client.post(
        "/api/auth/sign-out",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Verify audit logs in database
    audit_repo = BaseRepository(AuditLog, dbsession)
    logs = await audit_repo.find_many(AuditLog.entity_id == user_id, limit=50)
    actions = [log.action for log in logs]

    assert "CREATE" in actions
    assert "LOGIN" in actions
    assert "PASSWORD_CHANGE" in actions
    assert "LOGOUT" in actions


@pytest.mark.anyio
async def test_login_failed_audit_events_emitted(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Failed logins must emit LOGIN_FAILED for brute-force tracking."""
    from fastapi_plantilla.modules.audit.models import AuditLog

    unknown_email = f"unknown_{uuid.uuid4().hex[:8]}@example.com"
    audit_repo = BaseRepository(AuditLog, dbsession)

    # 1. Non-existent user login attempt
    bad_user_resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": unknown_email, "password": "Password123!"},
    )
    assert bad_user_resp.status_code == 401

    logs_unknown = await audit_repo.find_many(
        AuditLog.action == "LOGIN_FAILED",
        AuditLog.entity_name == unknown_email,
        limit=10,
    )
    assert len(logs_unknown) == 1
    assert logs_unknown[0].entity_id is None
    assert logs_unknown[0].entity_name == unknown_email

    # 2. Existing user, wrong password attempt
    valid_email = f"user_pwd_{uuid.uuid4().hex[:8]}@example.com"
    correct_pwd = "CorrectPassword123!"
    reg_resp = await client.post(
        "/api/auth/sign-up/email",
        json={"name": "Pwd User", "email": valid_email, "password": correct_pwd},
    )
    user_id = uuid.UUID(reg_resp.json()["user"]["id"])

    wrong_pwd_resp = await client.post(
        "/api/auth/sign-in/email",
        json={"email": valid_email, "password": "WrongPassword123!"},
    )
    assert wrong_pwd_resp.status_code == 401

    logs_wrong_pwd = await audit_repo.find_many(
        AuditLog.action == "LOGIN_FAILED",
        AuditLog.entity_id == user_id,
        limit=10,
    )
    assert len(logs_wrong_pwd) == 1
    assert logs_wrong_pwd[0].entity_id == user_id
    assert "invalid password" in (logs_wrong_pwd[0].details or "")
