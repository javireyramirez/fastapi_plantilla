import io
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, status
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
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.storage.dependencies import (
    get_storage_provider,
)
from fastapi_plantilla.modules.storage.providers import (
    LocalStorageProvider,
    MockStorageProvider,
    PresignedUrlMethod,
    S3StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import StorageRepository
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
    key = "storage/entity/123/file.bin"
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
        "http://testserver/api/storage/local-files/storage/entity/123/file.bin?"
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
    assert provider.public_client is provider.client

    # When public_endpoint_url is provided, public_client should use it
    provider_public = S3StorageProvider(
        bucket="test-bucket",
        endpoint_url="http://minio:9000",
        public_endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key="fake-key",
        secret_key="fake-secret",  # noqa: S106
    )
    assert provider_public.public_client is not provider_public.client


@pytest.mark.anyio
async def test_s3_provider_presigned_url_uses_public_client() -> None:
    """Verify presigned url uses public_endpoint_url."""
    provider = S3StorageProvider(
        bucket="test-bucket",
        endpoint_url="http://minio:9000",
        public_endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key="fake-key",
        secret_key="fake-secret",  # noqa: S106
    )
    url = await provider.get_presigned_url("test.pdf", expires_in=60)
    assert url.startswith("http://localhost:9000/test-bucket/test.pdf?")


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
        "/api/storage/presigned-upload",
        json=req_body,
    )
    assert res_upload_req.status_code == 201
    upload_data = res_upload_req.json()
    doc_id = upload_data["storage_id"]
    upload_url = upload_data["upload_url"]
    assert upload_data["method"] == "PUT"
    assert "local-files" in upload_url

    # Check document in DB is initially not uploaded
    res_doc_init = await storage_client.get(f"/api/storage/{doc_id}")
    assert res_doc_init.status_code == 200
    assert not res_doc_init.json()["is_uploaded"]

    # Trying to confirm before uploading to storage must fail
    res_confirm_fail = await storage_client.post(
        f"/api/storage/{doc_id}/confirm",
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
        f"/api/storage/{doc_id}/confirm",
        json={"size_bytes": len(file_bytes), "content_type": "application/pdf"},
    )
    assert res_confirm.status_code == 200
    confirmed_doc = res_confirm.json()
    assert confirmed_doc["is_uploaded"] is True
    assert confirmed_doc["name"] == "contrato.pdf"
    assert confirmed_doc["size_bytes"] == len(file_bytes)

    # 4. Request presigned download URL
    res_down_url = await storage_client.get(f"/api/storage/{doc_id}/download-url")
    assert res_down_url.status_code == 200
    download_url_data = res_down_url.json()
    assert "local-files" in download_url_data["download_url"]

    # 5. Download via presigned download URL
    dl_relative = download_url_data["download_url"].replace("http://test", "")
    dl_res = await storage_client.get(dl_relative)
    assert dl_res.status_code == 200
    assert dl_res.content == file_bytes

    # 6. Download content directly from API
    direct_res = await storage_client.get(f"/api/storage/{doc_id}/download")
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
        "/api/storage/upload",
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
    dl_res = await storage_client.get(f"/api/storage/{doc['id']}/download")
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
        "/api/storage/upload",
        files={"file": ("doc1.txt", io.BytesIO(doc1_bytes), "text/plain")},
        data={"entity_type": "project", "entity_id": str(entity_id)},
    )
    assert res1.status_code == 201
    doc1_id = res1.json()["id"]

    res2 = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("doc2.txt", io.BytesIO(doc2_bytes), "text/plain")},
        data={"entity_type": "project", "entity_id": str(entity_id)},
    )
    assert res2.status_code == 201
    doc2_id = res2.json()["id"]

    # 1. Download zip by entity_type + entity_id
    zip_res = await storage_client.post(
        "/api/storage/zip",
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

    # 2. Download zip by explicit storage_ids
    zip_ids_res = await storage_client.post(
        "/api/storage/zip",
        json={"storage_ids": [doc1_id, doc2_id]},
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
        "/api/storage/upload",
        files={"file": ("temp.log", io.BytesIO(file_bytes), "text/plain")},
        data={"entity_type": "server", "entity_id": str(entity_id)},
    )
    doc_id = res.json()["id"]

    # List paginated documents
    list_res = await storage_client.get(
        f"/api/storage?entity_type=server&entity_id={entity_id}"
    )
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["meta"]["total"] >= 1
    assert any(d["id"] == doc_id for d in list_data["data"])

    # Update metadata
    patch_res = await storage_client.patch(
        f"/api/storage/{doc_id}",
        json={"name": "server_audit.log", "description": "Actualizado"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "server_audit.log"
    assert patch_res.json()["description"] == "Actualizado"

    # Soft delete (move to trash)
    del_res = await storage_client.delete(f"/api/storage/{doc_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "TRASHED"

    # Active list should no longer show the trashed document
    list_after_del = await storage_client.get(
        f"/api/storage?entity_type=server&entity_id={entity_id}&is_trash=false"
    )
    assert not any(d["id"] == doc_id for d in list_after_del.json()["data"])

    # Restore from trash
    restore_res = await storage_client.post(f"/api/storage/{doc_id}/restore")
    assert restore_res.status_code == 200
    assert restore_res.json()["status"] == "ACTIVE"

    # Permanent delete
    perm_res = await storage_client.delete(f"/api/storage/{doc_id}/permanent")
    assert perm_res.status_code == 204

    # Document no longer exists
    get_res = await storage_client.get(f"/api/storage/{doc_id}")
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
        "/api/storage/upload",
        files={"file": ("secret_user.txt", io.BytesIO(b"user secret"), "text/plain")},
        data={"entity_type": "user_data", "entity_id": str(regular_user.id)},
    )
    assert res.status_code == 201
    user_doc_id = res.json()["id"]

    # Regular user can access it
    get_res = await storage_client.get(f"/api/storage/{user_doc_id}")
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
    forbidden_res = await storage_client.get(f"/api/storage/{user_doc_id}")
    assert forbidden_res.status_code == 404

    # Other user cannot delete it
    forbidden_del = await storage_client.delete(f"/api/storage/{user_doc_id}")
    assert forbidden_del.status_code == 404

    # 3. SuperAdmin can access it
    auth_state.user = admin_user
    admin_get = await storage_client.get(f"/api/storage/{user_doc_id}")
    assert admin_get.status_code == 200
    assert admin_get.json()["id"] == user_doc_id


@pytest.mark.anyio
async def test_presigned_upload_canonical_payload(
    storage_client: AsyncClient,
) -> None:
    """Verify presigned upload accepts canonical payload with name and size_bytes."""
    entity_id = str(uuid.uuid4())

    req_body = {
        "name": "36002307_20260820-1.pdf",
        "content_type": "application/pdf",
        "size_bytes": 45937,
        "entity_type": "companies",
        "entity_id": entity_id,
    }
    res = await storage_client.post(
        "/api/storage/presigned-upload",
        json=req_body,
    )
    assert res.status_code == 201
    data = res.json()
    assert "upload_url" in data
    assert "storage_id" in data


@pytest.mark.anyio
async def test_documents_entity_id_filtering_isolation(
    storage_client: AsyncClient,
) -> None:
    """Verify documents for company A and company B are strictly isolated."""
    comp_a_id = uuid.uuid4()
    comp_b_id = uuid.uuid4()

    # Upload doc A to Company A
    res_a = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("doc_a.pdf", io.BytesIO(b"content a"), "application/pdf")},
        data={"entity_type": "companies", "entity_id": str(comp_a_id)},
    )
    assert res_a.status_code == 201
    doc_a_id = res_a.json()["id"]

    # Upload doc B to Company B
    res_b = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("doc_b.pdf", io.BytesIO(b"content b"), "application/pdf")},
        data={"entity_type": "companies", "entity_id": str(comp_b_id)},
    )
    assert res_b.status_code == 201
    doc_b_id = res_b.json()["id"]

    # Verify module_principal_entity is present
    assert res_a.json()["module_principal_entity"] is not None
    assert res_a.json()["module_principal_entity"]["code"] == "companies"
    assert res_a.json()["module_principal_entity"]["name"] == "Compañías"
    assert res_a.json()["module_principal_entity"]["entity_id"] == str(comp_a_id)

    # 1. Query for Company A (snake_case)
    list_a = await storage_client.get(
        f"/api/storage?entity_type=companies&entity_id={comp_a_id}"
    )
    assert list_a.status_code == 200
    docs_a = list_a.json()["data"]
    assert any(d["id"] == doc_a_id for d in docs_a)
    assert not any(d["id"] == doc_b_id for d in docs_a)

    # 2. Query for Company B (snake_case)
    list_b = await storage_client.get(
        f"/api/storage?entity_type=companies&entity_id={comp_b_id}"
    )
    assert list_b.status_code == 200
    docs_b = list_b.json()["data"]
    assert any(d["id"] == doc_b_id for d in docs_b)
    assert not any(d["id"] == doc_a_id for d in docs_b)


@pytest.mark.anyio
async def test_documents_content_type_filtering(
    storage_client: AsyncClient,
) -> None:
    """Verify documents filtering by content_type (single and comma-separated)."""
    entity_id = uuid.uuid4()

    # Upload PDF
    res_pdf = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("doc.pdf", io.BytesIO(b"pdf data"), "application/pdf")},
        data={"entity_type": "companies", "entity_id": str(entity_id)},
    )
    assert res_pdf.status_code == 201
    pdf_id = res_pdf.json()["id"]

    # Upload PNG
    res_png = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("img.png", io.BytesIO(b"png data"), "image/png")},
        data={"entity_type": "companies", "entity_id": str(entity_id)},
    )
    assert res_png.status_code == 201
    png_id = res_png.json()["id"]

    # Upload TXT
    res_txt = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("note.txt", io.BytesIO(b"text data"), "text/plain")},
        data={"entity_type": "companies", "entity_id": str(entity_id)},
    )
    assert res_txt.status_code == 201
    txt_id = res_txt.json()["id"]

    # 1. Query for PDF only (snake_case)
    res = await storage_client.get(
        f"/api/storage?entity_id={entity_id}&content_type=application/pdf"
    )
    assert res.status_code == 200
    items = res.json()["data"]
    ids = [d["id"] for d in items]
    assert pdf_id in ids
    assert png_id not in ids
    assert txt_id not in ids

    # 2. Query for PDF and PNG (comma-separated content_type)
    res = await storage_client.get(
        f"/api/storage?entity_id={entity_id}&content_type=application/pdf,image/png"
    )
    assert res.status_code == 200
    items = res.json()["data"]
    ids = [d["id"] for d in items]
    assert pdf_id in ids
    assert png_id in ids
    assert txt_id not in ids


@pytest.mark.anyio
async def test_document_module_principal_entity_name_resolution(
    storage_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify module_principal_entity correctly resolves and returns entity_name."""
    company_repo = BaseRepository(Company, dbsession)
    company = await company_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Acme Logistics SL",
            "nif": f"B{uuid.uuid4().hex[:8].upper()}",
        }
    )
    await dbsession.flush()

    # Upload document associated with the company
    file_bytes = b"Contract terms and conditions"
    res = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("contract.pdf", io.BytesIO(file_bytes), "application/pdf")},
        data={"entity_type": "companies", "entity_id": str(company.id)},
    )
    assert res.status_code == 201
    upload_data = res.json()
    doc_id = upload_data["id"]

    assert upload_data["module_principal_entity"] is not None
    assert upload_data["module_principal_entity"]["code"] == "companies"
    assert upload_data["module_principal_entity"]["name"] == "Compañías"
    assert upload_data["module_principal_entity"]["entity_id"] == str(company.id)
    assert upload_data["module_principal_entity"]["entity_name"] == "Acme Logistics SL"

    # List documents for this company
    list_res = await storage_client.get(f"/api/storage?entity_id={company.id}")
    assert list_res.status_code == 200
    list_data = list_res.json()["data"]
    doc_in_list = next(d for d in list_data if d["id"] == doc_id)
    assert doc_in_list["module_principal_entity"]["entity_name"] == "Acme Logistics SL"

    # Get single document
    get_res = await storage_client.get(f"/api/storage/{doc_id}")
    assert get_res.status_code == 200
    single_data = get_res.json()
    assert single_data["module_principal_entity"]["entity_name"] == "Acme Logistics SL"


@pytest.mark.anyio
async def test_external_url_document_flow(
    storage_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify end-to-end flow for external URL documents."""
    company_repo = BaseRepository(Company, dbsession)
    company = await company_repo.create(
        {
            "id": generate_uuid7(),
            "name": "Cloud Integrations SL",
            "nif": f"B{uuid.uuid4().hex[:8].upper()}",
        }
    )
    await dbsession.flush()

    external_link = "https://drive.google.com/drive/folders/1a2b3c4d5e6f7g8h"
    res = await storage_client.post(
        "/api/storage/url",
        json={
            "entity_type": "companies",
            "entity_id": str(company.id),
            "url": external_link,
            "name": "Carpeta Drive de Facturas",
            "description": "Acceso a carpeta compartida de Google Drive",
        },
    )
    assert res.status_code == 201
    doc_data = res.json()
    doc_id = doc_data["id"]

    assert doc_data["name"] == "Carpeta Drive de Facturas"
    assert doc_data["external_url"] == external_link
    assert doc_data["is_uploaded"] is True
    assert doc_data["size_bytes"] == 0
    assert doc_data["file_key"].startswith("external/")
    assert doc_data["module_principal_entity"]["entity_name"] == "Cloud Integrations SL"

    # Presigned download URL returns direct external URL
    download_url_res = await storage_client.get(f"/api/storage/{doc_id}/download-url")
    assert download_url_res.status_code == 200
    dl_data = download_url_res.json()
    assert dl_data["download_url"] == external_link

    # Direct download endpoint redirects (HTTP 307)
    dl_direct_res = await storage_client.get(
        f"/api/storage/{doc_id}/download",
        follow_redirects=False,
    )
    assert dl_direct_res.status_code == 307
    assert dl_direct_res.headers["location"] == external_link

    # ZIP download includes .url InternetShortcut
    zip_res = await storage_client.post(
        "/api/storage/zip",
        json={"storage_ids": [doc_id]},
    )
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
        file_list = zf.namelist()
        assert len(file_list) == 1
        assert file_list[0].endswith(".url")
        shortcut_content = zf.read(file_list[0]).decode("utf-8")
        assert f"URL={external_link}" in shortcut_content

    # Permanent delete succeeds cleanly without storage provider failure
    del_res = await storage_client.delete(f"/api/storage/{doc_id}/permanent")
    assert del_res.status_code == 204


@pytest.mark.anyio
async def test_storage_etag_and_optimistic_locking(
    storage_client: AsyncClient,
) -> None:
    """Verify ETag headers on GET and optimistic locking on PATCH."""
    entity_id = uuid.uuid4()
    upload_res = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("doc_version.txt", b"Version test", "text/plain")},
        data={"entity_type": "company", "entity_id": str(entity_id)},
    )
    assert upload_res.status_code == status.HTTP_201_CREATED
    doc_id = upload_res.json()["id"]

    # 1. GET returns ETag W/"1"
    get_res = await storage_client.get(f"/api/storage/{doc_id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.headers.get("ETag") == 'W/"1"'

    # 2. PATCH with stale If-Match returns 409
    stale_patch = await storage_client.patch(
        f"/api/storage/{doc_id}",
        headers={"If-Match": 'W/"99"'},
        json={"name": "stale_rename.txt"},
    )
    assert stale_patch.status_code == status.HTTP_409_CONFLICT

    # 3. PATCH with matching If-Match succeeds and increments version
    ok_patch = await storage_client.patch(
        f"/api/storage/{doc_id}",
        headers={"If-Match": 'W/"1"'},
        json={"name": "valid_rename.txt"},
    )
    assert ok_patch.status_code == status.HTTP_200_OK
    assert ok_patch.json()["version"] == 2
    assert ok_patch.headers.get("ETag") == 'W/"2"'


@pytest.mark.anyio
async def test_storage_search_and_filtering(
    storage_client: AsyncClient,
) -> None:
    """Verify search_fields and centralized build_where_filters in list_storage."""
    entity_id = uuid.uuid4()
    await storage_client.post(
        "/api/storage/upload",
        files={"file": ("report_financial_2026.txt", b"Report data", "text/plain")},
        data={"entity_type": "company", "entity_id": str(entity_id)},
    )
    await storage_client.post(
        "/api/storage/upload",
        files={"file": ("invoice_vendor_acme.txt", b"Invoice data", "text/plain")},
        data={"entity_type": "company", "entity_id": str(entity_id)},
    )

    # 1. Search for 'financial' matches only report
    res_fin = await storage_client.get(
        "/api/storage",
        params={"entity_id": str(entity_id), "search": "financial"},
    )
    assert res_fin.status_code == status.HTTP_200_OK
    items_fin = res_fin.json()["data"]
    assert len(items_fin) == 1
    assert "report_financial" in items_fin[0]["name"]

    # 2. Search for 'vendor' matches only invoice
    res_ven = await storage_client.get(
        "/api/storage",
        params={"entity_id": str(entity_id), "search": "vendor"},
    )
    assert res_ven.status_code == status.HTTP_200_OK
    items_ven = res_ven.json()["data"]
    assert len(items_ven) == 1
    assert "invoice_vendor" in items_ven[0]["name"]


@pytest.mark.anyio
async def test_upload_direct_limits_and_validation(
    storage_app: FastAPI,
    storage_client: AsyncClient,
) -> None:
    """Verify upload_direct validates size and extension against settings."""
    mock_settings = MagicMock()

    async def _mock_get_value(key: str, default: Any = None) -> Any:
        if key == "storage.max_upload_size_bytes":
            return 25
        if key == "storage.allowed_extensions":
            return ["txt", "pdf"]
        return default

    mock_settings.get_value = AsyncMock(side_effect=_mock_get_value)
    storage_app.dependency_overrides[get_settings_service] = lambda: mock_settings

    try:
        entity_id = uuid.uuid4()

        # 1. Upload exceeding size limit (50 bytes > 25 bytes limit)
        oversized_res = await storage_client.post(
            "/api/storage/upload",
            files={"file": ("oversized.txt", b"A" * 50, "text/plain")},
            data={"entity_type": "company", "entity_id": str(entity_id)},
        )
        assert oversized_res.status_code == status.HTTP_413_CONTENT_TOO_LARGE
        assert "exceeds maximum limit" in oversized_res.json()["detail"]

        # 2. Upload with disallowed extension (.exe)
        bad_ext_res = await storage_client.post(
            "/api/storage/upload",
            files={"file": ("malicious.exe", b"binary", "application/octet-stream")},
            data={"entity_type": "company", "entity_id": str(entity_id)},
        )
        assert bad_ext_res.status_code == status.HTTP_400_BAD_REQUEST
        assert "File extension '.exe' is not permitted" in bad_ext_res.json()["detail"]
    finally:
        storage_app.dependency_overrides.pop(get_settings_service, None)


@pytest.mark.anyio
async def test_find_by_entity_limit_in_repository(
    dbsession: AsyncSession,
) -> None:
    """Verify find_by_entity in StorageRepository applies limit properly."""
    repo = StorageRepository(dbsession)
    entity_id = uuid.uuid4()

    for idx in range(4):
        await repo.create(
            {
                "id": generate_uuid7(),
                "entity_type": "test_limit_entity",
                "entity_id": entity_id,
                "name": f"file_{idx}.txt",
                "file_key": f"test_limit_{uuid.uuid4().hex[:6]}",
                "content_type": "text/plain",
                "size_bytes": 10,
                "is_uploaded": True,
            }
        )

    # Fetch with limit=2
    items_limited = await repo.find_by_entity("test_limit_entity", entity_id, limit=2)
    assert len(items_limited) == 2

    # Fetch with limit=None
    items_all = await repo.find_by_entity("test_limit_entity", entity_id, limit=None)
    assert len(items_all) == 4


@pytest.mark.anyio
async def test_confirm_upload_uses_real_metadata(
    storage_client: AsyncClient,
    storage_app: FastAPI,
) -> None:
    """Verify confirm_upload reads real size and MIME from storage provider."""
    entity_id = uuid.uuid4()
    req_body = {
        "name": "photo_real.png",
        "content_type": "text/plain",
        "size_bytes": 10,
        "entity_type": "company",
        "entity_id": str(entity_id),
    }
    presigned_res = await storage_client.post(
        "/api/storage/presigned-upload", json=req_body
    )
    assert presigned_res.status_code == status.HTTP_201_CREATED
    storage_id = presigned_res.json()["storage_id"]
    file_key = presigned_res.json()["file_key"]

    # Upload actual bytes (150 bytes) directly to local provider
    provider = storage_app.dependency_overrides[get_storage_provider]()
    actual_data = b"P" * 150
    await provider.upload(file_key, actual_data, "image/png")

    # Client confirms with fake declared size_bytes=5
    confirm_res = await storage_client.post(
        f"/api/storage/{storage_id}/confirm",
        json={"size_bytes": 5},
    )
    assert confirm_res.status_code == status.HTTP_200_OK
    data = confirm_res.json()
    assert data["is_uploaded"] is True
    # Factual size from provider (150) overrides fake declared size (5)
    assert data["size_bytes"] == 150


@pytest.mark.anyio
async def test_zip_download_scope_isolation(
    storage_client: AsyncClient,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify zip download enforces RBAC scope at SQL query level (SSOT)."""
    admin_user, regular_user = test_users

    # 1. Admin uploads document A
    auth_state.user = admin_user
    res_a = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("admin_doc.txt", b"admin content", "text/plain")},
        data={"entity_type": "company", "entity_id": str(uuid.uuid4())},
    )
    assert res_a.status_code == status.HTTP_201_CREATED
    doc_a_id = res_a.json()["id"]

    # 2. Regular user uploads document B
    auth_state.user = regular_user
    res_b = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("regular_doc.txt", b"regular content", "text/plain")},
        data={"entity_type": "user", "entity_id": str(regular_user.id)},
    )
    assert res_b.status_code == status.HTTP_201_CREATED
    doc_b_id = res_b.json()["id"]

    # 3. Regular user (OWN scope) requests ZIP of both docs
    zip_res = await storage_client.post(
        "/api/storage/zip",
        json={"storage_ids": [doc_a_id, doc_b_id]},
    )
    assert zip_res.status_code == status.HTTP_200_OK
    zip_bytes = zip_res.content
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "regular_doc.txt" in names
        assert "admin_doc.txt" not in names


@pytest.mark.anyio
async def test_zip_download_aggregate_size_limit(
    storage_client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Verify zip download rejects when aggregate size exceeds safety limit."""
    repo = StorageRepository(dbsession)
    entity_id = uuid.uuid4()

    # Create 2 records with aggregate size > 100 MB (104857600 bytes)
    id1 = generate_uuid7()
    id2 = generate_uuid7()
    for doc_id, name in [(id1, "huge1.dat"), (id2, "huge2.dat")]:
        await repo.create(
            {
                "id": doc_id,
                "entity_type": "company",
                "entity_id": entity_id,
                "name": name,
                "file_key": f"test_huge_{doc_id}",
                "content_type": "application/octet-stream",
                "size_bytes": 60 * 1024 * 1024,  # 60 MB each = 120 MB total
                "is_uploaded": True,
            }
        )

    zip_res = await storage_client.post(
        "/api/storage/zip",
        json={"storage_ids": [str(id1), str(id2)]},
    )
    assert zip_res.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert "exceeds maximum limit" in zip_res.json()["detail"]


@pytest.mark.anyio
async def test_entity_access_authorization(
    storage_client: AsyncClient,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
) -> None:
    """Verify non-admin users cannot attach files to arbitrary foreign entities."""
    _, regular_user = test_users
    auth_state.user = regular_user

    other_user_id = uuid.uuid4()
    # 1. Regular user trying to attach to another user's profile -> 403
    forbidden_res = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("hacked.txt", b"payload", "text/plain")},
        data={"entity_type": "user", "entity_id": str(other_user_id)},
    )
    assert forbidden_res.status_code == status.HTTP_403_FORBIDDEN
    assert "cannot attach files to another user" in forbidden_res.json()["detail"]

    # 2. Regular user attaching to their own user profile -> 201
    allowed_res = await storage_client.post(
        "/api/storage/upload",
        files={"file": ("legit.txt", b"payload", "text/plain")},
        data={"entity_type": "user", "entity_id": str(regular_user.id)},
    )
    assert allowed_res.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_purge_pending_orphans_endpoint(
    storage_client: AsyncClient,
    auth_state: AuthContextState,
    test_users: tuple[UserResponse, UserResponse],
    dbsession: AsyncSession,
) -> None:
    """Verify administrative endpoint for purging orphaned pending uploads."""
    admin_user, regular_user = test_users
    repo = StorageRepository(dbsession)

    orphan_id = generate_uuid7()
    await repo.create(
        {
            "id": orphan_id,
            "entity_type": "company",
            "entity_id": uuid.uuid4(),
            "name": "orphan.txt",
            "file_key": f"orphan_{orphan_id}",
            "content_type": "text/plain",
            "size_bytes": 0,
            "is_uploaded": False,
        }
    )

    # 1. Regular user cannot trigger purge -> 403
    auth_state.user = regular_user
    forbidden_res = await storage_client.delete(
        "/api/storage/pending/purge?older_than_seconds=0"
    )
    assert forbidden_res.status_code == status.HTTP_403_FORBIDDEN

    # 2. Admin can trigger purge -> 200 with count
    auth_state.user = admin_user
    ok_res = await storage_client.delete(
        "/api/storage/pending/purge?older_than_seconds=0"
    )
    assert ok_res.status_code == status.HTTP_200_OK
    assert ok_res.json()["purged"] >= 1

    # Verify orphan is deleted
    assert await repo.get_by_id(orphan_id) is None


@pytest.mark.anyio
async def test_documents_content_types_repeated_query_params(
    storage_client: AsyncClient,
) -> None:
    """Regression: repeated content_types params must filter instead of ignored."""
    entity_id = uuid.uuid4()

    async def _upload(filename: str, mime: str) -> str:
        res = await storage_client.post(
            "/api/storage/upload",
            files={"file": (filename, io.BytesIO(b"data"), mime)},
            data={"entity_type": "companies", "entity_id": str(entity_id)},
        )
        assert res.status_code == 201
        return res.json()["id"]

    pdf_id = await _upload("doc.pdf", "application/pdf")
    png_id = await _upload("img.png", "image/png")
    txt_id = await _upload("note.txt", "text/plain")

    res = await storage_client.get(
        "/api/storage",
        params=[
            ("page", "1"),
            ("limit", "20"),
            ("entity_id", str(entity_id)),
            ("sort_by", "created_at"),
            ("sort_order", "desc"),
            ("content_types", "application/pdf"),
            ("content_types", "image/png"),
            ("is_trash", "false"),
        ],
    )
    assert res.status_code == 200
    ids = [d["id"] for d in res.json()["data"]]
    assert pdf_id in ids
    assert png_id in ids
    assert txt_id not in ids
