import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_session,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import Session as AuthSession
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.routes import router as auth_router
from fastapi_plantilla.modules.auth.schema import AuthResponse, UserResponse


class ImpersonateAuthContext:
    """Helper holder for current authenticated user and session."""

    def __init__(self, user: UserResponse, token: str | None = None) -> None:
        self.user = user
        self.token = token


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
async def imp_users(
    dbsession: AsyncSession,
) -> tuple[UserResponse, UserResponse, UserResponse, UserResponse]:
    """Create admin, another admin, normal user, and inactive user."""
    repo = BaseRepository(User, dbsession)
    admin = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin 1",
            "email": f"admin1_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    admin2 = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin 2",
            "email": f"admin2_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular User",
            "email": f"user_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    inactive = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Inactive User",
            "email": f"inactive_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": False,
        }
    )
    return (
        _user_to_response(admin),
        _user_to_response(admin2),
        _user_to_response(regular),
        _user_to_response(inactive),
    )


@pytest.fixture
def imp_auth(
    imp_users: tuple[UserResponse, UserResponse, UserResponse, UserResponse],
) -> ImpersonateAuthContext:
    """Provide mutable auth context initialized to superadmin."""
    admin, _, _, _ = imp_users
    return ImpersonateAuthContext(admin)


@pytest.fixture
def imp_app(dbsession: AsyncSession, imp_auth: ImpersonateAuthContext) -> FastAPI:
    """Configure test FastAPI app with auth router and dependencies."""
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: imp_auth.user

    def _superuser_check() -> UserResponse:
        if not imp_auth.user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SuperAdmin required",
            )
        return imp_auth.user

    app.dependency_overrides[get_current_active_superuser] = _superuser_check
    return app


@pytest.fixture
async def imp_client(
    imp_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Provide AsyncClient for impersonation tests."""
    async with AsyncClient(
        transport=ASGITransport(imp_app), base_url="http://test"
    ) as ac:
        yield ac


# =========================================================================
# Impersonation Tests
# =========================================================================


@pytest.mark.anyio
async def test_superadmin_impersonate_flow(
    imp_app: FastAPI,
    imp_client: AsyncClient,
    imp_auth: ImpersonateAuthContext,
    imp_users: tuple[UserResponse, UserResponse, UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify SuperAdmin can impersonate regular user, audits session, and exits."""
    admin, _, regular, _ = imp_users

    # 1. Successful impersonation of regular user
    imp_auth.user = admin
    res = await imp_client.post(f"/api/auth/impersonate/{regular.id}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["user"]["id"] == str(regular.id)
    assert data["session"]["impersonated_by"] == str(admin.id)

    # Verify session in DB has impersonated_by set
    sess_repo = BaseRepository(AuthSession, dbsession)
    db_session = await sess_repo.find_first(AuthSession.user_id == regular.id)
    assert db_session is not None
    assert db_session.impersonated_by == admin.id
    assert db_session.is_valid is True

    # 2. Exit impersonation
    # Mock session dependency with the impersonated session
    impersonated_auth = AuthResponse.model_validate(data)
    imp_app.dependency_overrides[get_current_session] = lambda: impersonated_auth

    exit_res = await imp_client.post("/api/auth/impersonate/exit")
    assert exit_res.status_code == status.HTTP_200_OK

    # Verify session invalidated
    await dbsession.refresh(db_session)
    assert db_session.is_valid is False


@pytest.mark.anyio
async def test_impersonation_security_boundaries(
    imp_client: AsyncClient,
    imp_auth: ImpersonateAuthContext,
    imp_users: tuple[UserResponse, UserResponse, UserResponse, UserResponse],
) -> None:
    """Verify security constraints: non-admin 403, cannot impersonate self or admin."""
    admin, admin2, regular, inactive = imp_users

    # 1. Regular user cannot impersonate
    imp_auth.user = regular
    denied_res = await imp_client.post(f"/api/auth/impersonate/{admin.id}")
    assert denied_res.status_code == status.HTTP_403_FORBIDDEN

    # 2. Admin cannot impersonate self
    imp_auth.user = admin
    self_res = await imp_client.post(f"/api/auth/impersonate/{admin.id}")
    assert self_res.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Admin cannot impersonate another SuperAdmin
    super_res = await imp_client.post(f"/api/auth/impersonate/{admin2.id}")
    assert super_res.status_code == status.HTTP_400_BAD_REQUEST

    # 4. Admin cannot impersonate inactive user
    inactive_res = await imp_client.post(f"/api/auth/impersonate/{inactive.id}")
    assert inactive_res.status_code == status.HTTP_400_BAD_REQUEST

    # 5. Nonexistent user returns 404
    fake_id = uuid.uuid4()
    not_found_res = await imp_client.post(f"/api/auth/impersonate/{fake_id}")
    assert not_found_res.status_code == status.HTTP_404_NOT_FOUND
