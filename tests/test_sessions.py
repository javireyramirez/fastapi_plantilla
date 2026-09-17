import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import ScopeType
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_session,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import (
    Session as AuthSession,
)
from fastapi_plantilla.modules.auth.models import (
    User,
)
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    SessionResponse,
    UserResponse,
)
from fastapi_plantilla.modules.auth.utils import sign_token
from fastapi_plantilla.modules.rbac.catalog import (
    CORE_SYSTEM_MODULES,
    sync_system_modules,
)
from fastapi_plantilla.modules.rbac.models import (
    Role,
    RoleAssignment,
    RolePermission,
    SystemModule,
)
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.sessions.routes import router as sessions_router
from fastapi_plantilla.modules.teams.models import Team, TeamUser


class SessionsTestAuthContext:
    """Helper holder for current authenticated user and session in tests."""

    def __init__(self, user: UserResponse, session_token: str | None = None) -> None:
        self.user = user
        self.session_token = session_token

    @property
    def auth_response(self) -> AuthResponse:
        """Build mock AuthResponse envelope for test execution."""
        session_resp = None
        if self.session_token:
            session_resp = SessionResponse(
                id=generate_uuid7(),
                user_id=self.user.id,
                token=self.session_token,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                is_valid=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        return AuthResponse(user=self.user, session=session_resp)


def _user_to_response(user: User) -> UserResponse:
    """Transform User model to UserResponse."""
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        is_system=user.is_system,
        is_super_admin=user.is_super_admin,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@pytest.fixture
def sessions_auth_context() -> SessionsTestAuthContext:
    """Default placeholder context."""
    dummy_user = UserResponse(
        id=generate_uuid7(),
        name="Admin",
        email="admin@test.com",
        email_verified=True,
        is_active=True,
        is_system=False,
        is_super_admin=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return SessionsTestAuthContext(dummy_user)


@pytest.fixture
def sessions_app(
    dbsession: AsyncSession,
    sessions_auth_context: SessionsTestAuthContext,
) -> FastAPI:
    """FastAPI test app configured with sessions and rbac routers."""
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api")
    app.include_router(rbac_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: sessions_auth_context.user
    app.dependency_overrides[get_current_session] = lambda: (
        sessions_auth_context.auth_response
    )

    return app


@pytest.fixture
async def sessions_client(
    sessions_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Test client."""
    async with AsyncClient(
        transport=ASGITransport(sessions_app), base_url="http://test", timeout=5.0
    ) as ac:
        yield ac


@pytest.mark.anyio
async def test_sessions_module_catalog_definition(dbsession: AsyncSession) -> None:
    """Verify sessions module is properly configured in CORE_SYSTEM_MODULES."""
    session_mod = next(
        (m for m in CORE_SYSTEM_MODULES if m["code"] == "sessions"), None
    )
    assert session_mod is not None
    assert session_mod["category"] == "security"
    assert session_mod["sort_order"] == 3
    assert session_mod["is_active"] is True
    assert set(session_mod["supported_actions"]) == {
        RbacActions.READ,
        RbacActions.DELETE,
        RbacActions.EXPORT,
    }

    # Verify sync_system_modules registers it
    synced = await sync_system_modules(dbsession)
    synced_sessions = next((m for m in synced if m.code == "sessions"), None)
    assert synced_sessions is not None
    assert synced_sessions.name == "Sesiones"
    assert synced_sessions.category == "security"
    assert set(synced_sessions.supported_actions) == {"READ", "DELETE", "EXPORT"}


@pytest.mark.anyio
async def test_list_sessions_paginated_and_filters(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test paginated sessions list with device detection and query filters."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular Jane",
            "email": f"jane_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    # Generate raw token and sign it for the admin
    raw_token_admin = f"raw_admin_{uuid.uuid4().hex}"
    signed_token_admin = sign_token(raw_token_admin, settings.auth_secret)

    session_repo = BaseRepository(AuthSession, dbsession)
    s_admin = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": raw_token_admin,
            "ip_address": "192.168.1.100",
            "user_agent": "Mozilla/5.0 AdminBrowser",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    s_jane_active = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": regular_user.id,
            "token": f"token_jane_{uuid.uuid4().hex}",
            "ip_address": "10.0.0.50",
            "user_agent": "Mobile Safari",
            "expires_at": datetime.now(UTC) + timedelta(days=5),
            "is_valid": True,
        }
    )
    s_jane_revoked = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": regular_user.id,
            "token": f"token_jane_rev_{uuid.uuid4().hex}",
            "ip_address": "10.0.0.51",
            "user_agent": "Old Browser",
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "is_valid": False,
        }
    )

    # Authenticate as admin with signed cookie token
    sessions_auth_context.user = _user_to_response(admin_user)
    sessions_auth_context.session_token = signed_token_admin

    res = await sessions_client.get("/api/sessions?page=1&limit=10")
    assert res.status_code == status.HTTP_200_OK
    body = res.json()
    assert "data" in body
    assert "meta" in body
    assert body["meta"]["page"] == 1
    assert body["meta"]["limit"] == 10
    assert body["meta"]["total"] == 3  # all 3 sessions returned

    # Check is_current flag
    items = body["data"]
    admin_item = next(it for it in items if it["id"] == str(s_admin.id))
    jane_item = next(it for it in items if it["id"] == str(s_jane_active.id))
    assert admin_item["is_current"] is True
    assert jane_item["is_current"] is False

    # 2. Filter is_valid=true: should return only active, non-expired sessions
    res_act = await sessions_client.get("/api/sessions?is_valid=true")
    assert res_act.status_code == status.HTTP_200_OK
    body_act = res_act.json()
    assert body_act["meta"]["total"] == 2

    # 3. Filter is_valid=false: should return the revoked session
    res_rev = await sessions_client.get("/api/sessions?is_valid=false")
    assert res_rev.status_code == status.HTTP_200_OK
    body_rev = res_rev.json()
    assert body_rev["meta"]["total"] == 1
    assert body_rev["data"][0]["id"] == str(s_jane_revoked.id)

    # 4. Filter user_id with is_valid=true
    res_user = await sessions_client.get(
        f"/api/sessions?user_id={regular_user.id}&is_valid=true"
    )
    assert res_user.status_code == status.HTTP_200_OK
    assert res_user.json()["meta"]["total"] == 1
    assert res_user.json()["data"][0]["id"] == str(s_jane_active.id)

    # 5. Filter search
    res_search = await sessions_client.get("/api/sessions?search=AdminBrowser")
    assert res_search.status_code == status.HTTP_200_OK
    assert res_search.json()["meta"]["total"] == 1
    assert res_search.json()["data"][0]["id"] == str(s_admin.id)


@pytest.mark.anyio
async def test_get_session_by_id_and_not_found(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test single session detail and not found behavior."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular Jane",
            "email": f"jane_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    raw_token_admin = f"raw_admin_{uuid.uuid4().hex}"
    session_repo = BaseRepository(AuthSession, dbsession)
    s_admin = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": raw_token_admin,
            "ip_address": "192.168.1.100",
            "user_agent": "Mozilla/5.0 AdminBrowser",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    s_jane_active = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": regular_user.id,
            "token": f"token_jane_{uuid.uuid4().hex}",
            "ip_address": "10.0.0.50",
            "user_agent": "Mobile Safari",
            "expires_at": datetime.now(UTC) + timedelta(days=5),
            "is_valid": True,
        }
    )

    sessions_auth_context.user = _user_to_response(admin_user)
    sessions_auth_context.session_token = sign_token(
        raw_token_admin, settings.auth_secret
    )

    res_single = await sessions_client.get(f"/api/sessions/{s_admin.id}")
    assert res_single.status_code == status.HTTP_200_OK
    single_data = res_single.json()
    assert single_data["id"] == str(s_admin.id)
    assert single_data["is_current"] is True
    assert single_data["user_email"] == admin_user.email

    res_single_jane = await sessions_client.get(f"/api/sessions/{s_jane_active.id}")
    assert res_single_jane.status_code == status.HTTP_200_OK
    assert res_single_jane.json()["is_current"] is False

    res_not_found = await sessions_client.get(f"/api/sessions/{uuid.uuid4()}")
    assert res_not_found.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_revoke_session_single_and_audit(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test revoking a single session and verifying audit log generation."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    target_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Target User",
            "email": f"target_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    raw_admin = f"raw_adm_{uuid.uuid4().hex}"
    session_repo = BaseRepository(AuthSession, dbsession)
    admin_sess = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": raw_admin,
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    target_sess = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": target_user.id,
            "token": f"target_tok_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )

    sessions_auth_context.user = _user_to_response(admin_user)
    sessions_auth_context.session_token = sign_token(raw_admin, settings.auth_secret)

    # 1. Revoke target session
    del_res = await sessions_client.delete(f"/api/sessions/{target_sess.id}")
    assert del_res.status_code == status.HTTP_200_OK
    assert del_res.json()["message"] == "Sesión revocada exitosamente"
    assert del_res.json()["detail"] is None

    # Check database: target session is now invalid
    refreshed_target = await session_repo.get_by_id(target_sess.id)
    assert refreshed_target is not None
    assert refreshed_target.is_valid is False

    # Check audit log entry
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_type == "session",
        AuditLog.entity_id == target_sess.id,
        AuditLog.action == "REVOKE",
    )
    audit_res = await dbsession.execute(audit_stmt)
    log_entry = audit_res.scalars().first()
    assert log_entry is not None
    assert log_entry.actor_id == admin_user.id
    assert log_entry.changes == {"is_valid": {"old": True, "new": False}}

    # 2. Revoking an already revoked session returns message
    del_again = await sessions_client.delete(f"/api/sessions/{target_sess.id}")
    assert del_again.status_code == status.HTTP_200_OK
    assert "ya se encontraba revocada" in del_again.json()["message"]

    # 3. Auto-revocation: admin revoking current session gets warning detail
    del_self = await sessions_client.delete(f"/api/sessions/{admin_sess.id}")
    assert del_self.status_code == status.HTTP_200_OK
    assert "propia sesión actual" in (del_self.json()["detail"] or "")


@pytest.mark.anyio
async def test_bulk_revoke_sessions(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test bulk revoking multiple sessions with aggregated audit log."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )

    session_repo = BaseRepository(AuthSession, dbsession)
    s1 = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": f"bulk_tok_1_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    s2 = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": f"bulk_tok_2_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    non_existent_id = generate_uuid7()

    sessions_auth_context.user = _user_to_response(admin_user)

    bulk_res = await sessions_client.post(
        "/api/sessions/bulk/revoke",
        json={"ids": [str(s1.id), str(s2.id), str(non_existent_id)]},
    )
    assert bulk_res.status_code == status.HTTP_200_OK
    body = bulk_res.json()
    assert body["count"] == 2
    assert str(non_existent_id) in body["unprocessed_ids"]

    # Verify both are invalid
    refreshed_s1 = await session_repo.get_by_id(s1.id)
    assert refreshed_s1 is not None
    assert refreshed_s1.is_valid is False
    refreshed_s2 = await session_repo.get_by_id(s2.id)
    assert refreshed_s2 is not None
    assert refreshed_s2.is_valid is False

    # Verify aggregated audit log
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_type == "session",
        AuditLog.action == "BULK_REVOKE",
    )
    audit_res = await dbsession.execute(audit_stmt)
    bulk_log = audit_res.scalars().first()
    assert bulk_log is not None
    assert bulk_log.changes is not None
    assert bulk_log.changes["count"] == 2
    assert str(s1.id) in bulk_log.changes["revoked_session_ids"]
    assert str(s2.id) in bulk_log.changes["revoked_session_ids"]


@pytest.mark.anyio
async def test_sessions_rbac_scopes_team_and_own(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test RBAC scopes enforcement (TEAM and OWN)."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    team_repo = BaseRepository(Team, dbsession)
    team_user_repo = BaseRepository(TeamUser, dbsession)
    role_repo = BaseRepository(Role, dbsession)
    perm_repo = BaseRepository(RolePermission, dbsession)
    session_repo = BaseRepository(AuthSession, dbsession)

    # 1. Create Team A
    team_a = await team_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Team Alpha",
            "slug": f"alpha_{uuid.uuid4().hex[:6]}",
        }
    )

    # 2. Create Users: Member 1 (caller), Member 2 (same team), Outsider (no team)
    m1 = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Member One",
            "email": f"m1_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    m2 = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Member Two",
            "email": f"m2_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    outsider = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Outsider",
            "email": f"out_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    # Link m1 and m2 to Team A
    await team_user_repo.create(
        {"id": generate_uuid7(), "team_id": team_a.id, "user_id": m1.id}
    )
    await team_user_repo.create(
        {"id": generate_uuid7(), "team_id": team_a.id, "user_id": m2.id}
    )

    # Create active sessions
    sess_m1 = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": m1.id,
            "token": f"tok_m1_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    sess_m2 = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": m2.id,
            "token": f"tok_m2_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )
    sess_out = await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": outsider.id,
            "token": f"tok_out_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )

    # Query sessions module ID
    mod_stmt = select(SystemModule).where(SystemModule.code == "sessions")
    mod_sess = (await dbsession.execute(mod_stmt)).scalar_one()

    # Create Team Manager role with TEAM scope on sessions
    role_team = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Team Manager",
            "slug": f"tm_{uuid.uuid4().hex[:6]}",
        }
    )
    await perm_repo.create(
        {
            "id": generate_uuid7(),
            "role_id": role_team.id,
            "module_id": mod_sess.id,
            "action": RbacActions.READ,
            "scope": ScopeType.TEAM,
        }
    )
    await perm_repo.create(
        {
            "id": generate_uuid7(),
            "role_id": role_team.id,
            "module_id": mod_sess.id,
            "action": RbacActions.DELETE,
            "scope": ScopeType.TEAM,
        }
    )
    role_assignment_repo = BaseRepository(RoleAssignment, dbsession)
    await role_assignment_repo.create(
        {
            "id": generate_uuid7(),
            "role_id": role_team.id,
            "entity_type": "USER",
            "entity_id": m1.id,
        }
    )

    id_m1 = str(sess_m1.id)
    id_m2 = str(sess_m2.id)
    id_out = str(sess_out.id)

    # Authenticate as m1 (Team scope)
    sessions_auth_context.user = _user_to_response(m1)
    dbsession.expire_all()

    # List sessions: should see m1 and m2, but NOT outsider
    list_res = await sessions_client.get("/api/sessions")
    assert list_res.status_code == status.HTTP_200_OK
    found_ids = {it["id"] for it in list_res.json()["data"]}
    assert id_m1 in found_ids
    assert id_m2 in found_ids
    assert id_out not in found_ids

    # Attempt to revoke outsider session: should fail with 403 Forbidden
    del_out = await sessions_client.delete(f"/api/sessions/{id_out}")
    assert del_out.status_code == status.HTTP_403_FORBIDDEN

    # Attempt to revoke teammate session: succeeds
    del_m2 = await sessions_client.delete(f"/api/sessions/{id_m2}")
    assert del_m2.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_export_sessions(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test exporting sessions in CSV format."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    admin_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    session_repo = BaseRepository(AuthSession, dbsession)
    await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": admin_user.id,
            "token": f"exp_tok_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=2),
            "is_valid": True,
        }
    )

    sessions_auth_context.user = _user_to_response(admin_user)

    exp_res = await sessions_client.post(
        "/api/sessions/export",
        json={"format": "csv"},
    )
    assert exp_res.status_code == status.HTTP_200_OK
    assert "text/csv" in exp_res.headers.get("content-type", "")
    assert "attachment; filename=" in exp_res.headers.get("content-disposition", "")
    assert int(exp_res.headers.get("X-Total-Count", 0)) >= 1
    assert "Super Admin" in exp_res.text


@pytest.mark.anyio
async def test_sessions_unauthorized_when_missing_permission(
    dbsession: AsyncSession,
    sessions_client: AsyncClient,
    sessions_auth_context: SessionsTestAuthContext,
) -> None:
    """Test that a user without permissions is denied access."""
    await sync_system_modules(dbsession)

    user_repo = BaseRepository(User, dbsession)
    regular_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "No Perm User",
            "email": f"noperm_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    sessions_auth_context.user = _user_to_response(regular_user)

    # GET /api/sessions -> 403 Forbidden
    res = await sessions_client.get("/api/sessions")
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # DELETE /api/sessions/{id} -> 403 Forbidden
    del_res = await sessions_client.delete(f"/api/sessions/{generate_uuid7()}")
    assert del_res.status_code == status.HTTP_403_FORBIDDEN
