import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, status
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
from fastapi_plantilla.modules.companies.routes import router as companies_router
from fastapi_plantilla.modules.rbac.models import (
    RbacActions,
    Role,
    RoleAssignment,
    RolePermission,
    ScopeType,
    SystemModule,
)


class CompaniesAuthContext:
    """Helper holder for current authenticated user in Companies tests."""

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
async def setup_companies_context(
    dbsession: AsyncSession,
) -> tuple[UserResponse, UserResponse]:
    """Create system modules, superadmin user, and regular member user."""
    mod_repo = BaseRepository(SystemModule, dbsession)
    for code, name in (
        ("companies", "Companies Module"),
        ("users", "Users Module"),
        ("roles", "Roles Module"),
    ):
        if not await mod_repo.find_first(SystemModule.code == code):
            await mod_repo.create(
                {
                    "id": generate_uuid7(),
                    "code": code,
                    "name": name,
                    "description": f"{name} description",
                }
            )

    user_repo = BaseRepository(User, dbsession)
    admin_model = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Super Admin",
            "email": f"admin_comp_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular_model = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular User",
            "email": f"user_comp_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )

    # Give regular user OWN permission on companies
    role_repo = BaseRepository(Role, dbsession)
    perm_repo = BaseRepository(RolePermission, dbsession)
    assign_repo = BaseRepository(RoleAssignment, dbsession)

    role = await role_repo.create(
        {
            "id": generate_uuid7(),
            "name": f"Company Owner Role {uuid.uuid4().hex[:4]}",
            "slug": f"comp-owner-{uuid.uuid4().hex[:6]}",
        }
    )

    comp_mod = await mod_repo.find_first(SystemModule.code == "companies")
    assert comp_mod is not None

    for action in (
        RbacActions.READ,
        RbacActions.CREATE,
        RbacActions.UPDATE,
        RbacActions.DELETE,
    ):
        await perm_repo.create(
            {
                "id": generate_uuid7(),
                "role_id": role.id,
                "module_id": comp_mod.id,
                "action": action,
                "scope": ScopeType.OWN,
            }
        )

    await assign_repo.create(
        {
            "id": generate_uuid7(),
            "role_id": role.id,
            "entity_type": "USER",
            "entity_id": regular_model.id,
        }
    )

    await dbsession.commit()
    return _user_to_response(admin_model), _user_to_response(regular_model)


@pytest.fixture
async def companies_client(
    setup_companies_context: tuple[UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> AsyncGenerator[tuple[AsyncClient, CompaniesAuthContext], None]:
    """Test client wired to companies router with switchable auth context."""
    admin_user, _ = setup_companies_context
    context = CompaniesAuthContext(admin_user)

    app = FastAPI()
    app.include_router(companies_router, prefix="/api")

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: context.user
    app.dependency_overrides[get_current_active_superuser] = lambda: context.user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, context


@pytest.mark.anyio
async def test_create_and_get_company(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
) -> None:
    """Verify company creation, NIF uniqueness, and retrieval by ID."""
    client, context = companies_client

    nif = f"B{uuid.uuid4().hex[:7].upper()}"
    create_res = await client.post(
        "/api/companies",
        json={
            "name": "Acme Corporation",
            "nif": nif,
            "sector": "Technology",
            "website": "https://acme.example.com",
            "description": "Leading provider of anvils and roadrunner traps",
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    data = create_res.json()
    assert data["name"] == "Acme Corporation"
    assert data["nif"] == nif
    assert data["sector"] == "Technology"
    assert data["website"] == "https://acme.example.com"
    assert data["version"] == 1
    company_id = data["id"]

    # 1. Duplicate NIF conflict
    dup_res = await client.post(
        "/api/companies",
        json={
            "name": "Acme Copycat",
            "nif": nif,
        },
    )
    assert dup_res.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in dup_res.json()["detail"]

    # 2. Get by ID
    get_res = await client.get(f"/api/companies/{company_id}")
    assert get_res.status_code == status.HTTP_200_OK
    get_data = get_res.json()
    assert get_data["id"] == company_id
    assert get_data["name"] == "Acme Corporation"
    assert get_data["created_by"] == str(context.user.id)
    assert get_data["created_by_name"] == context.user.name
    assert get_data["creator"]["id"] == str(context.user.id)
    assert get_data["creator"]["name"] == context.user.name
    assert get_data["creator"]["email"] == context.user.email


@pytest.mark.anyio
async def test_update_company_and_optimistic_locking(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
) -> None:
    """Verify company updating, NIF uniqueness on update, and version increments."""
    client, context = companies_client

    nif1 = f"A{uuid.uuid4().hex[:7].upper()}"
    nif2 = f"B{uuid.uuid4().hex[:7].upper()}"

    res1 = await client.post(
        "/api/companies", json={"name": "Company One", "nif": nif1}
    )
    res2 = await client.post(
        "/api/companies", json={"name": "Company Two", "nif": nif2}
    )
    c1_id = res1.json()["id"]
    _ = res2.json()["id"]

    # 1. Update name
    patch_res = await client.patch(
        f"/api/companies/{c1_id}",
        json={"name": "Company One Renamed", "version": 1},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    patch_data = patch_res.json()
    assert patch_data["name"] == "Company One Renamed"
    assert patch_data["version"] == 2
    assert patch_data["updated_by"] == str(context.user.id)
    assert patch_data["updated_by_name"] == context.user.name
    assert patch_data["updater"]["name"] == context.user.name

    # 2. Attempt to update c1's NIF to c2's NIF -> Conflict
    conflict_res = await client.patch(
        f"/api/companies/{c1_id}",
        json={"nif": nif2},
    )
    assert conflict_res.status_code == status.HTTP_409_CONFLICT

    # 3. Version mismatch conflict (optimistic lock)
    stale_res = await client.patch(
        f"/api/companies/{c1_id}",
        json={"name": "Stale Update", "version": 1},
    )
    assert stale_res.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_list_companies_pagination_and_filters(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
) -> None:
    """Verify list filtering by search (name/nif), sector, and pagination."""
    client, _ = companies_client

    unique_tag = uuid.uuid4().hex[:5]
    await client.post(
        "/api/companies",
        json={
            "name": f"Alpha {unique_tag}",
            "nif": f"X1_{unique_tag}",
            "sector": "Healthcare",
        },
    )
    await client.post(
        "/api/companies",
        json={
            "name": f"Beta {unique_tag}",
            "nif": f"X2_{unique_tag}",
            "sector": "Fintech",
        },
    )

    # 1. Search by name
    search_res = await client.get(f"/api/companies?search=Alpha+{unique_tag}")
    assert search_res.status_code == status.HTTP_200_OK
    assert len(search_res.json()["data"]) == 1
    assert search_res.json()["data"][0]["name"] == f"Alpha {unique_tag}"

    # 2. Filter by sector
    sector_res = await client.get(f"/api/companies?search={unique_tag}&sector=Fintech")
    assert sector_res.status_code == status.HTTP_200_OK
    assert len(sector_res.json()["data"]) == 1
    assert sector_res.json()["data"][0]["sector"] == "Fintech"

    # 3. Combobox list dropdown
    combo_res = await client.get(f"/api/companies/list?search=Alpha+{unique_tag}")
    assert combo_res.status_code == status.HTTP_200_OK
    items = combo_res.json()
    assert len(items) == 1
    assert items[0]["name"] == f"Alpha {unique_tag}"


@pytest.mark.anyio
async def test_export_companies(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
) -> None:
    """Verify POST /api/companies/export across CSV, JSON, TSV, and Google Sheets."""
    client, _ = companies_client

    tag = uuid.uuid4().hex[:6]
    await client.post(
        "/api/companies",
        json={"name": f"Export Corp {tag}", "nif": f"E_{tag}"},
    )

    # CSV
    csv_res = await client.post("/api/companies/export", json={"format": "csv"})
    assert csv_res.status_code == status.HTTP_200_OK
    assert "text/csv" in csv_res.headers["content-type"]
    assert "nif" in csv_res.text

    # JSON
    json_res = await client.post("/api/companies/export", json={"format": "json"})
    assert json_res.status_code == status.HTTP_200_OK
    assert "application/json" in json_res.headers["content-type"]
    assert isinstance(json_res.json(), list)

    # TSV
    tsv_res = await client.post("/api/companies/export", json={"format": "tsv"})
    assert tsv_res.status_code == status.HTTP_200_OK
    assert "\t" in tsv_res.text

    # Google Sheets
    gs_res = await client.post(
        "/api/companies/export", json={"format": "google_sheets"}
    )
    assert gs_res.status_code == status.HTTP_200_OK
    assert gs_res.text.startswith("\ufeff")


@pytest.mark.anyio
async def test_companies_trash_restore_and_bulk_operations(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
) -> None:
    """Verify soft-delete, trash bin recovery, bulk ops, and permanent delete."""
    client, _ = companies_client

    t1 = uuid.uuid4().hex[:5]
    t2 = uuid.uuid4().hex[:5]

    # Bulk create
    bulk_res = await client.post(
        "/api/companies/bulk",
        json=[
            {"name": f"Bulk A {t1}", "nif": f"BA_{t1}"},
            {"name": f"Bulk B {t2}", "nif": f"BB_{t2}"},
        ],
    )
    assert bulk_res.status_code == status.HTTP_201_CREATED
    assert bulk_res.json()["count"] == 2

    # Query created IDs
    search = await client.get(f"/api/companies?search={t1}")
    comp1 = search.json()["data"][0]
    c1_id = comp1["id"]

    # 1. Soft-delete single
    del_res = await client.delete(f"/api/companies/{c1_id}")
    assert del_res.status_code == status.HTTP_200_OK

    # Verify not returned in active list
    list_active = await client.get(f"/api/companies?search={t1}")
    assert len(list_active.json()["data"]) == 0

    # Verify present in trash query
    list_trash = await client.get(f"/api/companies?search={t1}&is_trash=true")
    assert len(list_trash.json()["data"]) == 1

    # 2. Restore single
    res_res = await client.post(f"/api/companies/{c1_id}/restore")
    assert res_res.status_code == status.HTTP_200_OK

    # 3. Permanent delete single
    await client.delete(f"/api/companies/{c1_id}")
    perm_res = await client.delete(f"/api/companies/{c1_id}/permanent")
    assert perm_res.status_code == status.HTTP_200_OK

    # Check completely gone
    not_found = await client.get(f"/api/companies/{c1_id}")
    assert not_found.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_companies_rbac_ownership_scope_isolation(
    companies_client: tuple[AsyncClient, CompaniesAuthContext],
    setup_companies_context: tuple[UserResponse, UserResponse],
) -> None:
    """Verify that a user with OWN scope cannot access another user's company."""
    client, context = companies_client
    admin_user, regular_user = setup_companies_context

    # 1. Superadmin creates a company
    context.user = admin_user
    admin_comp_res = await client.post(
        "/api/companies",
        json={"name": "Admin Secret Corp", "nif": f"ADM_{uuid.uuid4().hex[:6]}"},
    )
    admin_comp_id = admin_comp_res.json()["id"]

    # 2. Switch context to regular user (has OWN permission on companies)
    context.user = regular_user

    # Regular user creates their own company
    user_comp_res = await client.post(
        "/api/companies",
        json={"name": "User Independent Corp", "nif": f"USR_{uuid.uuid4().hex[:6]}"},
    )
    assert user_comp_res.status_code == status.HTTP_201_CREATED
    user_comp_id = user_comp_res.json()["id"]

    # Regular user can see their own company
    user_get = await client.get(f"/api/companies/{user_comp_id}")
    assert user_get.status_code == status.HTTP_200_OK
    assert user_get.json()["name"] == "User Independent Corp"

    # Regular user CANNOT see admin's company (returns 404 anti-oracle)
    forbidden_get = await client.get(f"/api/companies/{admin_comp_id}")
    assert forbidden_get.status_code == status.HTTP_404_NOT_FOUND

    # In paginated list, regular user only sees their own company
    paginated_res = await client.get("/api/companies")
    assert paginated_res.status_code == status.HTTP_200_OK
    ids = [c["id"] for c in paginated_res.json()["data"]]
    assert user_comp_id in ids
    assert admin_comp_id not in ids
