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
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.models import SystemModule
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.teams.routes import router as teams_router


class TeamsAuthContext:
    """Helper holder for current authenticated user in Teams tests."""

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
async def team_users(
    dbsession: AsyncSession,
) -> tuple[UserResponse, UserResponse, UserResponse]:
    """Create superadmin, leader, and member users."""
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
    leader = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Team Leader",
            "email": f"leader_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    member = await repo.create(
        {
            "id": generate_uuid7(),
            "name": "Team Member",
            "email": f"member_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    return (
        _user_to_response(admin),
        _user_to_response(leader),
        _user_to_response(member),
    )


@pytest.fixture
def teams_auth(
    team_users: tuple[UserResponse, UserResponse, UserResponse],
) -> TeamsAuthContext:
    """Provide mutable auth state defaulting to super admin."""
    admin, _, _ = team_users
    return TeamsAuthContext(admin)


@pytest.fixture
def teams_app(dbsession: AsyncSession, teams_auth: TeamsAuthContext) -> FastAPI:
    """Configure test FastAPI application with Teams and RBAC routers."""
    app = FastAPI()
    app.include_router(teams_router, prefix="/api")
    app.include_router(rbac_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: teams_auth.user

    def _superuser_check() -> UserResponse:
        if not teams_auth.user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SuperAdmin required",
            )
        return teams_auth.user

    app.dependency_overrides[get_current_active_superuser] = _superuser_check
    return app


@pytest.fixture
async def teams_client(
    teams_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Provide AsyncClient for Teams endpoints."""
    async with AsyncClient(
        transport=ASGITransport(teams_app), base_url="http://test"
    ) as ac:
        yield ac


# =========================================================================
# Teams Tests
# =========================================================================


@pytest.mark.anyio
async def test_team_crud_and_membership(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify team lifecycle, member enrollment, updating, and removal."""
    _, _, member = team_users

    # Ensure teams module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "teams",
            "name": "Teams Module",
            "is_active": True,
        }
    )

    # Create team as admin
    create_res = await teams_client.post(
        "/api/teams",
        json={
            "name": "Engineering Team",
            "slug": "engineering",
            "description": "Core engineering department",
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    team_data = create_res.json()
    team_id = team_data["id"]
    assert team_data["name"] == "Engineering Team"
    assert team_data["members_count"] == 1  # creator enrolled

    # Add member to team
    add_res = await teams_client.post(
        f"/api/teams/{team_id}/members",
        json={"user_id": str(member.id)},
    )
    assert add_res.status_code == status.HTTP_201_CREATED
    assert add_res.json()["user_email"] == member.email

    # Duplicate member conflict
    conflict_res = await teams_client.post(
        f"/api/teams/{team_id}/members",
        json={"user_id": str(member.id)},
    )
    assert conflict_res.status_code == status.HTTP_409_CONFLICT

    # List team members
    list_mem_res = await teams_client.get(f"/api/teams/{team_id}/members")
    assert list_mem_res.status_code == status.HTTP_200_OK
    members = list_mem_res.json()
    assert len(members) == 2

    # Remove member
    del_mem_res = await teams_client.delete(f"/api/teams/{team_id}/members/{member.id}")
    assert del_mem_res.status_code == status.HTTP_200_OK

    # Verify removal
    list_after = await teams_client.get(f"/api/teams/{team_id}/members")
    assert len(list_after.json()) == 1

    # Soft delete team (syncs to trash)
    del_team_res = await teams_client.delete(f"/api/teams/{team_id}")
    assert del_team_res.status_code == status.HTTP_200_OK

    # Fetching deleted team returns detail with TRASHED status for read-only preview
    get_del_res = await teams_client.get(f"/api/teams/{team_id}")
    assert get_del_res.status_code == status.HTTP_200_OK
    assert get_del_res.json()["status"] == "TRASHED"

    # Individual restore via POST /{team_id}/restore
    restore_res = await teams_client.post(f"/api/teams/{team_id}/restore")
    assert restore_res.status_code == status.HTTP_200_OK
    assert restore_res.json()["status"] == "ACTIVE"


@pytest.mark.anyio
async def test_team_permissions_inheritance(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify that a user belonging to a team inherits roles assigned to that team."""
    _, _, member = team_users

    # Ensure modules exist
    mod_repo = BaseRepository(SystemModule, dbsession)
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "teams",
            "name": "Teams Module",
            "is_active": True,
        }
    )
    await mod_repo.create(
        {
            "id": generate_uuid7(),
            "code": "storage",
            "name": "Storage Module",
            "is_active": True,
        }
    )

    # Create team
    create_res = await teams_client.post(
        "/api/teams",
        json={
            "name": "DevOps",
            "slug": f"devops_{uuid.uuid4().hex[:6]}",
        },
    )
    team_id = create_res.json()["id"]

    # Enroll regular member in the team
    await teams_client.post(
        f"/api/teams/{team_id}/members",
        json={"user_id": str(member.id)},
    )

    # Create role granting storage READ with TEAM scope
    role_res = await teams_client.post(
        "/api/rbac/roles",
        json={
            "name": "Storage Auditor",
            "slug": f"storage_auditor_{uuid.uuid4().hex[:6]}",
            "permissions": [
                {"module_code": "storage", "action": "READ", "scope": "TEAM"}
            ],
        },
    )
    role_id = role_res.json()["id"]

    # Assign role to TEAM (not to the user directly)
    assign_res = await teams_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "TEAM", "entity_id": team_id},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # Now switch to the regular member and check effective permissions
    teams_auth.user = member
    perms_res = await teams_client.get("/api/rbac/my-permissions")
    assert perms_res.status_code == status.HTTP_200_OK
    matrix = perms_res.json()["permissions"]
    assert "storage" in matrix
    assert matrix["storage"]["READ"] == "TEAM"


@pytest.mark.anyio
async def test_teams_bulk_operations(
    teams_client: AsyncClient,
) -> None:
    """Test bulk trash, restore, and permanent deletion of teams."""
    # Create two teams
    r1 = await teams_client.post(
        "/api/teams",
        json={"name": "Bulk Team 1", "slug": f"bulk_1_{uuid.uuid4().hex[:6]}"},
    )
    assert r1.status_code == status.HTTP_201_CREATED
    id1 = r1.json()["id"]

    r2 = await teams_client.post(
        "/api/teams",
        json={"name": "Bulk Team 2", "slug": f"bulk_2_{uuid.uuid4().hex[:6]}"},
    )
    assert r2.status_code == status.HTTP_201_CREATED
    id2 = r2.json()["id"]

    # 1. Bulk trash both teams
    trash_res = await teams_client.post(
        "/api/teams/bulk/trash",
        json={"ids": [id1, id2]},
    )
    assert trash_res.status_code == status.HTTP_200_OK
    assert trash_res.json()["count"] == 2

    # Both teams can be inspected with TRASHED status for read-only preview
    res_t1 = await teams_client.get(f"/api/teams/{id1}")
    assert res_t1.status_code == status.HTTP_200_OK
    assert res_t1.json()["status"] == "TRASHED"
    res_t2 = await teams_client.get(f"/api/teams/{id2}")
    assert res_t2.status_code == status.HTTP_200_OK
    assert res_t2.json()["status"] == "TRASHED"

    # Both teams are excluded from active list
    teams_list = await teams_client.get("/api/teams")
    active_ids = [t["id"] for t in teams_list.json()["data"]]
    assert id1 not in active_ids
    assert id2 not in active_ids

    # 2. Bulk restore team 1
    restore_res = await teams_client.post(
        "/api/teams/bulk/restore",
        json={"ids": [id1]},
    )
    assert restore_res.status_code == status.HTTP_200_OK
    assert restore_res.json()["count"] == 1

    # Team 1 is back active, team 2 still trashed
    res_t1_back = await teams_client.get(f"/api/teams/{id1}")
    assert res_t1_back.status_code == status.HTTP_200_OK
    assert res_t1_back.json()["status"] == "ACTIVE"
    res_t2_still_del = await teams_client.get(f"/api/teams/{id2}")
    assert res_t2_still_del.status_code == status.HTTP_200_OK
    assert res_t2_still_del.json()["status"] == "TRASHED"

    # 3. Bulk permanent delete team 2 (via POST alias)
    perm_res = await teams_client.post(
        "/api/teams/bulk/permanent",
        json={"ids": [id2]},
    )
    assert perm_res.status_code == status.HTTP_200_OK
    assert perm_res.json()["count"] == 1

    # Team 2 is now completely gone (404)
    res_t2_gone = await teams_client.get(f"/api/teams/{id2}")
    assert res_t2_gone.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_team_reuse_slug_when_in_trash_and_restore_conflict(
    teams_client: AsyncClient,
) -> None:
    """Verify creating team with trashed slug succeeds, but restore conflicts."""
    test_slug = f"reused_team_{uuid.uuid4().hex[:6]}"

    # 1. Create first team
    r1 = await teams_client.post(
        "/api/teams",
        json={"name": "First Team", "slug": test_slug},
    )
    assert r1.status_code == status.HTTP_201_CREATED
    id1 = r1.json()["id"]

    # 2. Soft-delete first team
    del_res = await teams_client.delete(f"/api/teams/{id1}")
    assert del_res.status_code == status.HTTP_200_OK

    # 3. Create second team with identical slug -> Must succeed (201 Created, no 500)
    r2 = await teams_client.post(
        "/api/teams",
        json={"name": "Second Team", "slug": test_slug},
    )
    assert r2.status_code == status.HTTP_201_CREATED
    id2 = r2.json()["id"]
    assert id2 != id1

    # 4. Attempting to restore first team conflicts
    restore_conflict = await teams_client.post(
        "/api/teams/bulk/restore",
        json={"ids": [id1]},
    )
    assert restore_conflict.status_code == status.HTTP_409_CONFLICT
    assert "already in use by an active team" in restore_conflict.json()["detail"]

    # 5. Delete second team permanently
    await teams_client.delete(f"/api/teams/{id2}")
    await teams_client.post(
        "/api/teams/bulk/permanent",
        json={"ids": [id2]},
    )

    # 6. Now restoring first team succeeds
    restore_ok = await teams_client.post(
        "/api/teams/bulk/restore",
        json={"ids": [id1]},
    )
    assert restore_ok.status_code == status.HTTP_200_OK
    assert restore_ok.json()["count"] == 1


@pytest.mark.anyio
async def test_team_restore_out_of_scope_does_not_leak_slug_conflict(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify restoring a trashed team out of scope returns 404, not 409."""
    admin, _, member = team_users
    teams_auth.user = admin

    # Ensure teams module exists
    mod_repo = BaseRepository(SystemModule, dbsession)
    if not await mod_repo.find_first(SystemModule.code == "teams"):
        await mod_repo.create(
            {
                "id": generate_uuid7(),
                "code": "teams",
                "name": "Teams Module",
                "is_active": True,
            }
        )

    suffix = uuid.uuid4().hex[:6]
    conflict_slug = f"oracle_{suffix}"

    # 1. Admin creates and deletes Team 1
    r1 = await teams_client.post(
        "/api/teams",
        json={"name": "Foreign Trashed Team", "slug": conflict_slug},
    )
    assert r1.status_code == status.HTTP_201_CREATED
    id1 = r1.json()["id"]

    del_res = await teams_client.delete(f"/api/teams/{id1}")
    assert del_res.status_code == status.HTTP_200_OK

    # 2. Admin creates active Team 2 with same slug
    r2 = await teams_client.post(
        "/api/teams",
        json={"name": "Active Conflicting Team", "slug": conflict_slug},
    )
    assert r2.status_code == status.HTTP_201_CREATED

    # 3. Grant regular member OWN scope on teams:RESTORE and teams:READ
    role_res = await teams_client.post(
        "/api/rbac/roles",
        json={
            "name": f"Team Member Role {suffix}",
            "slug": f"team_member_role_{suffix}",
            "permissions": [
                {"module_code": "teams", "action": "RESTORE", "scope": "OWN"},
                {"module_code": "teams", "action": "READ", "scope": "OWN"},
            ],
        },
    )
    assert role_res.status_code == status.HTTP_201_CREATED
    role_id = role_res.json()["id"]

    assign_res = await teams_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(member.id)},
    )
    assert assign_res.status_code == status.HTTP_200_OK

    # 4. Member attempts to restore Admin's trashed team
    teams_auth.user = member
    res_restore = await teams_client.post(f"/api/teams/{id1}/restore")
    assert res_restore.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_team_list_with_own_scope_sees_created_team(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify user with OWN scope can see their own created team in list."""
    admin, _, member = team_users
    teams_auth.user = admin

    mod_repo = BaseRepository(SystemModule, dbsession)
    if not await mod_repo.find_first(SystemModule.code == "teams"):
        await mod_repo.create(
            {
                "id": generate_uuid7(),
                "code": "teams",
                "name": "Teams Module",
                "is_active": True,
            }
        )

    suffix = uuid.uuid4().hex[:6]
    role_res = await teams_client.post(
        "/api/rbac/roles",
        json={
            "name": f"Team Creator Role {suffix}",
            "slug": f"team_creator_role_{suffix}",
            "permissions": [
                {"module_code": "teams", "action": "CREATE", "scope": "OWN"},
                {"module_code": "teams", "action": "READ", "scope": "OWN"},
            ],
        },
    )
    role_id = role_res.json()["id"]

    await teams_client.post(
        "/api/rbac/assignments",
        json={"role_id": role_id, "entity_type": "USER", "entity_id": str(member.id)},
    )

    # Member creates their team
    teams_auth.user = member
    created = await teams_client.post(
        "/api/teams",
        json={"name": f"Member Team {suffix}", "slug": f"mem_team_{suffix}"},
    )
    assert created.status_code == status.HTTP_201_CREATED
    member_team_id = created.json()["id"]

    # Member lists teams -> must see their own team
    list_res = await teams_client.get("/api/teams")
    assert list_res.status_code == status.HTTP_200_OK
    listed_ids = [t["id"] for t in list_res.json()["data"]]
    assert member_team_id in listed_ids

    # Admin creates another team
    teams_auth.user = admin
    admin_team = await teams_client.post(
        "/api/teams",
        json={"name": f"Admin Team {suffix}", "slug": f"admin_team_{suffix}"},
    )
    assert admin_team.status_code == status.HTTP_201_CREATED
    admin_team_id = admin_team.json()["id"]

    # Member lists teams -> must NOT see Admin's team
    teams_auth.user = member
    list_res2 = await teams_client.get("/api/teams")
    assert list_res2.status_code == status.HTTP_200_OK
    listed_ids2 = [t["id"] for t in list_res2.json()["data"]]
    assert member_team_id in listed_ids2
    assert admin_team_id not in listed_ids2


@pytest.mark.anyio
async def test_team_cannot_remove_owner_from_members(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
) -> None:
    """Verify attempting to delete the team owner returns 400 Bad Request."""
    admin, _, _ = team_users
    teams_auth.user = admin

    suffix = uuid.uuid4().hex[:6]
    res = await teams_client.post(
        "/api/teams",
        json={"name": f"Owner Guard {suffix}", "slug": f"guard_{suffix}"},
    )
    team_id = res.json()["id"]

    del_res = await teams_client.delete(f"/api/teams/{team_id}/members/{admin.id}")
    assert del_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot remove team owner" in del_res.json()["detail"]


@pytest.mark.anyio
async def test_team_add_member_nonexistent_role_returns_404(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
) -> None:
    """Verify adding a member with non-existent role_id returns 404, not 409 or 500."""
    admin, _, member = team_users
    teams_auth.user = admin

    suffix = uuid.uuid4().hex[:6]
    res = await teams_client.post(
        "/api/teams",
        json={"name": f"Role Guard {suffix}", "slug": f"role_guard_{suffix}"},
    )
    team_id = res.json()["id"]

    add_res = await teams_client.post(
        f"/api/teams/{team_id}/members",
        json={"user_id": str(member.id), "role_id": str(uuid.uuid4())},
    )
    assert add_res.status_code == status.HTTP_404_NOT_FOUND
    assert "Role not found" in add_res.json()["detail"]


@pytest.mark.anyio
async def test_team_update_owner_auto_enrolls_new_owner(
    teams_client: AsyncClient,
    teams_auth: TeamsAuthContext,
    team_users: tuple[UserResponse, UserResponse, UserResponse],
) -> None:
    """Verify updating team owner auto-enrolls new owner into members."""
    admin, leader, _ = team_users
    teams_auth.user = admin

    suffix = uuid.uuid4().hex[:6]
    res = await teams_client.post(
        "/api/teams",
        json={"name": f"Transfer Team {suffix}", "slug": f"transfer_{suffix}"},
    )
    team_id = res.json()["id"]

    # Transfer ownership to leader (who was not previously a member)
    update_res = await teams_client.patch(
        f"/api/teams/{team_id}",
        json={"owner_id": str(leader.id)},
    )
    assert update_res.status_code == status.HTTP_200_OK
    assert update_res.json()["owner_id"] == str(leader.id)
    assert update_res.json()["members_count"] == 2

    # Check member list
    mem_res = await teams_client.get(f"/api/teams/{team_id}/members")
    assert mem_res.status_code == status.HTTP_200_OK
    member_user_ids = [m["user_id"] for m in mem_res.json()]
    assert str(leader.id) in member_user_ids
