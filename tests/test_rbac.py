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
from fastapi_plantilla.modules.audit.repository import AuditRepository
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
    assert data["supported_actions"] == []
    assert data["requires_super_admin"] is False
    assert data["category"] == "system"
    assert data["category_name"] == "Sistema"
    assert data["category_icon"] == "cpu"
    assert data["category_order"] == 4

    # Duplicate code conflict
    conflict_res = await rbac_client.post(
        "/api/rbac/modules",
        json={"code": "test_module", "name": "Duplicate"},
    )
    assert conflict_res.status_code == status.HTTP_409_CONFLICT

    # Create module with custom supported_actions and requires_super_admin
    custom_actions_res = await rbac_client.post(
        "/api/rbac/modules",
        json={
            "code": "test_custom_actions",
            "name": "Custom Actions Module",
            "supported_actions": ["READ", "EXPORT"],
            "requires_super_admin": True,
        },
    )
    assert custom_actions_res.status_code == status.HTTP_201_CREATED
    assert custom_actions_res.json()["supported_actions"] == ["READ", "EXPORT"]
    assert custom_actions_res.json()["requires_super_admin"] is True

    # List modules
    list_res = await rbac_client.get("/api/rbac/modules")
    assert list_res.status_code == status.HTTP_200_OK
    modules = list_res.json()
    assert any(
        m["code"] == "test_module" and m["requires_super_admin"] is False
        for m in modules
    )
    assert any(
        m["code"] == "test_custom_actions"
        and m["supported_actions"] == ["READ", "EXPORT"]
        and m["requires_super_admin"] is True
        for m in modules
    )


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


@pytest.mark.anyio
async def test_list_role_assignments_api(
    rbac_client: AsyncClient,
    rbac_auth: RbacAuthContext,
    rbac_test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify listing role assignments with metadata and sorting."""
    admin, regular = rbac_test_users

    # 1. Create a role as admin
    rbac_auth.user = admin
    role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Assignment Inspector",
            "slug": f"assignment_inspector_{uuid.uuid4().hex[:6]}",
            "permissions": [],
        },
    )
    assert role_res.status_code == status.HTTP_201_CREATED
    role_id = role_res.json()["id"]

    # 2. Assign role to regular user
    assign_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(regular.id)},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # 3. Query GET /api/rbac/assignments with canonical snake_case query params
    res = await rbac_client.get(
        f"/api/rbac/assignments?page=1&limit=10&sort_by=created_at&sort_order=desc&role_id={role_id}"
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert "data" in data
    assert "meta" in data
    assert data["meta"]["page"] == 1
    assert data["meta"]["limit"] == 10
    assert data["meta"]["total"] == 1

    assignment_item = data["data"][0]
    assignment_id = assignment_item["id"]
    assert assignment_item["role_id"] == role_id
    assert assignment_item["entity_type"] == "user"
    assert assignment_item["entity_id"] == str(regular.id)
    assert assignment_item["role"]["name"] == "Assignment Inspector"
    assert assignment_item["user"]["email"] == regular.email
    assert assignment_item["assigned_user"]["id"] == str(regular.id)

    # 4. Query sub-resource GET /api/rbac/roles/{role_id}/assignments
    sub_res = await rbac_client.get(f"/api/rbac/roles/{role_id}/assignments")
    assert sub_res.status_code == status.HTTP_200_OK
    sub_data = sub_res.json()
    assert sub_data["meta"]["total"] == 1
    assert sub_data["data"][0]["id"] == assignment_id

    # 5. Detail GET /api/rbac/assignments/{assignment_id}
    detail_res = await rbac_client.get(f"/api/rbac/assignments/{assignment_id}")
    assert detail_res.status_code == status.HTTP_200_OK
    detail_data = detail_res.json()
    assert detail_data["id"] == assignment_id
    assert detail_data["role"]["id"] == role_id
    assert detail_data["user"]["id"] == str(regular.id)

    # 6. Sub-resource detail GET /api/rbac/roles/{role_id}/assignments/{assignment_id}
    sub_detail_res = await rbac_client.get(
        f"/api/rbac/roles/{role_id}/assignments/{assignment_id}"
    )
    assert sub_detail_res.status_code == status.HTTP_200_OK
    assert sub_detail_res.json()["id"] == assignment_id

    # 7. Non-existent role returns 404
    non_existent_role_id = uuid.uuid4()
    not_found_res = await rbac_client.get(
        f"/api/rbac/assignments?role_id={non_existent_role_id}"
    )
    assert not_found_res.status_code == status.HTTP_404_NOT_FOUND

    # 8. Non-existent assignment returns 404
    fake_assign_id = uuid.uuid4()
    assign_404 = await rbac_client.get(f"/api/rbac/assignments/{fake_assign_id}")
    assert assign_404.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_roles_bulk_operations(
    rbac_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Test bulk trash, restore, permanent delete, and system role protection."""
    from fastapi_plantilla.modules.rbac.models import Role

    # Create two regular custom roles
    r1 = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Bulk Role 1", "slug": f"bulk_r1_{uuid.uuid4().hex[:6]}"},
    )
    assert r1.status_code == status.HTTP_201_CREATED
    id1 = r1.json()["id"]

    r2 = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Bulk Role 2", "slug": f"bulk_r2_{uuid.uuid4().hex[:6]}"},
    )
    assert r2.status_code == status.HTTP_201_CREATED
    id2 = r2.json()["id"]

    # Create a system role directly in DB
    role_repo = BaseRepository(Role, dbsession)
    sys_role = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "System Protected Role",
            "slug": f"sys_role_{uuid.uuid4().hex[:6]}",
            "is_system": True,
        }
    )

    # 1. Attempting bulk trash including system role must fail with 400
    sys_trash_res = await rbac_client.post(
        "/api/rbac/roles/bulk/trash",
        json={"ids": [id1, str(sys_role.id)]},
    )
    assert sys_trash_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "System roles cannot be deleted" in sys_trash_res.json()["detail"]

    # 2. Bulk trash both custom roles succeeds
    trash_res = await rbac_client.post(
        "/api/rbac/roles/bulk/trash",
        json={"ids": [id1, id2]},
    )
    assert trash_res.status_code == status.HTTP_200_OK
    assert trash_res.json()["count"] == 2

    # Verification: roles can be inspected with TRASHED status for preview
    res_r1 = await rbac_client.get(f"/api/rbac/roles/{id1}")
    assert res_r1.status_code == status.HTTP_200_OK
    assert res_r1.json()["status"] == "TRASHED"
    res_r2 = await rbac_client.get(f"/api/rbac/roles/{id2}")
    assert res_r2.status_code == status.HTTP_200_OK
    assert res_r2.json()["status"] == "TRASHED"

    # Both roles are excluded from active list
    roles_list = await rbac_client.get("/api/rbac/roles")
    active_role_ids = [r["id"] for r in roles_list.json()]
    assert id1 not in active_role_ids
    assert id2 not in active_role_ids

    # 3. Individual restore role 1 via POST /api/rbac/roles/{id1}/restore
    restore_res = await rbac_client.post(f"/api/rbac/roles/{id1}/restore")
    assert restore_res.status_code == status.HTTP_200_OK
    assert restore_res.json()["status"] == "ACTIVE"
    res_r1_back = await rbac_client.get(f"/api/rbac/roles/{id1}")
    assert res_r1_back.status_code == status.HTTP_200_OK
    assert res_r1_back.json()["status"] == "ACTIVE"

    # 4. Attempting bulk permanent delete with system role must fail with 400
    sys_perm_res = await rbac_client.request(
        "DELETE",
        "/api/rbac/roles/bulk/permanent",
        json={"ids": [id2, str(sys_role.id)]},
    )
    assert sys_perm_res.status_code == status.HTTP_400_BAD_REQUEST

    # 5. Bulk permanent delete role 2 succeeds (via DELETE method)
    perm_res = await rbac_client.request(
        "DELETE",
        "/api/rbac/roles/bulk/permanent",
        json={"ids": [id2]},
    )
    assert perm_res.status_code == status.HTTP_200_OK
    assert perm_res.json()["count"] == 1

    # Role 2 is now completely gone (404)
    res_r2_gone = await rbac_client.get(f"/api/rbac/roles/{id2}")
    assert res_r2_gone.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_role_reuse_slug_when_in_trash_and_restore_conflict(
    rbac_client: AsyncClient,
) -> None:
    """Verify creating role with trashed slug succeeds, but restore conflicts."""
    test_slug = f"reused_role_{uuid.uuid4().hex[:6]}"

    # 1. Create first role
    res1 = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "First Role", "slug": test_slug},
    )
    assert res1.status_code == status.HTTP_201_CREATED
    first_id = res1.json()["id"]

    # 2. Soft-delete first role into trash
    del_res = await rbac_client.delete(f"/api/rbac/roles/{first_id}")
    assert del_res.status_code == status.HTTP_200_OK

    # 3. Create second role with identical slug -> Must SUCCEED (201 Created, no 500!)
    res2 = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Second Role", "slug": test_slug},
    )
    assert res2.status_code == status.HTTP_201_CREATED
    second_id = res2.json()["id"]
    assert second_id != first_id

    # 4. Attempting to restore first role from trash now conflicts with active role
    restore_conflict = await rbac_client.post(
        "/api/rbac/roles/bulk/restore",
        json={"ids": [first_id]},
    )
    assert restore_conflict.status_code == status.HTTP_409_CONFLICT
    assert "already in use by an active role" in restore_conflict.json()["detail"]

    # 5. Delete second role permanently
    await rbac_client.delete(f"/api/rbac/roles/{second_id}")
    await rbac_client.request(
        "DELETE",
        "/api/rbac/roles/bulk/permanent",
        json={"ids": [second_id]},
    )

    # 6. Now restoring first role succeeds cleanly
    restore_ok = await rbac_client.post(
        "/api/rbac/roles/bulk/restore",
        json={"ids": [first_id]},
    )
    assert restore_ok.status_code == status.HTTP_200_OK
    assert restore_ok.json()["count"] == 1


@pytest.mark.anyio
async def test_role_optimistic_concurrency_and_versioning(
    rbac_client: AsyncClient,
) -> None:
    """Verify role versioning and optimistic concurrency control with ETags."""
    # 1. Create role with initial version 1
    create_res = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Locking Role", "slug": f"lock_{uuid.uuid4().hex[:6]}"},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    role_data = create_res.json()
    role_id = role_data["id"]
    assert role_data["version"] == 1

    # 2. Conflicting update with wrong If-Match returns 409
    stale_res = await rbac_client.patch(
        f"/api/rbac/roles/{role_id}",
        headers={"If-Match": '"99"'},
        json={"name": "Should Fail"},
    )
    assert stale_res.status_code == status.HTTP_409_CONFLICT
    assert "Concurrent modification conflict" in stale_res.json()["detail"]

    # 3. Successful update with valid If-Match increments version to 2
    ok_res = await rbac_client.patch(
        f"/api/rbac/roles/{role_id}",
        headers={"If-Match": '"1"'},
        json={"name": "Updated Role"},
    )
    assert ok_res.status_code == status.HTTP_200_OK
    assert ok_res.json()["name"] == "Updated Role"
    assert ok_res.json()["version"] == 2

    # 4. Old If-Match: 1 now fails with 409
    stale_res2 = await rbac_client.patch(
        f"/api/rbac/roles/{role_id}",
        headers={"If-Match": '"1"'},
        json={"name": "Should Fail Again"},
    )
    assert stale_res2.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_role_assignment_validation(
    rbac_client: AsyncClient,
    rbac_test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify entity_type validation and destination entity existence checks."""
    admin, _ = rbac_test_users
    role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Validator Role", "slug": f"val_{uuid.uuid4().hex[:6]}"},
    )
    role_id = role_res.json()["id"]

    # 1. Invalid entity_type returns 400
    invalid_type_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={
            "role_id": role_id,
            "entity_type": "ORGANIZATION",
            "entity_id": str(admin.id),
        },
    )
    assert invalid_type_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid entity_type" in invalid_type_res.json()["detail"]

    # 2. Non-existent user returns 404
    fake_user_id = uuid.uuid4()
    user_404_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={
            "role_id": role_id,
            "entity_type": "USER",
            "entity_id": str(fake_user_id),
        },
    )
    assert user_404_res.status_code == status.HTTP_404_NOT_FOUND
    assert f"User with ID {fake_user_id} not found" in user_404_res.json()["detail"]

    # 3. Non-existent team returns 404
    fake_team_id = uuid.uuid4()
    team_404_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={
            "role_id": role_id,
            "entity_type": "TEAM",
            "entity_id": str(fake_team_id),
        },
    )
    assert team_404_res.status_code == status.HTTP_404_NOT_FOUND
    assert f"Team with ID {fake_team_id} not found" in team_404_res.json()["detail"]


@pytest.mark.anyio
async def test_role_create_atomic_rollback_on_invalid_module(
    rbac_client: AsyncClient,
) -> None:
    """Verify create_role atomically rolls back if module validation fails."""
    slug = f"atomic_fail_{uuid.uuid4().hex[:6]}"
    res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Atomic Fail",
            "slug": slug,
            "permissions": [
                {
                    "module_code": "non_existent_module_slug",
                    "action": "READ",
                    "scope": "GLOBAL",
                }
            ],
        },
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert "non_existent_module_slug" in res.json()["detail"]

    # Ensure role was not left behind in the database
    roles_list = await rbac_client.get(f"/api/rbac/roles?name={slug}")
    assert len(roles_list.json()) == 0


@pytest.mark.anyio
async def test_role_audit_logging(
    rbac_client: AsyncClient,
    rbac_test_users: tuple[UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify audit entries for SET_ROLE_PERMISSIONS, ASSIGN_ROLE, UNASSIGN_ROLE."""
    _, regular = rbac_test_users

    # Ensure documents module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    mod = await mod_repo.find_first(SystemModule.code == "users")
    if not mod:
        await mod_repo.create(
            {"id": generate_uuid7(), "code": "users", "name": "Users"}
        )

    # 1. Create role
    role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "Audited Role", "slug": f"audit_{uuid.uuid4().hex[:6]}"},
    )
    assert role_res.status_code == status.HTTP_201_CREATED
    role_id = role_res.json()["id"]

    # 2. Set permissions
    perm_res = await rbac_client.put(
        f"/api/rbac/roles/{role_id}/permissions",
        json={
            "permissions": [
                {"module_code": "users", "action": "READ", "scope": "GLOBAL"}
            ]
        },
    )
    assert perm_res.status_code == status.HTTP_200_OK

    # 3. Assign role
    assign_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(regular.id)},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # 4. Unassign role
    unassign_res = await rbac_client.request(
        "DELETE",
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(regular.id)},
    )
    assert unassign_res.status_code == status.HTTP_200_OK

    # 5. Verify audit logs via AuditRepository
    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("role", uuid.UUID(role_id))
    actions = [entry.action for entry in logs]

    assert "CREATE" in actions
    assert "SET_ROLE_PERMISSIONS" in actions
    assert "ASSIGN_ROLE" in actions
    assert "UNASSIGN_ROLE" in actions


@pytest.mark.anyio
async def test_trashed_role_revokes_permissions_and_prevents_assignment(
    rbac_client: AsyncClient,
    rbac_auth: RbacAuthContext,
    rbac_test_users: tuple[UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify trashed roles revoke permissions immediately and cannot be assigned."""
    admin, regular = rbac_test_users

    # Ensure test module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    mod = await mod_repo.find_first(SystemModule.code == "test_module")
    if not mod:
        await mod_repo.create(
            {"id": generate_uuid7(), "code": "test_module", "name": "Test"}
        )

    # 1. Create role with READ permission on test_module
    role_res = await rbac_client.post(
        "/api/rbac/roles",
        json={
            "name": "Revocable Role",
            "slug": f"revocable_{uuid.uuid4().hex[:6]}",
            "permissions": [
                {"module_code": "test_module", "action": "READ", "scope": "GLOBAL"}
            ],
        },
    )
    assert role_res.status_code == status.HTTP_201_CREATED
    role_id = role_res.json()["id"]

    # 2. Assign role to regular user
    assign_res = await rbac_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(regular.id)},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # 3. User passes protected check
    rbac_auth.user = regular
    check_ok = await rbac_client.get("/api/test-protected")
    assert check_ok.status_code == status.HTTP_200_OK

    # 4. Soft-delete role into trash as admin
    rbac_auth.user = admin
    trash_res = await rbac_client.delete(f"/api/rbac/roles/{role_id}")
    assert trash_res.status_code == status.HTTP_200_OK
    assert trash_res.json()["status"] == "TRASHED"

    # 5. User immediately loses permission (403 Forbidden)!
    rbac_auth.user = regular
    check_forbidden = await rbac_client.get("/api/test-protected")
    assert check_forbidden.status_code == status.HTTP_403_FORBIDDEN

    # 6. Attempting to assign the trashed role fails with 400
    rbac_auth.user = admin
    assign_trashed = await rbac_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(admin.id)},
    )
    assert assign_trashed.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot assign a trashed role" in assign_trashed.json()["detail"]

    # 7. Restore role -> User regains permissions
    restore_res = await rbac_client.post(f"/api/rbac/roles/{role_id}/restore")
    assert restore_res.status_code == status.HTTP_200_OK
    assert restore_res.json()["status"] == "ACTIVE"

    rbac_auth.user = regular
    check_restored = await rbac_client.get("/api/test-protected")
    assert check_restored.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_role_etag_headers_and_permissions_concurrency(
    rbac_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify ETags are emitted in GET, PATCH, and PUT permissions."""
    mod_repo = BaseRepository(SystemModule, dbsession)
    mod = await mod_repo.find_first(SystemModule.code == "test_module")
    if not mod:
        await mod_repo.create(
            {"id": generate_uuid7(), "code": "test_module", "name": "Test"}
        )

    # 1. Create role
    create_res = await rbac_client.post(
        "/api/rbac/roles",
        json={"name": "ETag Role", "slug": f"etag_{uuid.uuid4().hex[:6]}"},
    )
    role_id = create_res.json()["id"]

    # 2. GET emits ETag
    get_res = await rbac_client.get(f"/api/rbac/roles/{role_id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert "ETag" in get_res.headers
    assert get_res.headers["ETag"] == 'W/"1"'

    # 3. PUT permissions with stale If-Match returns 409
    stale_perm = await rbac_client.put(
        f"/api/rbac/roles/{role_id}/permissions",
        headers={"If-Match": 'W/"99"'},
        json={
            "permissions": [
                {"module_code": "test_module", "action": "READ", "scope": "OWN"}
            ]
        },
    )
    assert stale_perm.status_code == status.HTTP_409_CONFLICT

    # 4. PUT permissions with valid If-Match increments version and updates ETag
    ok_perm = await rbac_client.put(
        f"/api/rbac/roles/{role_id}/permissions",
        headers={"If-Match": 'W/"1"'},
        json={
            "permissions": [
                {"module_code": "test_module", "action": "READ", "scope": "OWN"}
            ]
        },
    )
    assert ok_perm.status_code == status.HTTP_200_OK
    assert ok_perm.json()["version"] == 2
    assert ok_perm.headers.get("ETag") == 'W/"2"'


@pytest.mark.anyio
async def test_create_module_audit_logging(
    rbac_client: AsyncClient,
    dbsession: AsyncSession,
    rbac_test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify registering a module produces a CREATE_MODULE audit entry with actor."""
    admin, _ = rbac_test_users
    code = f"mod_{uuid.uuid4().hex[:6]}"
    res = await rbac_client.post(
        "/api/rbac/modules",
        json={"code": code, "name": "Audited Module"},
    )
    assert res.status_code == status.HTTP_201_CREATED
    mod_id = uuid.UUID(res.json()["id"])

    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("role", mod_id)
    entry = next((e for e in logs if e.action == "CREATE_MODULE"), None)
    assert entry is not None
    assert entry.actor_id == admin.id


@pytest.mark.anyio
async def test_assignments_endpoints_privacy_superuser_only(
    rbac_client: AsyncClient,
    rbac_auth: RbacAuthContext,
    rbac_test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify assignment listing endpoints require superuser privileges."""
    _, regular = rbac_test_users
    rbac_auth.user = regular

    res = await rbac_client.get("/api/rbac/assignments")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "SuperAdmin required" in res.json()["detail"]


@pytest.mark.anyio
async def test_modules_show_in_nav(
    rbac_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify show_in_nav column in module creation, listing, and seed sync."""
    await sync_system_modules(dbsession)
    await dbsession.commit()

    # 1. GET /api/rbac/modules includes show_in_nav
    res = await rbac_client.get("/api/rbac/modules")
    assert res.status_code == status.HTTP_200_OK
    modules = res.json()
    notif_mod = next(m for m in modules if m["code"] == "notifications")
    assert notif_mod["show_in_nav"] is False
    companies_mod = next(m for m in modules if m["code"] == "companies")
    assert companies_mod["show_in_nav"] is True

    # 2. POST /api/rbac/modules with explicit show_in_nav=False
    code = f"mod_hidden_{uuid.uuid4().hex[:6]}"
    create_res = await rbac_client.post(
        "/api/rbac/modules",
        json={"code": code, "name": "Hidden Module", "show_in_nav": False},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    assert create_res.json()["show_in_nav"] is False
