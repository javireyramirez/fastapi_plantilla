import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import Session as AuthSession
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.rbac.models import Role, SystemModule
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.users.routes import router as users_router


class UsersAdminAuthContext:
    """Helper holder for current authenticated user in User Admin tests."""

    def __init__(self, user: UserResponse) -> None:
        self.user = user


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
async def admin_and_targets(
    dbsession: AsyncSession,
) -> tuple[UserResponse, User, User]:
    """Create superadmin and two target test users."""
    repo = BaseRepository(User, dbsession)
    admin = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    target1 = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Target One",
            "email": f"target1_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    target2 = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Target Two",
            "email": f"target2_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    # Add active sessions for target users
    session_repo = BaseRepository(AuthSession, dbsession)
    await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": target1.id,
            "token": f"token1_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "is_valid": True,
        }
    )
    await session_repo.create(
        {
            "id": generate_uuid7(),
            "user_id": target2.id,
            "token": f"token2_{uuid.uuid4().hex}",
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "is_valid": True,
        }
    )

    return _user_to_response(admin), target1, target2


@pytest.fixture
def users_admin_auth(
    admin_and_targets: tuple[UserResponse, User, User],
) -> UsersAdminAuthContext:
    """Provide mutable auth context initialized to super admin."""
    admin, _, _ = admin_and_targets
    return UsersAdminAuthContext(admin)


@pytest.fixture
def users_admin_app(
    dbsession: AsyncSession, users_admin_auth: UsersAdminAuthContext
) -> FastAPI:
    """Configure test FastAPI application with Users and RBAC routers."""
    app = FastAPI()
    app.include_router(users_router, prefix="/api")
    app.include_router(rbac_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: users_admin_auth.user

    def _superuser_check() -> UserResponse:
        if not users_admin_auth.user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SuperAdmin required",
            )
        return users_admin_auth.user

    mock_email = MagicMock()
    mock_email.send = AsyncMock()
    mock_builder = MagicMock()
    mock_builder.to.return_value = mock_builder
    mock_builder.subject.return_value = mock_builder
    mock_builder.template.return_value = "msg_payload"
    mock_email.create_builder.return_value = mock_builder

    app.dependency_overrides[get_current_active_superuser] = _superuser_check
    app.dependency_overrides[get_email_service] = lambda: mock_email
    return app


@pytest.fixture
async def users_client(
    users_admin_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Provide AsyncClient for user administration endpoints."""
    async with AsyncClient(
        transport=ASGITransport(users_admin_app), base_url="http://test"
    ) as ac:
        yield ac


# =========================================================================
# Users Administration Tests
# =========================================================================


@pytest.mark.anyio
async def test_users_admin_crud_and_listing(
    users_client: AsyncClient,
    admin_and_targets: tuple[UserResponse, User, User],
    dbsession: AsyncSession,
) -> None:
    """Verify listing with filters, admin user creation, and metadata updating."""
    _, target1, _ = admin_and_targets

    # Ensure users module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "users",
            "name": "Users Module",
            "is_active": True,
        }
    )

    # List users
    list_res = await users_client.get("/api/users?page=1&limit=10")
    assert list_res.status_code == status.HTTP_200_OK
    data = list_res.json()
    assert len(data["data"]) >= 3
    assert data["meta"]["total"] >= 3

    # Search filter
    search_res = await users_client.get("/api/users?search=Target+One")
    assert search_res.status_code == status.HTTP_200_OK
    assert len(search_res.json()["data"]) == 1

    # Create new user administratively
    create_res = await users_client.post(
        "/api/users",
        json={
            "name": "Created Admin User",
            "email": f"created_{uuid.uuid4().hex[:6]}@example.com",
            "password": "SecurePassword123!",
            "is_active": True,
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    new_user = create_res.json()
    assert new_user["name"] == "Created Admin User"

    # Update user
    patch_res = await users_client.patch(
        f"/api/users/{target1.id}",
        json={"name": "Target One Updated"},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert patch_res.json()["name"] == "Target One Updated"


@pytest.mark.anyio
async def test_single_and_bulk_suspension_with_session_invalidation(
    users_client: AsyncClient,
    admin_and_targets: tuple[UserResponse, User, User],
    dbsession: AsyncSession,
) -> None:
    """Verify single and bulk suspension deactivates users and terminates sessions."""
    _, target1, target2 = admin_and_targets
    session_repo = BaseRepository(AuthSession, dbsession)

    # 1. Single user suspension
    suspend_res = await users_client.post(f"/api/users/{target1.id}/suspend")
    assert suspend_res.status_code == status.HTTP_200_OK

    # Check user is inactive
    get_res = await users_client.get(f"/api/users/{target1.id}")
    assert get_res.json()["is_active"] is False

    # Check session was invalidated
    sess1 = await session_repo.find_first(AuthSession.user_id == target1.id)
    assert sess1 is not None
    assert sess1.is_valid is False

    # Reactivate user
    reactivate_res = await users_client.post(f"/api/users/{target1.id}/reactivate")
    assert reactivate_res.status_code == status.HTTP_200_OK
    get_reactivated = await users_client.get(f"/api/users/{target1.id}")
    assert get_reactivated.json()["is_active"] is True

    # 2. Bulk suspension
    bulk_res = await users_client.post(
        "/api/users/bulk/suspend",
        json={"user_ids": [str(target1.id), str(target2.id)]},
    )
    assert bulk_res.status_code == status.HTTP_200_OK
    assert bulk_res.json()["count"] == 2

    # Check sessions of target2 invalidated
    sess2 = await session_repo.find_first(AuthSession.user_id == target2.id)
    assert sess2 is not None
    assert sess2.is_valid is False

    # Bulk reactivate
    bulk_re_res = await users_client.post(
        "/api/users/bulk/reactivate",
        json={"user_ids": [str(target1.id), str(target2.id)]},
    )
    assert bulk_re_res.status_code == status.HTTP_200_OK
    assert bulk_re_res.json()["count"] == 2


@pytest.mark.anyio
async def test_assign_roles_and_resend_invitation(
    users_client: AsyncClient,
    admin_and_targets: tuple[UserResponse, User, User],
    dbsession: AsyncSession,
) -> None:
    """Verify assigning roles to users and resending invitation emails."""
    _, target1, _ = admin_and_targets

    # Create a role
    role_repo = BaseRepository(Role, dbsession)
    role = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Operator",
            "slug": f"operator_{uuid.uuid4().hex[:6]}",
            "is_system": False,
        }
    )

    # Assign role
    assign_res = await users_client.post(
        f"/api/users/{target1.id}/roles",
        json={"role_ids": [str(role.id)]},
    )
    assert assign_res.status_code == status.HTTP_200_OK
    assert role.slug in assign_res.json()["roles"]

    # Remove role
    remove_res = await users_client.delete(f"/api/users/{target1.id}/roles/{role.id}")
    assert remove_res.status_code == status.HTTP_200_OK
    assert role.slug not in remove_res.json()["roles"]

    # Resend invitation
    invite_res = await users_client.post(f"/api/users/{target1.id}/resend-invitation")
    assert invite_res.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_seed_initial_superadmin(dbsession: AsyncSession) -> None:
    """Verify bootstrap superadmin seeding and idempotency."""
    from argon2 import PasswordHasher

    from fastapi_plantilla.core.config import settings
    from fastapi_plantilla.modules.auth.models import Account, User
    from scripts.seeds.superadmin import seed_superadmin

    test_email = f"bootstrap_{uuid.uuid4().hex[:6]}@example.com"
    test_pass = "SuperSecure1996*"  # noqa: S105

    orig_email = settings.initial_superadmin_email
    orig_pass = settings.initial_superadmin_password
    orig_name = settings.initial_superadmin_name
    try:
        settings.initial_superadmin_email = test_email
        settings.initial_superadmin_password = test_pass
        settings.initial_superadmin_name = "Bootstrap Admin"

        # 1. First run seeds the user
        await seed_superadmin(dbsession)
        await dbsession.commit()

        user_repo = BaseRepository(User, dbsession)
        user = await user_repo.find_first(User.email == test_email)
        assert user is not None
        assert user.is_super_admin is True
        assert user.is_active is True
        assert user.name == "Bootstrap Admin"

        acc_repo = BaseRepository(Account, dbsession)
        account = await acc_repo.find_first(Account.user_id == user.id)
        assert account is not None
        assert account.provider_id == "credential"
        assert account.password is not None
        ph = PasswordHasher()
        ph.verify(account.password, test_pass)

        # 2. Second run is idempotent
        await seed_superadmin(dbsession)
        await dbsession.commit()
    finally:
        settings.initial_superadmin_email = orig_email
        settings.initial_superadmin_password = orig_pass
        settings.initial_superadmin_name = orig_name
