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

    # Fetching deleted team returns 404
    get_del_res = await teams_client.get(f"/api/teams/{team_id}")
    assert get_del_res.status_code == status.HTTP_404_NOT_FOUND


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
