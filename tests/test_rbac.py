import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import ScopeContext, ScopeType
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.catalog import (
    CORE_SYSTEM_MODULES,
    sync_system_modules,
)
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.models import SystemModule
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.rbac.schema import RbacActions


class RbacAuthContext:
    """Helper holder for current authenticated user in RBAC tests."""

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
async def rbac_test_users(
    dbsession: AsyncSession,
) -> tuple[UserResponse, UserResponse]:
    """Create and persist admin and regular users."""
    repo = BaseRepository(User, dbsession)
    admin = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"super_{uuid.uuid4().hex[:6]}@example.com",
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
    return _user_to_response(admin), _user_to_response(regular)


@pytest.fixture
def rbac_auth(rbac_test_users: tuple[UserResponse, UserResponse]) -> RbacAuthContext:
    """Provide mutable auth state defaulting to super admin."""
    admin, _ = rbac_test_users
    return RbacAuthContext(admin)


@pytest.fixture
def rbac_app(dbsession: AsyncSession, rbac_auth: RbacAuthContext) -> FastAPI:
    """Configure test FastAPI application with RBAC router and test endpoints."""
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")

    # Test endpoint protected by require_permission
    @app.get("/api/test-protected")
    async def protected_endpoint(
        scope: ScopeContext = Depends(
            require_permission("test_module", RbacActions.READ)
        ),
    ) -> dict[str, Any]:
        return {"scope": scope.scope, "user_id": str(scope.user_id)}

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: rbac_auth.user

    def _superuser_check() -> UserResponse:
        if not rbac_auth.user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SuperAdmin required",
            )
        return rbac_auth.user

    app.dependency_overrides[get_current_active_superuser] = _superuser_check
    return app


@pytest.fixture
async def rbac_client(
    rbac_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Provide AsyncClient for RBAC endpoints."""
    async with AsyncClient(
        transport=ASGITransport(rbac_app), base_url="http://test"
    ) as ac:
        yield ac


# =========================================================================
# RBAC Tests
# =========================================================================


@pytest.mark.anyio
async def test_module_catalog_flow(rbac_client: AsyncClient) -> None:
    """Verify creating, querying and conflict prevention on system modules."""
    res = await rbac_client.post(
        "/api/rbac/modules",
        json={
            "code": "test_module",
            "name": "Test Module",
            "description": "Module for testing",
        },
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["code"] == "test_module"
    assert data["name"] == "Test Module"

    # Duplicate code conflict
    conflict_res = await rbac_client.post(
        "/api/rbac/modules",
        json={"code": "test_module", "name": "Duplicate"},
    )
    assert conflict_res.status_code == status.HTTP_409_CONFLICT

    # List modules
    list_res = await rbac_client.get("/api/rbac/modules")
    assert list_res.status_code == status.HTTP_200_OK
    modules = list_res.json()
    assert any(m["code"] == "test_module" for m in modules)


@pytest.mark.anyio
async def test_role_crud_and_permissions(
    rbac_client: AsyncClient, dbsession: AsyncSession
) -> None:
    """Verify creating roles, setting permissions matrix, and role lifecycle."""
    # Ensure module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "documents",
            "name": "Documents Module",
            "is_active": True,
        }
    )

    # Create role with permissions
    res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Editor",
            "slug": "editor",
            "description": "Can edit documents",
            "permissions": [
                {
                    "module_code": "documents",
                    "action": "READ",
                    "scope": "GLOBAL",
                },
                {
                    "module_code": "documents",
                    "action": "UPDATE",
                    "scope": "OWN",
                },
            ],
        },
    )
    assert res.status_code == status.HTTP_201_CREATED
    role_data = res.json()
    role_id = role_data["id"]
    assert role_data["slug"] == "editor"
    assert len(role_data["permissions"]) == 2

    # Update role metadata
    update_res = await rbac_client.patch(
        f"/api/rbac/roles/{role_id}",
        json={"name": "Senior Editor"},
    )
    assert update_res.status_code == status.HTTP_200_OK
    assert update_res.json()["name"] == "Senior Editor"

    # Replace permissions with all 8 actions
    put_perms_res = await rbac_client.put(
        f"/api/rbac/roles/{role_id}/permissions",
        json={
            "permissions": [
                {"module_code": "documents", "action": "CREATE", "scope": "OWN"},
                {"module_code": "documents", "action": "READ", "scope": "TEAM"},
                {"module_code": "documents", "action": "UPDATE", "scope": "OWN"},
                {"module_code": "documents", "action": "DELETE", "scope": "OWN"},
                {"module_code": "documents", "action": "RESTORE", "scope": "OWN"},
                {"module_code": "documents", "action": "EXPORT", "scope": "GLOBAL"},
                {"module_code": "documents", "action": "IMPORT", "scope": "GLOBAL"},
                {"module_code": "documents", "action": "SETTINGS", "scope": "GLOBAL"},
            ]
        },
    )
    assert put_perms_res.status_code == status.HTTP_200_OK
    updated_perms = put_perms_res.json()["permissions"]
    assert len(updated_perms) == 8


@pytest.mark.anyio
async def test_system_role_protection(
    rbac_client: AsyncClient, dbsession: AsyncSession
) -> None:
    """Verify system roles cannot be deleted or have their slugs changed."""
    from fastapi_plantilla.modules.rbac.models import Role

    role_repo = BaseRepository(Role, dbsession)
    sys_role = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "SuperAdmin Role",
            "slug": "superadmin_role",
            "is_system": True,
        }
    )

    # Cannot delete system role
    del_res = await rbac_client.delete(f"/api/rbac/roles/{sys_role.id}")
    assert del_res.status_code == status.HTTP_400_BAD_REQUEST

    # Cannot alter system role slug
    patch_res = await rbac_client.patch(
        f"/api/rbac/roles/{sys_role.id}",
        json={"slug": "new_slug"},
    )
    assert patch_res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_require_permission_dependency_and_hierarchy(
    rbac_client: AsyncClient,
    rbac_auth: RbacAuthContext,
    rbac_test_users: tuple[UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify require_permission dependency resolution and hierarchy logic."""
    admin, regular = rbac_test_users

    # Register module
    mod_repo = BaseRepository(SystemModule, dbsession)
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "test_module",
            "name": "Test Module",
            "is_active": True,
        }
    )

    # 1. SuperAdmin automatically passes with GLOBAL scope
    rbac_auth.user = admin
    super_res = await rbac_client.get("/api/test-protected")
    assert super_res.status_code == status.HTTP_200_OK
    assert super_res.json()["scope"] == ScopeType.GLOBAL

    # 2. Regular user with no assigned role -> 403 Forbidden
    rbac_auth.user = regular
    denied_res = await rbac_client.get("/api/test-protected")
    assert denied_res.status_code == status.HTTP_403_FORBIDDEN

    # 3. Create role granting OWN scope on test_module
    rbac_auth.user = admin
    role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Viewer",
            "slug": "viewer",
            "permissions": [
                {"module_code": "test_module", "action": "READ", "scope": "OWN"}
            ],
        },
    )
    role_id = role_res.json()["id"]

    # Assign role to regular user
    assign_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(regular.id)},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # Regular user now passes with OWN scope
    rbac_auth.user = regular
    ok_res = await rbac_client.get("/api/test-protected")
    assert ok_res.status_code == status.HTTP_200_OK
    assert ok_res.json()["scope"] == ScopeType.OWN

    # 4. Grant TEAM scope: hierarchy ensures user receives TEAM (highest)
    rbac_auth.user = admin
    team_role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Team Reader",
            "slug": "team_reader",
            "permissions": [
                {"module_code": "test_module", "action": "READ", "scope": "TEAM"}
            ],
        },
    )
    team_role_id = team_role_res.json()["id"]
    await rbac_client.post(
        "/api/rbac/assignments",
        json={
            "role_id": team_role_id,
            "entity_type": "USER",
            "entity_id": str(regular.id),
        },
    )

    # Regular user now resolves to TEAM scope
    rbac_auth.user = regular
    hierarchy_res = await rbac_client.get("/api/test-protected")
    assert hierarchy_res.status_code == status.HTTP_200_OK
    assert hierarchy_res.json()["scope"] == ScopeType.TEAM

    # Test permissions matrix endpoint
    matrix_res = await rbac_client.get("/api/rbac/my-permissions")
    assert matrix_res.status_code == status.HTTP_200_OK
    matrix = matrix_res.json()
    assert matrix["permissions"]["test_module"]["READ"] == "TEAM"


@pytest.mark.anyio
async def test_sync_system_modules_idempotency_and_api(
    dbsession: AsyncSession,
    rbac_client: AsyncClient,
    rbac_auth: RbacAuthContext,
    rbac_test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify sync_system_modules registers core modules and updates idempotently."""
    _, regular = rbac_test_users

    # 1. Sync core modules
    synced = await sync_system_modules(dbsession)
    assert len(synced) >= len(CORE_SYSTEM_MODULES)
    core_codes = {m["code"] for m in CORE_SYSTEM_MODULES}
    synced_codes = {m.code for m in synced}
    assert core_codes.issubset(synced_codes)

    # 2. Re-running sync with updated name/description is idempotent and keeps IDs
    first_users_mod = next(m for m in synced if m.code == "users")
    original_id = first_users_mod.id

    custom_modules = [
        {
            "code": "users",
            "name": "Gestión de Usuarios",
            "description": "Usuarios actualizados",
        }
    ]
    updated_synced = await sync_system_modules(dbsession, modules=custom_modules)
    updated_users_mod = next(m for m in updated_synced if m.code == "users")
    assert updated_users_mod.id == original_id
    assert updated_users_mod.name == "Gestión de Usuarios"
    assert updated_users_mod.description == "Usuarios actualizados"

    # 3. GET /api/rbac/modules returns the modules for authenticated user
    rbac_auth.user = regular
    res = await rbac_client.get("/api/rbac/modules")
    assert res.status_code == status.HTTP_200_OK
    modules_data = res.json()
    assert any(m["code"] == "users" for m in modules_data)
