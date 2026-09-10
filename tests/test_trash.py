import asyncio
import io
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.dependencies import (
    get_scope_context,
    get_write_options,
)
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import (
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus, generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.storage.dependencies import (
    get_storage_provider,
    set_storage_provider_override,
)
from fastapi_plantilla.modules.storage.models import Document
from fastapi_plantilla.modules.storage.providers import LocalStorageProvider
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.routes import router as trash_router
from fastapi_plantilla.modules.trash.tasks import (
    purge_expired_trash,
    run_periodic_trash_purge,
)


def user_to_response(user: User) -> UserResponse:
    """Map DB User model to Pydantic UserResponse."""
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        is_active=user.is_active,
        is_super_admin=user.is_super_admin,
        is_system=user.is_system,
        email_verified=user.email_verified,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class AuthContextState:
    """Holder for current user context in tests."""

    def __init__(self, user: UserResponse) -> None:
        self.user = user


@pytest.fixture
async def test_users(dbsession: AsyncSession) -> tuple[UserResponse, UserResponse]:
    """Create and persist admin and regular users in database."""
    user_repo = BaseRepository(User, dbsession)
    admin = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Admin User",
            "email": f"admin_trash_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular User",
            "email": f"regular_trash_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    return user_to_response(admin), user_to_response(regular)


@pytest.fixture
def auth_state(test_users: tuple[UserResponse, UserResponse]) -> AuthContextState:
    """Provide mutable auth state initialized to admin user."""
    admin, _ = test_users
    return AuthContextState(admin)


@pytest.fixture
def local_storage(tmp_path: Path) -> Generator[LocalStorageProvider, None, None]:
    """Provide LocalStorageProvider pointed at temporary directory."""
    provider = LocalStorageProvider(
        base_path=tmp_path,
        secret="test-auth-secret-key-trash-9999",  # noqa: S106
        base_url="http://test",
    )
    set_storage_provider_override(provider)
    yield provider
    set_storage_provider_override(None)


@pytest.fixture
def app(
    dbsession: AsyncSession,
    local_storage: LocalStorageProvider,
    auth_state: AuthContextState,
) -> FastAPI:
    """Configure test FastAPI application with storage and trash routers."""
    test_app = FastAPI()
    test_app.include_router(storage_router, prefix="/api")
    test_app.include_router(trash_router, prefix="/api")

    test_app.dependency_overrides[get_db_session] = lambda: dbsession
    test_app.dependency_overrides[get_storage_provider] = lambda: local_storage
    test_app.dependency_overrides[get_current_user] = lambda: auth_state.user

    def _scope_override() -> ScopeContext:
        user = auth_state.user
        return ScopeContext(
            scope=ScopeType.GLOBAL if user.is_super_admin else ScopeType.OWN,
            user_id=user.id,
            is_super_admin=user.is_super_admin,
        )

    def _write_options_override() -> WriteOptions:
        user = auth_state.user
        scope = _scope_override()
        return WriteOptions(
            user_id=user.id,
            scope=scope,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )

    test_app.dependency_overrides[get_scope_context] = _scope_override
    test_app.dependency_overrides[get_write_options] = _write_options_override

    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    """AsyncClient wired to the test app."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.anyio
async def test_soft_delete_creates_trash_item(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify soft-deleting a document automatically populates sys_trash_bin."""
    entity_id = uuid.uuid4()
    file_bytes = b"Sample document contents for trash bin test"

    # 1. Upload a document
    res = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("report_q1.pdf", io.BytesIO(file_bytes), "application/pdf")},
        data={"entity_type": "invoice", "entity_id": str(entity_id)},
    )
    assert res.status_code == 201
    doc_id = uuid.UUID(res.json()["id"])

    # 2. Soft delete the document
    del_res = await client.delete(f"/api/storage/documents/{doc_id}")
    assert del_res.status_code == 200

    # 3. Query sys_trash_bin to verify sync hook created the item
    trash_repo = TrashRepository(dbsession)
    trash_item = await trash_repo.get_by_entity("document", doc_id)
    assert trash_item is not None
    assert trash_item.name == "report_q1.pdf"
    assert trash_item.entity_id == doc_id
    assert trash_item.entity_type == "document"
    assert trash_item.expires_at > datetime.now(UTC)


@pytest.mark.anyio
async def test_trash_list_and_filters(
    client: AsyncClient,
) -> None:
    """Verify GET /api/trash pagination, filtering and search."""
    entity_id = uuid.uuid4()

    # Upload and soft-delete two distinct documents
    res1 = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("tax_2025.pdf", io.BytesIO(b"tax data"), "application/pdf")},
        data={"entity_type": "finance", "entity_id": str(entity_id)},
    )
    doc1_id = res1.json()["id"]

    res2 = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("readme.txt", io.BytesIO(b"readme text"), "text/plain")},
        data={"entity_type": "project", "entity_id": str(entity_id)},
    )
    doc2_id = res2.json()["id"]

    await client.delete(f"/api/storage/documents/{doc1_id}")
    await client.delete(f"/api/storage/documents/{doc2_id}")

    # List all trash
    list_res = await client.get("/api/trash")
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["meta"]["total"] >= 2

    # Filter by entity_type
    filtered_res = await client.get("/api/trash?entity_type=document")
    assert filtered_res.status_code == 200
    assert filtered_res.json()["meta"]["total"] >= 2

    # Search keyword
    search_res = await client.get("/api/trash?q=tax_2025")
    assert search_res.status_code == 200
    search_items = search_res.json()["data"]
    assert len(search_items) == 1
    assert search_items[0]["name"] == "tax_2025.pdf"


@pytest.mark.anyio
async def test_trash_get_single_item(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify GET /api/trash/{id} retrieves trash item and 404s for unknown IDs."""
    entity_id = uuid.uuid4()
    res = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("contract.pdf", io.BytesIO(b"contract"), "application/pdf")},
        data={"entity_type": "vendor", "entity_id": str(entity_id)},
    )
    doc_id = uuid.UUID(res.json()["id"])
    await client.delete(f"/api/storage/documents/{doc_id}")

    trash_repo = TrashRepository(dbsession)
    trash_item = await trash_repo.get_by_entity("document", doc_id)
    assert trash_item is not None

    get_res = await client.get(f"/api/trash/{trash_item.id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == str(trash_item.id)
    assert get_res.json()["name"] == "contract.pdf"

    # Non-existent ID returns 404
    missing_id = uuid.uuid4()
    not_found_res = await client.get(f"/api/trash/{missing_id}")
    assert not_found_res.status_code == 404


@pytest.mark.anyio
async def test_trash_restore_item(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify POST /api/trash/{id}/restore restores entity and cleans trash bin."""
    entity_id = uuid.uuid4()
    res = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("restore_me.txt", io.BytesIO(b"restore"), "text/plain")},
        data={"entity_type": "note", "entity_id": str(entity_id)},
    )
    doc_id = uuid.UUID(res.json()["id"])
    await client.delete(f"/api/storage/documents/{doc_id}")

    trash_repo = TrashRepository(dbsession)
    trash_item = await trash_repo.get_by_entity("document", doc_id)
    assert trash_item is not None

    # Call restore endpoint
    restore_res = await client.post(f"/api/trash/{trash_item.id}/restore")
    assert restore_res.status_code == 200

    # Trash item should be deleted from sys_trash_bin
    assert await trash_repo.get_by_entity("document", doc_id) is None

    # Document should now be ACTIVE again in storage
    doc_res = await client.get(f"/api/storage/documents/{doc_id}")
    assert doc_res.status_code == 200
    assert doc_res.json()["status"] == RecordStatus.ACTIVE


@pytest.mark.anyio
async def test_trash_purge_item(
    client: AsyncClient,
    dbsession: AsyncSession,
    local_storage: LocalStorageProvider,
) -> None:
    """Verify DELETE purge removes DB entity, file, and trash record."""
    entity_id = uuid.uuid4()
    file_bytes = b"Physical file bytes to be purged completely"
    res = await client.post(
        "/api/storage/documents/upload",
        files={
            "file": (
                "purge_me.bin",
                io.BytesIO(file_bytes),
                "application/octet-stream",
            )
        },
        data={"entity_type": "asset", "entity_id": str(entity_id)},
    )
    doc_data = res.json()
    doc_id = uuid.UUID(doc_data["id"])
    file_key = doc_data["file_key"]

    # Verify physical file exists initially
    assert await local_storage.exists(file_key)

    # Soft-delete the document
    await client.delete(f"/api/storage/documents/{doc_id}")

    trash_repo = TrashRepository(dbsession)
    trash_item = await trash_repo.get_by_entity("document", doc_id)
    assert trash_item is not None

    # Purge the item permanently
    purge_res = await client.delete(f"/api/trash/{trash_item.id}/purge")
    assert purge_res.status_code == 204

    # 1. Trash record must be removed
    assert await trash_repo.get_by_id(trash_item.id) is None

    # 2. Document record must be removed from DB
    doc_repo = BaseRepository(Document, dbsession)
    assert await doc_repo.get_by_id(doc_id) is None

    # 3. Physical file must be deleted from storage provider
    assert not await local_storage.exists(file_key)


@pytest.mark.anyio
async def test_bulk_restore_and_bulk_purge(
    client: AsyncClient,
    dbsession: AsyncSession,
    local_storage: LocalStorageProvider,
) -> None:
    """Verify batch restoration and batch purging operations."""
    entity_id = uuid.uuid4()

    # Upload 4 documents
    doc_ids = []
    file_keys = []
    for i in range(4):
        res = await client.post(
            "/api/storage/documents/upload",
            files={
                "file": (
                    f"bulk_{i}.txt",
                    io.BytesIO(f"content {i}".encode()),
                    "text/plain",
                )
            },
            data={"entity_type": "bulk_entity", "entity_id": str(entity_id)},
        )
        doc_ids.append(uuid.UUID(res.json()["id"]))
        file_keys.append(res.json()["file_key"])
        await client.delete(f"/api/storage/documents/{res.json()['id']}")

    trash_repo = TrashRepository(dbsession)
    trash_items = [
        await trash_repo.get_by_entity("document", doc_id) for doc_id in doc_ids
    ]
    trash_ids = [item.id for item in trash_items if item is not None]
    assert len(trash_ids) == 4

    # 1. Bulk restore first 2 items
    restore_res = await client.post(
        "/api/trash/bulk/restore",
        json={"ids": [str(trash_ids[0]), str(trash_ids[1])]},
    )
    assert restore_res.status_code == 200
    assert restore_res.json()["count"] == 2

    # Verify first 2 are active, remaining 2 still in trash
    assert await trash_repo.get_by_id(trash_ids[0]) is None
    assert await trash_repo.get_by_id(trash_ids[1]) is None
    assert await trash_repo.get_by_id(trash_ids[2]) is not None
    assert await trash_repo.get_by_id(trash_ids[3]) is not None

    # 2. Bulk purge remaining 2 items
    purge_res = await client.post(
        "/api/trash/bulk/purge",
        json={"ids": [str(trash_ids[2]), str(trash_ids[3])]},
    )
    assert purge_res.status_code == 200
    assert purge_res.json()["count"] == 2

    assert await trash_repo.get_by_id(trash_ids[2]) is None
    assert await trash_repo.get_by_id(trash_ids[3]) is None
    assert not await local_storage.exists(file_keys[2])
    assert not await local_storage.exists(file_keys[3])


@pytest.mark.anyio
async def test_purge_expired_endpoint_and_authorization(
    client: AsyncClient,
    dbsession: AsyncSession,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify superadmin-only expired purge and deadline filtering."""
    admin_user, regular_user = test_users
    trash_repo = TrashRepository(dbsession)

    # Create expired trash item
    expired_item = await trash_repo.create(
        {
            "entity_type": "document",
            "entity_id": uuid.uuid4(),
            "name": "expired_report.pdf",
            "owner_id": admin_user.id,
            "expires_at": datetime.now(UTC) - timedelta(days=2),
        }
    )

    # Create active (not expired) trash item
    future_item = await trash_repo.create(
        {
            "entity_type": "document",
            "entity_id": uuid.uuid4(),
            "name": "active_trash.pdf",
            "owner_id": admin_user.id,
            "expires_at": datetime.now(UTC) + timedelta(days=15),
        }
    )

    # Regular user attempting to purge expired should receive 403
    auth_state.user = regular_user
    forbidden_res = await client.post("/api/trash/purge-expired")
    assert forbidden_res.status_code == 403

    # SuperAdmin triggers purge-expired
    auth_state.user = admin_user
    purge_res = await client.post("/api/trash/purge-expired")
    assert purge_res.status_code == 200
    assert purge_res.json()["purged_count"] >= 1

    # Verify expired was purged, future item remains
    assert await trash_repo.get_by_id(expired_item.id) is None
    assert await trash_repo.get_by_id(future_item.id) is not None


@pytest.mark.anyio
async def test_trash_rbac_scoping(
    client: AsyncClient,
    dbsession: AsyncSession,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify RBAC scoping prevents cross-user access to trash bin items."""
    admin_user, regular_user = test_users

    # 1. Create document as regular_user
    auth_state.user = regular_user
    res = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("user_private.txt", io.BytesIO(b"priv"), "text/plain")},
        data={"entity_type": "user_item", "entity_id": str(regular_user.id)},
    )
    doc_id = res.json()["id"]
    await client.delete(f"/api/storage/documents/{doc_id}")

    trash_repo = TrashRepository(dbsession)
    user_trash = await trash_repo.get_by_entity("document", uuid.UUID(doc_id))
    assert user_trash is not None

    # 2. Switch to a second regular user
    user_repo = BaseRepository(User, dbsession)
    second_user_db = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Second User",
            "email": f"second_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    auth_state.user = user_to_response(second_user_db)

    # Second user sees empty trash list
    list_res = await client.get("/api/trash")
    assert list_res.status_code == 200
    assert not any(i["id"] == str(user_trash.id) for i in list_res.json()["data"])

    # Second user cannot access or restore the item (404 masked)
    assert (await client.get(f"/api/trash/{user_trash.id}")).status_code == 404
    assert (await client.post(f"/api/trash/{user_trash.id}/restore")).status_code == 404
    assert (await client.delete(f"/api/trash/{user_trash.id}/purge")).status_code == 404

    # 3. SuperAdmin can access and manage it
    auth_state.user = admin_user
    admin_get = await client.get(f"/api/trash/{user_trash.id}")
    assert admin_get.status_code == 200
    assert admin_get.json()["id"] == str(user_trash.id)


@pytest.mark.anyio
async def test_periodic_trash_purge_task(
    app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Verify background task logic and clean shutdown on cancellation."""
    trash_repo = TrashRepository(dbsession)

    # Insert expired item
    expired = await trash_repo.create(
        {
            "entity_type": "document",
            "entity_id": uuid.uuid4(),
            "name": "old_backup.tar",
            "expires_at": datetime.now(UTC) - timedelta(days=1),
        }
    )

    # Test purge_expired_trash function directly
    purged_count = await purge_expired_trash(dbsession)
    assert purged_count >= 1
    assert await trash_repo.get_by_id(expired.id) is None

    # Test background loop task cancellation
    task = asyncio.create_task(run_periodic_trash_purge(app))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_trash_deletor_info_populated(
    client: AsyncClient,
    auth_state: AuthContextState,
) -> None:
    """Verify trash items return deletor user name, email, and deletor object."""
    entity_id = uuid.uuid4()
    upload_res = await client.post(
        "/api/storage/documents/upload",
        files={"file": ("deletor_test.pdf", io.BytesIO(b"content"), "application/pdf")},
        data={"entity_type": "invoice", "entity_id": str(entity_id)},
    )
    doc_id = upload_res.json()["id"]

    # Soft delete the document as the current admin user
    del_res = await client.delete(f"/api/storage/documents/{doc_id}")
    assert del_res.status_code == 200

    # List trash items and find the deleted document
    list_res = await client.get("/api/trash?q=deletor_test")
    assert list_res.status_code == 200
    items = list_res.json()["data"]
    assert len(items) == 1
    item = items[0]

    expected_name = auth_state.user.name
    expected_email = auth_state.user.email
    assert item["deleted_by"] == str(auth_state.user.id)
    assert item["deleted_by_name"] == expected_name
    assert item["deleted_by_email"] == expected_email
    assert item["deletor"] == {"name": expected_name, "email": expected_email}

    # Verify single item endpoint returns the exact same deletor fields
    get_res = await client.get(f"/api/trash/{item['id']}")
    assert get_res.status_code == 200
    single = get_res.json()
    assert single["deleted_by_name"] == expected_name
    assert single["deleted_by_email"] == expected_email
    assert single["deletor"] == {"name": expected_name, "email": expected_email}


@pytest.mark.anyio
async def test_trash_expires_at_filters(
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify filtering trash items by expires_at_from and expires_at_to."""
    trash_repo = TrashRepository(dbsession)
    now = datetime.now(UTC)

    # Item expiring in 3 days
    item_3d = await trash_repo.create(
        {
            "entity_type": "invoice",
            "entity_id": uuid.uuid4(),
            "name": "expire_3d.pdf",
            "expires_at": now + timedelta(days=3),
        }
    )

    # Item expiring in 10 days
    item_10d = await trash_repo.create(
        {
            "entity_type": "invoice",
            "entity_id": uuid.uuid4(),
            "name": "expire_10d.pdf",
            "expires_at": now + timedelta(days=10),
        }
    )

    # Filter with epoch milliseconds (like frontend: +2 days to +5 days)
    ts_from_ms = int((now + timedelta(days=2)).timestamp() * 1000)
    ts_to_ms = int((now + timedelta(days=5)).timestamp() * 1000)
    res_ms = await client.get(
        "/api/trash",
        params={"expires_at_from": str(ts_from_ms), "expires_at_to": str(ts_to_ms)},
    )
    assert res_ms.status_code == 200
    ids_ms = [i["id"] for i in res_ms.json()["data"]]
    assert str(item_3d.id) in ids_ms
    assert str(item_10d.id) not in ids_ms

    # Filter with ISO date strings (+8 days to +12 days)
    iso_from = (now + timedelta(days=8)).isoformat()
    iso_to = (now + timedelta(days=12)).isoformat()
    res_iso = await client.get(
        "/api/trash",
        params={"expires_at_from": iso_from, "expires_at_to": iso_to},
    )
    assert res_iso.status_code == 200
    ids_iso = [i["id"] for i in res_iso.json()["data"]]
    assert str(item_10d.id) in ids_iso
    assert str(item_3d.id) not in ids_iso


@pytest.mark.anyio
async def test_trash_purge_emits_audit(
    client: AsyncClient,
    dbsession: AsyncSession,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify purging trash items records PERMANENT_DELETE audit log."""
    from fastapi_plantilla.modules.audit.repository import AuditRepository

    test_user = test_users[0]
    auth_state.user = test_user
    trash_repo = TrashRepository(dbsession)
    entity_id = uuid.uuid4()
    item = await trash_repo.create(
        {
            "entity_type": "company",
            "entity_id": entity_id,
            "name": "Acme Purged Corp",
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        }
    )

    purge_res = await client.delete(f"/api/trash/{item.id}/purge")
    assert purge_res.status_code == 204

    audit_repo = AuditRepository(dbsession)
    logs = await audit_repo.get_entity_history("company", entity_id)
    assert len(logs) >= 1
    purge_log = next(
        (entry for entry in logs if entry.action == "PERMANENT_DELETE"), None
    )
    assert purge_log is not None
    assert purge_log.actor_id == test_user.id
    assert purge_log.details is not None
    assert "Acme Purged Corp" in purge_log.details
