import io
import time
import uuid
import zipfile
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
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.storage.dependencies import (
    get_storage_provider,
)
from fastapi_plantilla.modules.storage.providers import (
    LocalStorageProvider,
    MockStorageProvider,
    PresignedUrlMethod,
    S3StorageProvider,
)
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.storage.service import sanitize_filename

# =========================================================================
# Unit Tests: Storage Providers & Helpers
# =========================================================================


def test_sanitize_filename() -> None:
    """Verify filename sanitization removes paths and illegal characters."""
    assert sanitize_filename("../../../etc/passwd") == "passwd"
    assert sanitize_filename("my file (1) [final].pdf") == "my_file__1___final_.pdf"
    assert sanitize_filename("normal_file.txt") == "normal_file.txt"


@pytest.mark.anyio
async def test_mock_storage_provider() -> None:
    """Verify in-memory MockStorageProvider operations."""
    provider = MockStorageProvider()
    key = "test/mock.txt"
    data = b"Hello, Mock!"

    assert not await provider.exists(key)
    await provider.upload(key, data, "text/plain")
    assert await provider.exists(key)

    downloaded = await provider.download(key)
    assert downloaded == data

    url = await provider.get_presigned_url(key, expires_in=100, method="GET")
    assert "mock://storage/" in url

    await provider.delete(key)
    assert not await provider.exists(key)

    with pytest.raises(FileNotFoundError):
        await provider.download(key)


@pytest.mark.anyio
async def test_local_storage_provider(tmp_path: Path) -> None:
    """Verify LocalStorageProvider file operations and HMAC signed URLs."""
    provider = LocalStorageProvider(
        base_path=tmp_path,
        secret="test-secret-key-12345",  # noqa: S106
        base_url="http://testserver",
    )
    key = "documents/entity/123/file.bin"
    payload = b"\x00\x01\x02\x03\x04"

    assert not await provider.exists(key)
    await provider.upload(key, payload)
    assert await provider.exists(key)

    content = await provider.download(key)
    assert content == payload

    # Presigned URL generation & verification
    url = await provider.get_presigned_url(
        key, expires_in=300, method=PresignedUrlMethod.PUT
    )
    expected_prefix = (
        "http://testserver/api/storage/local-files/documents/entity/123/file.bin?"
    )
    assert expected_prefix in url
    assert "signature=" in url
    assert "expires=" in url
    assert "method=PUT" in url

    # Valid signature check
    expires = int(time.time()) + 300
    sig = provider.generate_signature(key, expires, "PUT")
    assert provider.verify_signature(key, expires, sig, "PUT")
    # Different method fails
    assert not provider.verify_signature(key, expires, sig, "GET")
    # Expired timestamp fails
    assert not provider.verify_signature(key, int(time.time()) - 10, sig, "PUT")
    # Tampered signature fails
    assert not provider.verify_signature(key, expires, "tampered-sig", "PUT")

    # Path traversal protection
    with pytest.raises(ValueError, match="Directory traversal detected"):
        await provider.upload("../../escape.txt", b"hack")

    await provider.delete(key)
    assert not await provider.exists(key)


def test_s3_provider_initialization() -> None:
    """Verify S3StorageProvider initializes client properly."""
    provider = S3StorageProvider(
        bucket="test-bucket",
        region="us-east-1",
        access_key="fake-key",
        secret_key="fake-secret",  # noqa: S106
    )
    assert provider.bucket == "test-bucket"


# =========================================================================
# Integration Test App & Fixtures
# =========================================================================


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
            "email": f"admin_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": True,
            "is_active": True,
        }
    )
    regular = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Regular User",
            "email": f"regular_{uuid.uuid4().hex[:6]}@example.com",
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
def storage_app(
    dbsession: AsyncSession,
    tmp_path: Path,
    auth_state: AuthContextState,
) -> FastAPI:
    """Configure test FastAPI application with storage router and local provider."""
    app = FastAPI()
    app.include_router(storage_router, prefix="/api")

    local_provider = LocalStorageProvider(
        base_path=tmp_path,
        secret="test-auth-secret-key-storage-9999",  # noqa: S106
        base_url="http://test",
    )

    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_storage_provider] = lambda: local_provider
    app.dependency_overrides[get_current_user] = lambda: auth_state.user

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

    app.dependency_overrides[get_scope_context] = _scope_override
    app.dependency_overrides[get_write_options] = _write_options_override

    return app


@pytest.fixture
async def storage_client(storage_app: FastAPI) -> AsyncClient:
    """AsyncClient wired to the test storage app."""
    return AsyncClient(
        transport=ASGITransport(app=storage_app),
        base_url="http://test",
    )


# =========================================================================
# Integration Tests: Document Lifecycle & Endpoints
# =========================================================================


@pytest.mark.anyio
async def test_presigned_upload_and_download_flow(
    storage_client: AsyncClient,
) -> None:
    """Test full presigned upload -> direct PUT -> confirm -> download lifecycle."""
    entity_id = uuid.uuid4()
    file_bytes = b"Contenido de prueba para documento PDF"

    # 1. Request presigned upload URL
    req_body = {
        "entity_type": "company",
        "entity_id": str(entity_id),
        "name": "contrato.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(file_bytes),
        "description": "Contrato comercial firmado",
    }
    res_upload_req = await storage_client.post(
        "/api/storage/documents/presigned-upload",
        json=req_body,
    )
    assert res_upload_req.status_code == 201
    upload_data = res_upload_req.json()
    doc_id = upload_data["document_id"]
    upload_url = upload_data["upload_url"]
    assert upload_data["method"] == "PUT"
    assert "local-files" in upload_url

    # Check document in DB is initially not uploaded
    res_doc_init = await storage_client.get(f"/api/storage/documents/{doc_id}")
    assert res_doc_init.status_code == 200
    assert not res_doc_init.json()["is_uploaded"]

    # Trying to confirm before uploading to storage must fail
    res_confirm_fail = await storage_client.post(
        f"/api/storage/documents/{doc_id}/confirm",
        json={},
    )
    assert res_confirm_fail.status_code == 400

    # 2. Upload directly to presigned URL (PUT to local-files endpoint)
    relative_url = upload_url.replace("http://test", "")
    put_res = await storage_client.put(
        relative_url,
        content=file_bytes,
        headers={"Content-Type": "application/pdf"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["status"] == "ok"

    # Test tampering with signature returns 403
    bad_url = relative_url.replace("signature=", "signature=bad")
    bad_res = await storage_client.put(bad_url, content=file_bytes)
    assert bad_res.status_code == 403

    # 3. Confirm upload
    res_confirm = await storage_client.post(
        f"/api/storage/documents/{doc_id}/confirm",
        json={"size_bytes": len(file_bytes), "content_type": "application/pdf"},
    )
    assert res_confirm.status_code == 200
    confirmed_doc = res_confirm.json()
    assert confirmed_doc["is_uploaded"] is True
    assert confirmed_doc["name"] == "contrato.pdf"
    assert confirmed_doc["size_bytes"] == len(file_bytes)

    # 4. Request presigned download URL
    res_down_url = await storage_client.get(
        f"/api/storage/documents/{doc_id}/download-url"
    )
    assert res_down_url.status_code == 200
    download_url_data = res_down_url.json()
    assert "local-files" in download_url_data["download_url"]

    # 5. Download via presigned download URL
    dl_relative = download_url_data["download_url"].replace("http://test", "")
    dl_res = await storage_client.get(dl_relative)
    assert dl_res.status_code == 200
    assert dl_res.content == file_bytes

    # 6. Download content directly from API
    direct_res = await storage_client.get(f"/api/storage/documents/{doc_id}/download")
    assert direct_res.status_code == 200
    assert direct_res.content == file_bytes
    assert "contrato.pdf" in direct_res.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_direct_file_upload(
    storage_client: AsyncClient,
) -> None:
    """Test POST /documents/upload multipart direct upload."""
    entity_id = uuid.uuid4()
    file_bytes = b"Datos directos en formato CSV\n1,2,3"

    files = {"file": ("reporte.csv", io.BytesIO(file_bytes), "text/csv")}
    data = {
        "entity_type": "company",
        "entity_id": str(entity_id),
        "description": "Reporte financiero mensual",
    }

    res = await storage_client.post(
        "/api/storage/documents/upload",
        files=files,
        data=data,
    )
    assert res.status_code == 201
    doc = res.json()
    assert doc["is_uploaded"] is True
    assert doc["name"] == "reporte.csv"
    assert doc["content_type"] == "text/csv"
    assert doc["size_bytes"] == len(file_bytes)
    assert doc["extension"] == "csv"

    # Verify download matches uploaded bytes
    dl_res = await storage_client.get(f"/api/storage/documents/{doc['id']}/download")
    assert dl_res.status_code == 200
    assert dl_res.content == file_bytes


@pytest.mark.anyio
async def test_zip_packaging_multiple_documents(
    storage_client: AsyncClient,
) -> None:
    """Test packaging multiple uploaded documents into a ZIP archive."""
    entity_id = uuid.uuid4()

    # Upload two documents for the entity
    doc1_bytes = b"Archivo Uno de prueba"
    doc2_bytes = b"Archivo Dos de prueba"

    res1 = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("doc1.txt", io.BytesIO(doc1_bytes), "text/plain")},
        data={"entity_type": "project", "entity_id": str(entity_id)},
    )
    assert res1.status_code == 201
    doc1_id = res1.json()["id"]

    res2 = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("doc2.txt", io.BytesIO(doc2_bytes), "text/plain")},
        data={"entity_type": "project", "entity_id": str(entity_id)},
    )
    assert res2.status_code == 201
    doc2_id = res2.json()["id"]

    # 1. Download zip by entity_type + entity_id
    zip_res = await storage_client.post(
        "/api/storage/documents/zip",
        json={"entity_type": "project", "entity_id": str(entity_id)},
    )
    assert zip_res.status_code == 200
    assert zip_res.headers.get("content-type") == "application/zip"

    # Inspect zip contents in memory
    zf = zipfile.ZipFile(io.BytesIO(zip_res.content))
    namelist = zf.namelist()
    assert "doc1.txt" in namelist
    assert "doc2.txt" in namelist
    assert zf.read("doc1.txt") == doc1_bytes
    assert zf.read("doc2.txt") == doc2_bytes

    # 2. Download zip by explicit document_ids
    zip_ids_res = await storage_client.post(
        "/api/storage/documents/zip",
        json={"document_ids": [doc1_id, doc2_id]},
    )
    assert zip_ids_res.status_code == 200
    zf2 = zipfile.ZipFile(io.BytesIO(zip_ids_res.content))
    assert zf2.read("doc1.txt") == doc1_bytes
    assert zf2.read("doc2.txt") == doc2_bytes


@pytest.mark.anyio
async def test_document_crud_soft_delete_restore_permanent(
    storage_client: AsyncClient,
) -> None:
    """Test listing, metadata update, soft-delete, restore and permanent delete."""
    entity_id = uuid.uuid4()
    file_bytes = b"Temporal document data"

    res = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("temp.log", io.BytesIO(file_bytes), "text/plain")},
        data={"entity_type": "server", "entity_id": str(entity_id)},
    )
    doc_id = res.json()["id"]

    # List paginated documents
    list_res = await storage_client.get(
        f"/api/storage/documents?entity_type=server&entity_id={entity_id}"
    )
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["meta"]["total"] >= 1
    assert any(d["id"] == doc_id for d in list_data["data"])

    # Update metadata
    patch_res = await storage_client.patch(
        f"/api/storage/documents/{doc_id}",
        json={"name": "server_audit.log", "description": "Actualizado"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "server_audit.log"
    assert patch_res.json()["description"] == "Actualizado"

    # Soft delete (move to trash)
    del_res = await storage_client.delete(f"/api/storage/documents/{doc_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "TRASHED"

    # Active list should no longer show the trashed document
    list_after_del = await storage_client.get(
        f"/api/storage/documents?entity_type=server&entity_id={entity_id}&is_trash=false"
    )
    assert not any(d["id"] == doc_id for d in list_after_del.json()["data"])

    # Restore from trash
    restore_res = await storage_client.post(f"/api/storage/documents/{doc_id}/restore")
    assert restore_res.status_code == 200
    assert restore_res.json()["status"] == "ACTIVE"

    # Permanent delete
    perm_res = await storage_client.delete(f"/api/storage/documents/{doc_id}/permanent")
    assert perm_res.status_code == 204

    # Document no longer exists
    get_res = await storage_client.get(f"/api/storage/documents/{doc_id}")
    assert get_res.status_code == 404


@pytest.mark.anyio
async def test_rbac_document_ownership_isolation(
    storage_client: AsyncClient,
    dbsession: AsyncSession,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify regular users cannot access other users' documents."""
    admin_user, regular_user = test_users

    # 1. Create document as regular_user
    auth_state.user = regular_user

    res = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("secret_user.txt", io.BytesIO(b"user secret"), "text/plain")},
        data={"entity_type": "user_data", "entity_id": str(regular_user.id)},
    )
    assert res.status_code == 201
    user_doc_id = res.json()["id"]

    # Regular user can access it
    get_res = await storage_client.get(f"/api/storage/documents/{user_doc_id}")
    assert get_res.status_code == 200

    # 2. Switch to another regular user created in DB
    user_repo = BaseRepository(User, dbsession)
    other_db_user = await user_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Other User",
            "email": f"other_{uuid.uuid4().hex[:6]}@example.com",
            "is_super_admin": False,
            "is_active": True,
        }
    )
    auth_state.user = user_to_response(other_db_user)

    # Other user receives 404 (masked forbidden)
    forbidden_res = await storage_client.get(f"/api/storage/documents/{user_doc_id}")
    assert forbidden_res.status_code == 404

    # Other user cannot delete it
    forbidden_del = await storage_client.delete(f"/api/storage/documents/{user_doc_id}")
    assert forbidden_del.status_code == 404

    # 3. SuperAdmin can access it
    auth_state.user = admin_user
    admin_get = await storage_client.get(f"/api/storage/documents/{user_doc_id}")
    assert admin_get.status_code == 200
    assert admin_get.json()["id"] == user_doc_id


@pytest.mark.anyio
async def test_presigned_upload_with_filename_aliases(
    storage_client: AsyncClient,
) -> None:
    """Verify presigned upload accepts filename and file_size aliases."""
    entity_id = str(uuid.uuid4())

    req_body = {
        "filename": "36002307_20260820-1.pdf",
        "content_type": "application/pdf",
        "file_size": 45937,
        "entity_type": "companies",
        "entity_id": entity_id,
    }
    res = await storage_client.post(
        "/api/storage/documents/presigned-upload",
        json=req_body,
    )
    assert res.status_code == 201
    data = res.json()
    assert "upload_url" in data
    assert "document_id" in data


@pytest.mark.anyio
async def test_documents_entity_id_filtering_isolation(
    storage_client: AsyncClient,
) -> None:
    """Verify documents for company A and company B are strictly isolated."""
    comp_a_id = uuid.uuid4()
    comp_b_id = uuid.uuid4()

    # Upload doc A to Company A
    res_a = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("doc_a.pdf", io.BytesIO(b"content a"), "application/pdf")},
        data={"entity_type": "companies", "entity_id": str(comp_a_id)},
    )
    assert res_a.status_code == 201
    doc_a_id = res_a.json()["id"]

    # Upload doc B to Company B
    res_b = await storage_client.post(
        "/api/storage/documents/upload",
        files={"file": ("doc_b.pdf", io.BytesIO(b"content b"), "application/pdf")},
        data={"entityType": "companies", "entityId": str(comp_b_id)},
    )
    assert res_b.status_code == 201
    doc_b_id = res_b.json()["id"]

    # 1. Query for Company A (snake_case)
    list_a = await storage_client.get(
        f"/api/storage/documents?entity_type=companies&entity_id={comp_a_id}"
    )
    assert list_a.status_code == 200
    docs_a = list_a.json()["data"]
    assert any(d["id"] == doc_a_id for d in docs_a)
    assert not any(d["id"] == doc_b_id for d in docs_a)

    # 2. Query for Company B (camelCase)
    list_b = await storage_client.get(
        f"/api/storage/documents?entityType=companies&entityId={comp_b_id}"
    )
    assert list_b.status_code == 200
    docs_b = list_b.json()["data"]
    assert any(d["id"] == doc_b_id for d in docs_b)
    assert not any(d["id"] == doc_a_id for d in docs_b)
