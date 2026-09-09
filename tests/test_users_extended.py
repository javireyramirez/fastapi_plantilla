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
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.rbac.models import Role, SystemModule
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.teams.routes import router as teams_router
from fastapi_plantilla.modules.users.routes import router as users_router


class ExtendedUsersAuthContext:
    """Helper holder for current authenticated user in extended user tests."""

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
async def setup_extended_users(
    dbsession: AsyncSession,
) -> tuple[UserResponse, User, User, Team, Role, Role]:
    """Create system modules, superadmin, targets, a team, and roles."""
    mod_repo = BaseRepository(SystemModule, dbsession)
    for code, name in (
        ("users", "Users Module"),
        ("teams", "Teams Module"),
        ("roles", "Roles Module"),
    ):
        if not await mod_repo.find_first(SystemModule.code == code):
            await mod_repo.create(
                {
                    "id": generate_uuid7(),
                    "code": code,
                    "name": name,
                    "is_active": True,
                }
            )

    user_repo = BaseRepository(User, dbsession)
    admin = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin Extended",
            "email": f"ext_admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
            "email_verified": True,
        }
    )
    user1 = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Verified User",
            "email": f"ver_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
            "email_verified": True,
        }
    )
    user2 = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Unverified User",
            "email": f"unver_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
            "email_verified": False,
        }
    )

    team_repo = BaseRepository(Team, dbsession)
    team = await team_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Platform Team",
            "slug": f"platform_{uuid.uuid4().hex[:6]}",
        }
    )

    role_repo = BaseRepository(Role, dbsession)
    role_admin = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Admin Role",
            "slug": f"role_admin_{uuid.uuid4().hex[:6]}",
        }
    )
    role_editor = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Editor Role",
            "slug": f"role_editor_{uuid.uuid4().hex[:6]}",
        }
    )

    return _user_to_response(admin), user1, user2, team, role_admin, role_editor


@pytest.fixture
def extended_auth(
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> ExtendedUsersAuthContext:
    """Auth context initialized with superadmin."""
    admin, _, _, _, _, _ = setup_extended_users
    return ExtendedUsersAuthContext(admin)


@pytest.fixture
def extended_app(
    dbsession: AsyncSession, extended_auth: ExtendedUsersAuthContext
) -> FastAPI:
    """FastAPI test app configured with users, teams, and rbac routers."""
    app = FastAPI()
    app.include_router(users_router, prefix="/api")
    app.include_router(teams_router, prefix="/api")
    app.include_router(rbac_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: extended_auth.user

    def _superuser_check() -> UserResponse:
        if not extended_auth.user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="SuperAdmin required"
            )
        return extended_auth.user

    mock_email = MagicMock()
    mock_email.send = AsyncMock()
    mock_builder = MagicMock()
    mock_builder.to.return_value = mock_builder
    mock_builder.subject.return_value = mock_builder
    mock_builder.template.return_value = "msg"
    mock_email.create_builder.return_value = mock_builder

    app.dependency_overrides[get_current_active_superuser] = _superuser_check
    app.dependency_overrides[get_email_service] = lambda: mock_email
    return app


@pytest.fixture
async def client(
    extended_app: FastAPI, anyio_backend: Any
) -> AsyncGenerator[AsyncClient, None]:
    """Test async client."""
    async with AsyncClient(
        transport=ASGITransport(extended_app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.anyio
async def test_combobox_and_temporal_filters(
    client: AsyncClient,
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> None:
    """Verify GET /api/users/list and advanced temporal + email_verified filters."""
    _, user1, user2, _, _, _ = setup_extended_users

    # 1. Combobox dropdown
    combo_res = await client.get("/api/users/list?limit=10")
    assert combo_res.status_code == status.HTTP_200_OK
    items = combo_res.json()
    assert isinstance(items, list)
    assert any(it["id"] == str(user1.id) for it in items)

    # Search filter in combobox
    combo_search = await client.get("/api/users/list?search=Verified+User")
    assert combo_search.status_code == status.HTTP_200_OK
    assert any(it["id"] == str(user1.id) for it in combo_search.json())

    # 2. email_verified filter on /api/users
    ver_res = await client.get("/api/users?email_verified=true")
    assert ver_res.status_code == status.HTTP_200_OK
    ver_ids = [u["id"] for u in ver_res.json()["data"]]
    assert str(user1.id) in ver_ids
    assert str(user2.id) not in ver_ids

    unver_res = await client.get("/api/users?email_verified=false")
    assert unver_res.status_code == status.HTTP_200_OK
    unver_ids = [u["id"] for u in unver_res.json()["data"]]
    assert str(user2.id) in unver_ids
    assert str(user1.id) not in unver_ids

    # 3. Temporal filters
    now = datetime.now(UTC)
    from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_res = await client.get(
        f"/api/users?created_at_from={from_iso}&created_at_to={to_iso}"
    )
    assert date_res.status_code == status.HTTP_200_OK
    assert len(date_res.json()["data"]) >= 3


@pytest.mark.anyio
async def test_export_users(
    client: AsyncClient,
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> None:
    """Verify POST /api/users/export for CSV and JSON formats."""
    _, user1, _, _, _, _ = setup_extended_users

    # Export CSV
    csv_res = await client.post("/api/users/export", json={"format": "csv"})
    assert csv_res.status_code == status.HTTP_200_OK
    assert "text/csv" in csv_res.headers["content-type"]
    assert "attachment; filename=" in csv_res.headers["content-disposition"]
    assert "email" in csv_res.text

    # Export JSON
    json_res = await client.post("/api/users/export", json={"format": "json"})
    assert json_res.status_code == status.HTTP_200_OK
    assert "application/json" in json_res.headers["content-type"]
    json_data = json_res.json()
    assert isinstance(json_data, list)
    assert any(row.get("id") == str(user1.id) for row in json_data)

    # Export TSV
    tsv_res = await client.post("/api/users/export", json={"format": "tsv"})
    assert tsv_res.status_code == status.HTTP_200_OK
    assert "text/tab-separated-values" in tsv_res.headers["content-type"]
    assert "\t" in tsv_res.text

    # Export Google Sheets (TSV with UTF-8 BOM)
    sheets_res = await client.post(
        "/api/users/export", json={"format": "google_sheets"}
    )
    assert sheets_res.status_code == status.HTTP_200_OK
    assert "text/tab-separated-values" in sheets_res.headers["content-type"]
    assert sheets_res.text.startswith("\ufeff")
    assert "\t" in sheets_res.text


@pytest.mark.anyio
async def test_trash_restore_and_bulk_operations(
    client: AsyncClient,
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> None:
    """Verify single and bulk trash, restore, and permanent deletion."""
    _, user1, user2, _, _, _ = setup_extended_users

    # 1. Single soft-delete and restore
    del_res = await client.delete(f"/api/users/{user1.id}")
    assert del_res.status_code == status.HTTP_200_OK

    # Restoring single user
    res_res = await client.post(f"/api/users/{user1.id}/restore")
    assert res_res.status_code == status.HTTP_200_OK

    # 2. Bulk trash
    bulk_trash = await client.post(
        "/api/users/bulk/trash",
        json={"ids": [str(user1.id), str(user2.id)]},
    )
    assert bulk_trash.status_code == status.HTTP_200_OK
    assert bulk_trash.json()["count"] == 2

    # 3. Bulk restore
    bulk_res = await client.post(
        "/api/users/bulk/restore",
        json={"ids": [str(user1.id), str(user2.id)]},
    )
    assert bulk_res.status_code == status.HTTP_200_OK
    assert bulk_res.json()["count"] == 2

    # 4. Trash and permanent delete
    await client.delete(f"/api/users/{user1.id}")
    perm_res = await client.delete(f"/api/users/{user1.id}/permanent")
    assert perm_res.status_code == status.HTTP_200_OK

    # User no longer found
    check_res = await client.get(f"/api/users/{user1.id}")
    assert check_res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_user_teams_and_roles_subroutes(
    client: AsyncClient,
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> None:
    """Verify subroutes for user team and role assignments."""
    _, _, user2, team, role_admin, role_editor = setup_extended_users

    # 1. User Teams assignment
    assign_team_res = await client.post(
        f"/api/users/{user2.id}/teams",
        json={"team_ids": [str(team.id)]},
    )
    assert assign_team_res.status_code == status.HTTP_200_OK
    assert assign_team_res.json()["count"] == 1

    # Get user teams
    teams_list_res = await client.get(f"/api/users/{user2.id}/teams")
    assert teams_list_res.status_code == status.HTTP_200_OK
    assert len(teams_list_res.json()["data"]) == 1
    assert teams_list_res.json()["data"][0]["slug"] == team.slug

    # Remove user teams
    del_team_res = await client.request(
        "DELETE",
        f"/api/users/{user2.id}/teams",
        json={"team_ids": [str(team.id)]},
    )
    assert del_team_res.status_code == status.HTTP_200_OK
    assert del_team_res.json()["count"] == 1

    # 2. User Roles detailed listing and bulk remove
    assign_roles = await client.post(
        f"/api/users/{user2.id}/roles",
        json={"role_ids": [str(role_admin.id), str(role_editor.id)]},
    )
    assert assign_roles.status_code == status.HTTP_200_OK

    roles_list = await client.get(f"/api/users/{user2.id}/roles")
    assert roles_list.status_code == status.HTTP_200_OK
    assert len(roles_list.json()["data"]) == 2
    assert "assigned_at" in roles_list.json()["data"][0]

    # Bulk remove role
    del_roles = await client.request(
        "DELETE",
        f"/api/users/{user2.id}/roles",
        json={"role_ids": [str(role_admin.id)]},
    )
    assert del_roles.status_code == status.HTTP_200_OK
    assert del_roles.json()["count"] == 1

    # Verify single role remains
    after_roles = await client.get(f"/api/users/{user2.id}/roles")
    assert len(after_roles.json()["data"]) == 1
    assert after_roles.json()["data"][0]["slug"] == role_editor.slug


@pytest.mark.anyio
async def test_teams_date_filtering(
    client: AsyncClient,
    setup_extended_users: tuple[UserResponse, User, User, Team, Role, Role],
) -> None:
    """Verify temporal filters on GET /api/teams."""
    now = datetime.now(UTC)
    from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = await client.get(
        f"/api/teams?created_at_from={from_iso}&created_at_to={to_iso}"
    )
    assert res.status_code == status.HTTP_200_OK
    assert len(res.json()["data"]) >= 1
