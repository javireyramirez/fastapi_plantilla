import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.core.crud.schema import (
    ScopeContext,
    ScopeType,
)
from fastapi_plantilla.core.crud.service import BaseAuditService
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    RecordStatus,
    UUID7PrimaryKeyMixin,
)
from fastapi_plantilla.modules.auth.models import User


class RouterTestItem(Base, UUID7PrimaryKeyMixin, AuditFieldsMixin, OptimisticLockMixin):
    """SQLAlchemy model for router factory integration tests."""

    __tablename__ = "test_router_items"

    name: Mapped[str] = mapped_column(String(100), nullable=False)


class ItemOut(BaseModel):
    """Pydantic response schema for test item."""

    id: uuid.UUID
    name: str
    status: RecordStatus = RecordStatus.ACTIVE

    model_config = ConfigDict(from_attributes=True)


class ItemCreate(BaseModel):
    """Pydantic creation schema for test item."""

    name: str


class ItemUpdate(BaseModel):
    """Pydantic update schema for test item."""

    name: str | None = None


@pytest.fixture
async def setup_test_router(
    dbsession: AsyncSession, _engine: AsyncEngine
) -> AsyncGenerator[tuple[FastAPI, BaseAuditService[RouterTestItem]], None]:
    """Configure a test FastAPI application with create_crud_router."""
    async with _engine.begin() as conn:
        await conn.run_sync(RouterTestItem.metadata.create_all)

    repo = BaseRepository(RouterTestItem, dbsession)
    service = BaseAuditService(repo)

    mock_user = User(
        id=uuid.uuid4(),
        name="Router Admin",
        email="admin_router@example.com",
        is_super_admin=True,
    )

    router = create_crud_router(
        service_getter=lambda: service,
        schema_out=ItemOut,
        schema_create=ItemCreate,
        schema_update=ItemUpdate,
        prefix="/items",
        tags=["Items"],
        current_user_getter=lambda: mock_user,
        scope_getter=lambda: ScopeContext(scope=ScopeType.GLOBAL, user_id=mock_user.id),
    )

    app = FastAPI()
    app.include_router(router)
    yield app, service


@pytest.fixture
async def router_client(
    setup_test_router: tuple[FastAPI, BaseAuditService[RouterTestItem]],
) -> AsyncGenerator[AsyncClient, None]:
    """Provide an AsyncClient wired to the test FastAPI router."""
    app, _ = setup_test_router
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_crud_router_create_and_get_by_id(
    router_client: AsyncClient,
) -> None:
    """Verify POST /items/ creates record and GET /items/{id} retrieves it."""
    res_create = await router_client.post("/items/", json={"name": "Widget Alpha"})
    assert res_create.status_code == 201
    created_data = res_create.json()
    assert created_data["name"] == "Widget Alpha"
    assert created_data["status"] == "ACTIVE"
    item_id = created_data["id"]

    res_get = await router_client.get(f"/items/{item_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == item_id
    assert res_get.json()["name"] == "Widget Alpha"


async def test_crud_router_find_paginated_and_dropdown_list(
    router_client: AsyncClient,
) -> None:
    """Verify paginated listing and dropdown selectors."""
    tag = uuid.uuid4().hex[:6]
    for i in range(3):
        await router_client.post("/items/", json={"name": f"Paging {tag} {i}"})

    res_page = await router_client.get(f"/items/?search={tag}&limit=10")
    assert res_page.status_code == 200
    pdata = res_page.json()
    assert "data" in pdata
    assert "meta" in pdata
    assert pdata["meta"]["total"] >= 3
    assert len(pdata["data"]) >= 3

    res_list = await router_client.get(f"/items/list?search={tag}")
    assert res_list.status_code == 200
    items = res_list.json()
    assert len(items) >= 3
    assert "id" in items[0]
    assert "name" in items[0]


async def test_crud_router_update(
    router_client: AsyncClient,
) -> None:
    """Verify PATCH /items/{id} partially updates a record and checks If-Match."""
    res_create = await router_client.post("/items/", json={"name": "Original Name"})
    item_id = res_create.json()["id"]

    # 1. Update with valid If-Match header
    res_update = await router_client.patch(
        f"/items/{item_id}",
        json={"name": "Modified Name"},
        headers={"If-Match": '"1"'},
    )
    assert res_update.status_code == 200
    assert res_update.json()["name"] == "Modified Name"

    # 2. Update with stale If-Match header -> 409 Conflict
    res_conflict = await router_client.patch(
        f"/items/{item_id}",
        json={"name": "Stale Update"},
        headers={"If-Match": '"1"'},
    )
    assert res_conflict.status_code == 409

    # 3. Update with new version If-Match -> 200
    res_update2 = await router_client.patch(
        f"/items/{item_id}",
        json={"name": "Second Update"},
        headers={"If-Match": '"2"'},
    )
    assert res_update2.status_code == 200

    res_get = await router_client.get(f"/items/{item_id}")
    assert res_get.json()["name"] == "Second Update"


async def test_crud_router_lifecycle_trash_restore_permanent(
    router_client: AsyncClient,
) -> None:
    """Verify soft-delete trash, restore, and permanent deletion flow."""
    res_create = await router_client.post("/items/", json={"name": "Lifecycle Item"})
    item_id = res_create.json()["id"]

    # 1. Soft-delete to trash
    res_trash = await router_client.delete(f"/items/{item_id}")
    assert res_trash.status_code == 200
    assert res_trash.json()["status"] == "TRASHED"

    # Trashed record has status TRASHED
    res_trashed = await router_client.get(f"/items/{item_id}")
    assert res_trashed.status_code == 200
    assert res_trashed.json()["status"] == "TRASHED"

    # 2. Restore from trash
    res_restore = await router_client.post(f"/items/{item_id}/restore")
    assert res_restore.status_code == 200
    assert res_restore.json()["status"] == "ACTIVE"

    # Active GET works again
    res_active2 = await router_client.get(f"/items/{item_id}")
    assert res_active2.status_code == 200

    # 3. Trash again, then permanently delete
    await router_client.delete(f"/items/{item_id}")
    res_perm = await router_client.delete(f"/items/{item_id}/permanent")
    assert res_perm.status_code == 200

    # Permanent delete: record is completely gone
    res_gone = await router_client.get(f"/items/{item_id}")
    assert res_gone.status_code == 404
    assert res_gone.json()["detail"] == "Resource not found"


async def test_crud_router_bulk_operations(
    router_client: AsyncClient,
) -> None:
    """Verify bulk create, bulk trash, restore, and permanent delete."""
    tag = uuid.uuid4().hex[:6]
    bulk_payload = [{"name": f"Bulk {tag} A"}, {"name": f"Bulk {tag} B"}]

    # 1. Bulk create
    res_create = await router_client.post("/items/bulk", json=bulk_payload)
    assert res_create.status_code == 201
    assert res_create.json()["count"] == 2

    # Fetch created IDs
    res_page = await router_client.get(f"/items/?search={tag}")
    ids = [item["id"] for item in res_page.json()["data"]]
    assert len(ids) == 2

    # 2. Bulk soft-delete
    res_trash = await router_client.post("/items/bulk/trash", json={"ids": ids})
    assert res_trash.status_code == 200
    assert res_trash.json()["count"] == 2

    # 3. Bulk restore 1 item
    res_restore = await router_client.post(
        "/items/bulk/restore", json={"ids": [ids[0]]}
    )
    assert res_restore.status_code == 200
    assert res_restore.json()["count"] == 1

    # 4. Bulk purge the trashed item
    res_purge = await router_client.request(
        "DELETE", "/items/bulk/permanent", json={"ids": [ids[1]]}
    )
    assert res_purge.status_code == 200
    assert res_purge.json()["count"] == 1

    # 5. Anti-DoS payload size guard (> 1000 items -> 422)
    too_many_items = [{"name": f"Item {i}"} for i in range(1001)]
    res_too_many = await router_client.post("/items/bulk", json=too_many_items)
    assert res_too_many.status_code == 422


async def test_crud_router_validation_and_404_errors(
    router_client: AsyncClient,
) -> None:
    """Verify 422 on invalid UUID/payload and 404 on missing entity."""
    # 1. Invalid UUID in path -> 422
    res_invalid_uuid = await router_client.get("/items/not-a-valid-uuid")
    assert res_invalid_uuid.status_code == 422

    # 2. Non-existent UUID -> 404 without leaking internal model class name
    non_existent = str(uuid.uuid4())
    res_not_found = await router_client.get(f"/items/{non_existent}")
    assert res_not_found.status_code == 404
    assert res_not_found.json()["detail"] == "Resource not found"
    assert "_TestItemModel" not in res_not_found.text

    # 3. Invalid payload in POST -> 422 (missing name)
    res_bad_post = await router_client.post("/items/", json={})
    assert res_bad_post.status_code == 422


async def test_crud_router_custom_max_bulk_limit(
    dbsession: AsyncSession, _engine: AsyncEngine
) -> None:
    """Verify create_crud_router respects custom max_bulk_limit parameter."""
    repo = BaseRepository(RouterTestItem, dbsession)
    service = BaseAuditService(repo)

    def _fake_scope() -> ScopeContext:
        return ScopeContext()

    router = create_crud_router(
        service_getter=lambda: service,
        schema_out=ItemOut,
        schema_create=ItemCreate,
        schema_update=ItemUpdate,
        prefix="/limited",
        max_bulk_limit=3,
        current_user_getter=lambda: None,
        scope_getter=_fake_scope,
    )
    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res_ok = await client.post(
            "/limited/bulk", json=[{"name": f"Item {i}"} for i in range(3)]
        )
        assert res_ok.status_code == 201

        res_fail = await client.post(
            "/limited/bulk", json=[{"name": f"Item {i}"} for i in range(4)]
        )
        assert res_fail.status_code == 422
