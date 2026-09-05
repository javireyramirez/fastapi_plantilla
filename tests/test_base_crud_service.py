import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException, status
from sqlalchemy import String, Uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import (
    ListQueryParams,
    PaginationParams,
    ScopeContext,
    ScopeType,
    SortOrder,
)
from fastapi_plantilla.core.crud.service import (
    BaseAuditService,
    BaseCRUDService,
    BaseOwnedService,
)
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    OwnedMixin,
    RecordStatus,
    UUID7PrimaryKeyMixin,
)
from fastapi_plantilla.modules.auth.models import User


async def test_base_crud_service_get_by_id_and_404(dbsession: AsyncSession) -> None:
    """Test get_by_id returns model or raises 404 HTTPException."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    # 1. Create a user
    user = await repo.create(
        {
            "name": "Service User",
            "email": f"service_{uuid.uuid4().hex[:6]}@example.com",
            "is_active": True,
        }
    )

    # 2. Get by ID (success)
    fetched = await service.get_by_id(user.id)
    assert fetched.id == user.id
    assert fetched.name == "Service User"

    # 3. Non-existent ID raises 404
    non_existent_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc_info:
        await service.get_by_id(non_existent_id)
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


async def test_base_crud_service_find_paginated_flow(
    dbsession: AsyncSession,
) -> None:
    """Test find_paginated with search and ordering."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    tag = uuid.uuid4().hex[:6]
    users_data = [
        {
            "name": f"Searchable {tag} {i}",
            "email": f"search_{tag}_{i}@example.com",
            "is_active": True,
        }
        for i in range(5)
    ]
    await repo.create_many(users_data)

    # Test search filter
    params = PaginationParams(page=1, limit=2, search=tag, sort_order=SortOrder.ASC)
    response = await service.find_paginated(params)

    assert len(response.data) == 2
    assert response.meta.total == 5
    assert response.meta.total_pages == 3
    assert response.meta.has_next is True
    assert response.meta.has_prev is False


def test_base_crud_service_filter_helpers(dbsession: AsyncSession) -> None:
    """Test individual query building helper methods."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    # String filter
    str_f = service.build_string_filter("name", "Alice")
    assert str_f is not None

    # Multi search filter
    multi_f = service.build_multi_search_filter(["name", "email"], "query")
    assert multi_f is not None

    # Date range filter with end-of-day adjustment
    now = datetime.now(UTC)
    date_f = service.build_date_range_filter("created_at", now, now)
    assert len(date_f) == 2

    # Boolean filter
    bool_f = service.build_boolean_filter("is_active", "true")
    assert bool_f is not None

    # Null filter
    null_f = service.build_null_filter("image", True)
    assert null_f is not None

    # Non-existent column returns None or empty
    assert service.build_string_filter("non_existent", "val") is None
    assert service.build_date_range_filter("non_existent", now, now) == []


async def test_base_crud_service_find_list_flow(dbsession: AsyncSession) -> None:
    """Test find_list returns lightweight ListItemResponse objects."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    tag = uuid.uuid4().hex[:6]
    users_data = [
        {
            "name": f"Dropdown {tag} {i}",
            "email": f"dropdown_{tag}_{i}@example.com",
            "is_active": True,
        }
        for i in range(3)
    ]
    await repo.create_many(users_data)

    from fastapi_plantilla.core.crud.schema import ListQueryParams

    params = ListQueryParams(
        limit=10, search=tag, sort_by="name", sort_order=SortOrder.ASC
    )
    items = await service.find_list(params)

    assert len(items) == 3
    assert all(tag in item.name for item in items)
    assert items[0].id is not None

    # Test custom display_field (e.g. showing email instead of name)
    email_items = await service.find_list(params, display_field="email")
    assert len(email_items) == 3
    assert all("@example.com" in item.name for item in email_items)


async def test_base_crud_service_write_operations(dbsession: AsyncSession) -> None:
    """Test create, update, and delete operations via BaseCRUDService."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    # 1. Create
    email = f"writer_{uuid.uuid4().hex[:6]}@example.com"
    created = await service.create({"name": "Writer", "email": email})
    assert created.id is not None
    assert created.name == "Writer"

    # 2. Update
    updated = await service.update(created.id, {"name": "Writer Updated"})
    assert updated.name == "Writer Updated"

    # 3. Update non-existent raises 404
    with pytest.raises(HTTPException) as exc_info:
        await service.update(uuid.uuid4(), {"name": "Nobody"})
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    # 4. Delete
    deleted = await service.delete(created.id)
    assert deleted.id == created.id

    # 5. Delete non-existent raises 404
    with pytest.raises(HTTPException) as exc_del:
        await service.delete(created.id)
    assert exc_del.value.status_code == status.HTTP_404_NOT_FOUND


async def test_base_crud_service_bulk_operations(dbsession: AsyncSession) -> None:
    """Test bulk_create and bulk_delete on BaseCRUDService."""
    repo = BaseRepository(User, dbsession)
    service = BaseCRUDService(repo)

    from fastapi_plantilla.core.crud.schema import BulkIdsRequest

    tag = uuid.uuid4().hex[:6]
    items = [
        {"name": f"Bulk {tag} {i}", "email": f"bulk_srv_{tag}_{i}@example.com"}
        for i in range(4)
    ]
    create_res = await service.bulk_create(items)
    assert create_res.count == 4

    # Fetch created IDs
    users = await repo.find_many(User.email.like(f"%{tag}%"))
    assert len(users) == 4
    user_ids = [u.id for u in users]

    # Bulk delete
    del_res = await service.bulk_delete(BulkIdsRequest(ids=user_ids))
    assert del_res.count == 4


class SampleAuditItem(
    Base, UUID7PrimaryKeyMixin, AuditFieldsMixin, OptimisticLockMixin
):
    """Temporary test model for BaseAuditService testing."""

    __tablename__ = "sample_audit_items"
    name: Mapped[str] = mapped_column(String(100))


async def test_base_audit_service_actor_stamping_and_filters(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test BaseAuditService actor stamping and trash filtering."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleAuditItem.metadata.create_all)

    repo = BaseRepository(SampleAuditItem, dbsession)
    service = BaseAuditService(repo)

    # 1. Create with user_id stamps both created_by and updated_by
    item = await service.create({"name": "Audit Item 1"}, user_id="user_admin")
    assert item.created_by == "user_admin"
    assert item.updated_by == "user_admin"
    assert item.status == RecordStatus.ACTIVE

    # 2. Update with user_id stamps updated_by while keeping created_by
    updated = await service.update(
        item.id, {"name": "Audit Item Renamed"}, user_id="user_editor"
    )
    assert updated.name == "Audit Item Renamed"
    assert updated.created_by == "user_admin"
    assert updated.updated_by == "user_editor"

    # 3. Soft delete simulation (TRASHED)
    updated.status = RecordStatus.TRASHED
    await dbsession.flush()

    # Active queries exclude trashed by default
    active_res = await service.find_paginated(PaginationParams(limit=50))
    assert all(i.id != item.id for i in active_res.data)

    # is_trash=True queries return trashed records
    trash_res = await service.find_paginated(PaginationParams(is_trash=True, limit=50))
    assert any(i.id == item.id for i in trash_res.data)


async def test_base_audit_service_lifecycle_operations(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test trash, restore, and permanent_delete operations on BaseAuditService."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleAuditItem.metadata.create_all)

    repo = BaseRepository(SampleAuditItem, dbsession)
    service = BaseAuditService(repo)

    # 1. Create active item
    item = await service.create({"name": "Lifecycle Item"}, user_id="creator")
    assert item.status == RecordStatus.ACTIVE

    # 2. Permanent delete on active item FAILS with 400 Bad Request
    with pytest.raises(HTTPException) as exc_active:
        await service.permanent_delete(item.id)
    assert exc_active.value.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Trash (soft delete)
    trashed = await service.trash(item.id, user_id="trasher")
    assert trashed.status == RecordStatus.TRASHED
    assert trashed.deleted_by == "trasher"
    assert trashed.deleted_at is not None

    # 4. Restore
    restored = await service.restore(item.id, user_id="restorer")
    assert restored.status == RecordStatus.ACTIVE
    assert restored.restored_by == "restorer"
    assert restored.restored_at is not None

    # 5. Move to trash again and permanent delete succeeds
    await service.trash(item.id, user_id="trasher")
    deleted = await service.permanent_delete(item.id)
    assert deleted.id == item.id

    # 6. Subsequent access raises 404
    with pytest.raises(HTTPException) as exc_info:
        await service.get_by_id(item.id)
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


async def test_base_audit_service_bulk_lifecycle_operations(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test bulk_trash, bulk_restore, and bulk_permanent_delete on BaseAuditService."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleAuditItem.metadata.create_all)

    repo = BaseRepository(SampleAuditItem, dbsession)
    service = BaseAuditService(repo)

    from fastapi_plantilla.core.crud.schema import BulkIdsRequest

    tag = uuid.uuid4().hex[:6]
    items_data = [{"name": f"BulkAudit {tag} {i}"} for i in range(3)]
    await service.bulk_create(items_data)

    items = await repo.find_many(SampleAuditItem.name.like(f"%{tag}%"))
    ids = [item.id for item in items]
    assert len(ids) == 3

    # Bulk trash all 3
    trash_res = await service.bulk_trash(
        BulkIdsRequest(ids=ids), user_id="bulk_trasher"
    )
    assert trash_res.count == 3

    # Bulk restore only 1 item (so 1 is ACTIVE, 2 are TRASHED)
    active_id = ids[0]
    await service.bulk_restore(BulkIdsRequest(ids=[active_id]), user_id="bulk_restorer")

    # Bulk permanent delete called with ALL 3 ids
    # Safety guard: only the 2 TRASHED items should be deleted,
    # the ACTIVE one must remain!
    del_res = await service.bulk_permanent_delete(BulkIdsRequest(ids=ids))
    assert del_res.count == 2

    # The active record must still exist!
    active_item = await service.get_by_id(active_id)
    assert active_item.id == active_id
    assert active_item.status == RecordStatus.ACTIVE


class SampleOwnedItem(
    Base, UUID7PrimaryKeyMixin, AuditFieldsMixin, OwnedMixin, OptimisticLockMixin
):
    """Temporary test model for BaseOwnedService testing."""

    __tablename__ = "sample_owned_items"
    name: Mapped[str] = mapped_column(String(100))
    team_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, default=None)


async def test_base_owned_service_create_and_bulk_create(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test owner assignment on create and bulk_create."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "Owner 1", "email": f"o1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "Owner 2", "email": f"o2_{uuid.uuid4().hex[:6]}@example.com"}
    )

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    # 1. Create with user_id auto-assigns owner_id
    item1 = await service.create({"name": "Owned by U1"}, user_id=u1.id)
    assert item1.owner_id == u1.id
    assert item1.created_by == str(u1.id)

    # 2. Create with explicit owner_id overrides user_id
    item2 = await service.create({"name": "Owned by U2"}, user_id=u1.id, owner_id=u2.id)
    assert item2.owner_id == u2.id
    assert item2.created_by == str(u1.id)

    # 3. Bulk create auto-assigns owner_id
    res = await service.bulk_create(
        [{"name": "Bulk Owned 1"}, {"name": "Bulk Owned 2"}],
        user_id=u1.id,
    )
    assert res.count == 2
    bulk_items = await repo.find_many(SampleOwnedItem.name.like("Bulk Owned%"))
    assert all(i.owner_id == u1.id for i in bulk_items)


async def test_base_owned_service_read_scope_protection(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test get_by_id, find_paginated, and find_list with RBAC scope context."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "Owner A", "email": f"oa_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "Owner B", "email": f"ob_{uuid.uuid4().hex[:6]}@example.com"}
    )

    team1_id = uuid.uuid4()
    team2_id = uuid.uuid4()

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    # Setup: item1 (u1, team1), item2 (u2, team2)
    tag = uuid.uuid4().hex[:6]
    item1 = await service.create(
        {"name": f"ReadScope_{tag}_1", "team_id": team1_id}, user_id=u1.id
    )
    item2 = await service.create(
        {"name": f"ReadScope_{tag}_2", "team_id": team2_id}, user_id=u2.id
    )

    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)
    scope_global = ScopeContext(scope=ScopeType.GLOBAL)

    # 1. get_by_id checks
    with pytest.raises(HTTPException) as exc_no_scope:
        await service.get_by_id(item1.id)
    assert exc_no_scope.value.status_code == status.HTTP_403_FORBIDDEN

    # u1 can get item1
    fetched1 = await service.get_by_id(item1.id, scope=scope_u1)
    assert fetched1.id == item1.id

    # u1 cannot get item2 -> 404 Not Found (masked by default for anti-enumeration)
    with pytest.raises(HTTPException) as exc_404_alien:
        await service.get_by_id(item2.id, scope=scope_u1)
    assert exc_404_alien.value.status_code == status.HTTP_404_NOT_FOUND

    # With masking disabled, it differentiates 403 Forbidden
    service.mask_forbidden_as_not_found = False
    with pytest.raises(HTTPException) as exc_403:
        await service.get_by_id(item2.id, scope=scope_u1)
    assert exc_403.value.status_code == status.HTTP_403_FORBIDDEN

    # non-existent record -> 404 Not Found
    with pytest.raises(HTTPException) as exc_404:
        await service.get_by_id(uuid.uuid4(), scope=scope_u1)
    assert exc_404.value.status_code == status.HTTP_404_NOT_FOUND

    # global scope gets any item
    fetched2 = await service.get_by_id(item2.id, scope=scope_global)
    assert fetched2.id == item2.id


async def test_base_owned_service_find_and_list_scope_protection(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test find_paginated and find_list RBAC scope context isolation."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "Owner A", "email": f"oa2_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "Owner B", "email": f"ob2_{uuid.uuid4().hex[:6]}@example.com"}
    )
    team1_id = uuid.uuid4()
    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    tag = uuid.uuid4().hex[:6]
    item1 = await service.create(
        {"name": f"FindScope_{tag}_1", "team_id": team1_id}, user_id=u1.id
    )
    item2 = await service.create(
        {"name": f"FindScope_{tag}_2", "team_id": None}, user_id=u2.id
    )

    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)
    scope_u2 = ScopeContext(scope=ScopeType.OWN, user_id=u2.id)
    scope_team1 = ScopeContext(scope=ScopeType.TEAM, user_id=u1.id, team_ids=[team1_id])
    scope_global = ScopeContext(scope=ScopeType.GLOBAL)

    # 2. find_paginated checks
    params = PaginationParams(page=1, limit=10, search=tag)

    with pytest.raises(HTTPException) as exc_find_no_scope:
        await service.find_paginated(params)
    assert exc_find_no_scope.value.status_code == status.HTTP_403_FORBIDDEN

    res_u1 = await service.find_paginated(params, scope=scope_u1)
    assert len(res_u1.data) == 1
    assert res_u1.data[0].id == item1.id

    res_team1 = await service.find_paginated(params, scope=scope_team1)
    assert len(res_team1.data) == 1
    assert res_team1.data[0].id == item1.id

    res_global = await service.find_paginated(params, scope=scope_global)
    assert len(res_global.data) == 2

    # 3. find_list checks
    list_params = ListQueryParams(limit=10, search=tag)
    list_u2 = await service.find_list(list_params, scope=scope_u2)
    assert len(list_u2) == 1
    assert list_u2[0].id == item2.id


async def test_base_owned_service_teammate_peer_scope(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test TEAM scope allows teammates to access records without team_id."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "Teammate 1", "email": f"tm1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "Teammate 2", "email": f"tm2_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u3_outsider = await user_repo.create(
        {"name": "Outsider", "email": f"out_{uuid.uuid4().hex[:6]}@example.com"}
    )

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    # U2 creates an item with NO team_id (team_id is None)
    item_u2 = await service.create(
        {"name": "U2 Document", "team_id": None}, user_id=u2.id
    )
    assert item_u2.team_id is None
    assert item_u2.owner_id == u2.id

    # U1 has TEAM scope and shares team with U2 (teammate_ids = [u1.id, u2.id])
    scope_u1_team = ScopeContext(
        scope=ScopeType.TEAM, user_id=u1.id, teammate_ids=[u1.id, u2.id]
    )

    # U1 CAN read U2's record because they are teammates!
    fetched = await service.get_by_id(item_u2.id, scope=scope_u1_team)
    assert fetched.id == item_u2.id

    # Outsider has TEAM scope but does not share team with U2 (teammate_ids = [u3.id])
    scope_u3_team = ScopeContext(
        scope=ScopeType.TEAM, user_id=u3_outsider.id, teammate_ids=[u3_outsider.id]
    )

    # Outsider CANNOT read U2's record -> 404 Not Found (masked by default)
    with pytest.raises(HTTPException) as exc_404_out:
        await service.get_by_id(item_u2.id, scope=scope_u3_team)
    assert exc_404_out.value.status_code == status.HTTP_404_NOT_FOUND


async def test_base_owned_service_write_and_lifecycle_scope_protection(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test update, delete, trash, restore, and permanent_delete scope boundaries."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "WriteOwner 1", "email": f"wo1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "WriteOwner 2", "email": f"wo2_{uuid.uuid4().hex[:6]}@example.com"}
    )

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)
    scope_u2 = ScopeContext(scope=ScopeType.OWN, user_id=u2.id)

    # Item owned by U1
    item = await service.create({"name": "Initial Name"}, user_id=u1.id)

    # 1. Update: missing scope raises 403 Forbidden
    with pytest.raises(HTTPException) as exc_upd_no_scope:
        await service.update(item.id, {"name": "No Scope"})
    assert exc_upd_no_scope.value.status_code == status.HTTP_403_FORBIDDEN

    # U2 tries to update U1's item -> 404 (masked as not found)
    with pytest.raises(HTTPException) as exc_upd:
        await service.update(item.id, {"name": "Hacked"}, scope=scope_u2)
    assert exc_upd.value.status_code == status.HTTP_404_NOT_FOUND

    # U1 updates successfully
    updated = await service.update(item.id, {"name": "Updated by U1"}, scope=scope_u1)
    assert updated.name == "Updated by U1"

    # 2. Trash (soft delete): U2 tries to trash U1's item -> 404
    with pytest.raises(HTTPException) as exc_trash:
        await service.trash(item.id, user_id=u2.id, scope=scope_u2)
    assert exc_trash.value.status_code == status.HTTP_404_NOT_FOUND

    # U1 trashes successfully
    trashed = await service.trash(item.id, user_id=u1.id, scope=scope_u1)
    assert trashed.status == RecordStatus.TRASHED

    # 3. Restore: U2 tries to restore U1's item -> 404
    with pytest.raises(HTTPException) as exc_res:
        await service.restore(item.id, user_id=u2.id, scope=scope_u2)
    assert exc_res.value.status_code == status.HTTP_404_NOT_FOUND

    # U1 restores successfully
    restored = await service.restore(item.id, user_id=u1.id, scope=scope_u1)
    assert restored.status == RecordStatus.ACTIVE

    # 4. Permanent delete: trash first, then test scope
    await service.trash(item.id, user_id=u1.id, scope=scope_u1)

    with pytest.raises(HTTPException) as exc_pdel:
        await service.permanent_delete(item.id, scope=scope_u2)
    assert exc_pdel.value.status_code == status.HTTP_404_NOT_FOUND

    # U1 permanent delete succeeds
    perm_del = await service.permanent_delete(item.id, scope=scope_u1)
    assert perm_del.id == item.id

    # Item is gone
    with pytest.raises(HTTPException) as exc_404:
        await service.get_by_id(item.id, scope=scope_u1)
    assert exc_404.value.status_code == status.HTTP_404_NOT_FOUND


async def test_base_owned_service_bulk_operations_scope_protection(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Test bulk_trash, bulk_restore, and bulk_delete with scope restriction."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "BulkOwner 1", "email": f"bo1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "BulkOwner 2", "email": f"bo2_{uuid.uuid4().hex[:6]}@example.com"}
    )

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    from fastapi_plantilla.core.crud.schema import BulkIdsRequest

    # Create 2 items for U1 and 2 items for U2
    u1_items = [
        await service.create({"name": f"U1_item_{i}"}, user_id=u1.id) for i in range(2)
    ]
    u2_items = [
        await service.create({"name": f"U2_item_{i}"}, user_id=u2.id) for i in range(2)
    ]
    all_ids = [it.id for it in u1_items + u2_items]

    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)

    # 1. Bulk trash with U1 scope: only U1's 2 items get trashed!
    trash_res = await service.bulk_trash(
        BulkIdsRequest(ids=all_ids), user_id=u1.id, scope=scope_u1
    )
    assert trash_res.count == 2

    scope_u2 = ScopeContext(scope=ScopeType.OWN, user_id=u2.id)

    # U2's items are still active
    for it in u2_items:
        check = await service.get_by_id(it.id, scope=scope_u2)
        assert check.status == RecordStatus.ACTIVE

    # 2. Bulk restore with U1 scope: only U1's 2 items get restored
    restore_res = await service.bulk_restore(
        BulkIdsRequest(ids=all_ids), user_id=u1.id, scope=scope_u1
    )
    assert restore_res.count == 2

    # 3. Bulk delete with U1 scope: only U1's 2 items get soft-deleted (moved to trash)
    del_res = await service.bulk_delete(BulkIdsRequest(ids=all_ids), scope=scope_u1)
    assert del_res.count == 2
    for it in u1_items:
        check = await service.get_by_id(it.id, scope=scope_u1)
        assert check.status == RecordStatus.TRASHED

    # 4. Bulk permanent delete with U1 scope: only U1 items permanently deleted
    perm_res = await service.bulk_permanent_delete(
        BulkIdsRequest(ids=all_ids), scope=scope_u1
    )
    assert perm_res.count == 2

    # U1 items are permanently deleted
    for it in u1_items:
        with pytest.raises(HTTPException) as exc_404:
            await service.get_by_id(it.id, scope=scope_u1)
        assert exc_404.value.status_code == status.HTTP_404_NOT_FOUND

    # U2 items still exist intact
    for it in u2_items:
        check = await service.get_by_id(it.id, scope=scope_u2)
        assert check.id == it.id
        assert check.status == RecordStatus.ACTIVE


async def test_security_ownership_and_state_guards(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Verify ownership transfer, claiming, and lifecycle state transition guards."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "User 1", "email": f"sec1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    u2 = await user_repo.create(
        {"name": "User 2", "email": f"sec2_{uuid.uuid4().hex[:6]}@example.com"}
    )

    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)

    # 1. Mass assignment & Ownership spoofing protection on CREATE
    spoofed_create = await service.create(
        {"name": "Spoofed Item", "owner_id": u2.id}, user_id=u1.id
    )
    assert spoofed_create.owner_id == u1.id

    # Superadmin CAN assign explicit owner_id
    admin_scope = ScopeContext(scope=ScopeType.GLOBAL, is_super_admin=True)
    admin_create = await service.create(
        {"name": "Admin Item", "owner_id": u2.id},
        user_id=u1.id,
        scope=admin_scope,
    )
    assert admin_create.owner_id == u2.id

    # 2. Ownership transfer validation on UPDATE
    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)
    with pytest.raises(HTTPException) as exc_unauthorized_owner:
        await service.update(
            spoofed_create.id,
            {"owner_id": u2.id},
            scope=scope_u1,
        )
    assert exc_unauthorized_owner.value.status_code == status.HTTP_403_FORBIDDEN

    # Self-assignment / Claiming: u1 assigning to u1 succeeds
    claimed = await service.update(
        spoofed_create.id,
        {"owner_id": u1.id, "id": uuid.uuid4()},
        scope=scope_u1,
    )
    assert claimed.owner_id == u1.id
    assert claimed.id == spoofed_create.id

    # Team reassignment: user with TEAM scope sharing team with u2 can reassign to u2
    scope_team = ScopeContext(
        scope=ScopeType.TEAM, user_id=u1.id, teammate_ids=[u1.id, u2.id]
    )
    team_reassigned = await service.update(
        spoofed_create.id,
        {"owner_id": u2.id},
        scope=scope_team,
    )
    assert team_reassigned.owner_id == u2.id

    # 3. Trashed record freeze: cannot update trashed records
    await service.trash(spoofed_create.id, user_id=u1.id, scope=scope_team)
    with pytest.raises(HTTPException) as exc_trash_edit:
        await service.update(
            spoofed_create.id, {"name": "Hacked Trash"}, scope=scope_team
        )
    assert exc_trash_edit.value.status_code == status.HTTP_400_BAD_REQUEST

    # 4. Trashing already trashed record raises 400
    with pytest.raises(HTTPException) as exc_retrash:
        await service.trash(spoofed_create.id, user_id=u1.id, scope=scope_team)
    assert exc_retrash.value.status_code == status.HTTP_400_BAD_REQUEST

    # 5. Restoring active record raises 400
    active_item = await service.create({"name": "Active Item"}, user_id=u1.id)
    with pytest.raises(HTTPException) as exc_active_restore:
        await service.restore(active_item.id, user_id=u1.id, scope=scope_u1)
    assert exc_active_restore.value.status_code == status.HTTP_400_BAD_REQUEST


async def test_security_input_sanitization_and_boundaries(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Verify input filtering, empty payload safety, mass assignment, and DoS bounds."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "User 1", "email": f"sec1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)
    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)

    # 6. Sensitive columns & non-column injection blocked
    crud_user_service = BaseCRUDService(user_repo)
    assert crud_user_service.build_string_filter("hashed_password", "secret") is None
    assert crud_user_service.build_order_by("hashed_password") is None
    assert crud_user_service.build_string_filter("metadata", "val") is None
    assert crud_user_service.build_in_filter("name", ["x"] * 1001) is None

    # 7. Anti-enumeration masking (mask_forbidden_as_not_found)
    u2 = await user_repo.create(
        {"name": "User 2", "email": f"sec2_{uuid.uuid4().hex[:6]}@example.com"}
    )
    other_item = await service.create(
        {"name": "Other Item"},
        user_id=u2.id,
        scope=ScopeContext(scope=ScopeType.GLOBAL, is_super_admin=True),
    )
    service.mask_forbidden_as_not_found = True
    with pytest.raises(HTTPException) as exc_masked:
        await service.get_by_id(other_item.id, scope=scope_u1)
    assert exc_masked.value.status_code == status.HTTP_404_NOT_FOUND

    # 8. Anti-DoS limit on bulk_create
    with pytest.raises(HTTPException) as exc_bulk_limit:
        await service.bulk_create(
            [{"name": f"i{i}"} for i in range(1001)], user_id=u1.id
        )
    assert exc_bulk_limit.value.status_code == status.HTTP_400_BAD_REQUEST

    # 9. Empty Payload Update Guard (Prevents Crash DoS / syntax error)
    item = await service.create({"name": "Safe Item"}, user_id=u1.id)
    empty_res = await service.update(item.id, {}, scope=scope_u1)
    assert empty_res.id == item.id
    assert empty_res.name == "Safe Item"

    immutable_only = await service.update(item.id, {"id": uuid.uuid4()}, scope=scope_u1)
    assert immutable_only.id == item.id

    with pytest.raises(HTTPException) as exc_conflict:
        await service.update(item.id, {}, scope=scope_u1, expected_version=999)
    assert exc_conflict.value.status_code == status.HTTP_409_CONFLICT


async def test_security_mass_assignment_and_tampering(
    dbsession: AsyncSession, _engine: Any
) -> None:
    """Verify mass assignment purge, audit overwrite, team tampering, soft-delete."""
    async with _engine.begin() as conn:
        await conn.run_sync(SampleOwnedItem.metadata.create_all)

    user_repo = BaseRepository(User, dbsession)
    u1 = await user_repo.create(
        {"name": "User 1", "email": f"sec1_{uuid.uuid4().hex[:6]}@example.com"}
    )
    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)
    scope_u1 = ScopeContext(scope=ScopeType.OWN, user_id=u1.id)

    # 10. Mass Assignment in Create (immutable fields and status forced to ACTIVE)
    fake_id = uuid.uuid4()
    fake_time = datetime(2020, 1, 1, tzinfo=UTC)
    mass_create = await service.create(
        {
            "name": "Mass Item",
            "id": fake_id,
            "created_at": fake_time,
            "deleted_at": fake_time,
            "version": 99,
            "status": RecordStatus.TRASHED,
        },
        user_id=u1.id,
    )
    assert mass_create.id != fake_id
    assert mass_create.created_at != fake_time
    assert mass_create.deleted_at is None
    assert mass_create.version == 1
    assert mass_create.status == RecordStatus.ACTIVE

    # 11. Audit Spoofing in Bulk Create (unconditional overwrite)
    fake_actor = "evil_hacker"
    spoofed_bulk = await service.bulk_create(
        [
            {
                "name": "Bulk Spoof",
                "created_by": fake_actor,
                "updated_by": fake_actor,
            }
        ],
        user_id=u1.id,
    )
    assert spoofed_bulk.count == 1
    bulk_item = await repo.find_first(SampleOwnedItem.name == "Bulk Spoof")
    assert bulk_item is not None
    assert bulk_item.created_by == str(u1.id)
    assert bulk_item.updated_by == str(u1.id)
    assert bulk_item.status == RecordStatus.ACTIVE

    # 12. Cross-Tenant Team Tampering (create, update, bulk_create with alien team)
    alien_team = uuid.uuid4()
    my_team = uuid.uuid4()
    user_team_scope = ScopeContext(
        scope=ScopeType.TEAM, user_id=u1.id, team_ids=[my_team]
    )

    with pytest.raises(HTTPException) as exc_team_create:
        await service.create(
            {"name": "Alien Team Item", "team_id": alien_team},
            user_id=u1.id,
            scope=user_team_scope,
        )
    assert exc_team_create.value.status_code == status.HTTP_403_FORBIDDEN

    my_team_item = await service.create(
        {"name": "My Team Item", "team_id": my_team},
        user_id=u1.id,
        scope=user_team_scope,
    )
    assert my_team_item.team_id == my_team

    with pytest.raises(HTTPException) as exc_team_update:
        await service.update(
            my_team_item.id, {"team_id": alien_team}, scope=user_team_scope
        )
    assert exc_team_update.value.status_code == status.HTTP_403_FORBIDDEN

    with pytest.raises(HTTPException) as exc_team_bulk:
        await service.bulk_create(
            [{"name": "Bulk Alien", "team_id": alien_team}],
            user_id=u1.id,
            scope=user_team_scope,
        )
    assert exc_team_bulk.value.status_code == status.HTTP_403_FORBIDDEN

    # 13. Soft-Delete Bypass Guard (delete() safely trashes on BaseAuditService)
    soft_del_item = await service.create({"name": "Soft Del Guard"}, user_id=u1.id)
    del_result = await service.delete(soft_del_item.id, scope=scope_u1)
    assert del_result.status == RecordStatus.TRASHED
    assert del_result.deleted_at is not None


def test_security_scope_fail_closed_and_enum(dbsession: AsyncSession) -> None:
    """Verify ScopeType enum rejects arbitrary strings and scope filters fail-closed."""
    from pydantic import ValidationError
    from sqlalchemy.sql.elements import False_

    from fastapi_plantilla.core.crud.schema import ScopeType

    # Arbitrary strings rejected by Pydantic ScopeType
    with pytest.raises(ValidationError):
        ScopeContext(scope="HACKER_SCOPE")  # type: ignore[arg-type]

    # Valid enum scopes
    assert ScopeContext(scope=ScopeType.GLOBAL).scope == ScopeType.GLOBAL
    assert ScopeContext(scope=ScopeType.TEAM).scope == ScopeType.TEAM
    assert ScopeContext(scope=ScopeType.OWN).scope == ScopeType.OWN

    # Fail-closed check if an unknown scope somehow bypassed schema
    repo = BaseRepository(SampleOwnedItem, dbsession)
    service = BaseOwnedService(repo)
    fake_scope = ScopeContext(scope=ScopeType.GLOBAL)
    fake_scope.scope = "UNRECOGNIZED"  # type: ignore[assignment]
    filters = service.build_scope_filters(fake_scope)
    assert len(filters) == 1
    assert isinstance(filters[0], False_)
