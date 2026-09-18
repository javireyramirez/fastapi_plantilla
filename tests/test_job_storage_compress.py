import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.app import get_app
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.storage.dependencies import get_storage_provider
from fastapi_plantilla.modules.storage.jobs import (
    StorageCompressJobPayload,
    handle_storage_compress,
)
from fastapi_plantilla.modules.storage.models import Storage


def _user_to_response(user: User) -> UserResponse:
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


@pytest.mark.anyio
async def test_handle_storage_compress(dbsession: AsyncSession) -> None:
    """Test background handler compresses files into ZIP and uploads to storage."""
    user = User(name="Owner", email=f"user-{uuid.uuid4().hex[:8]}@example.com")
    dbsession.add(user)
    await dbsession.flush()

    provider = get_storage_provider()
    key1 = f"test_docs/{uuid.uuid4().hex}_file1.txt"
    key2 = f"test_docs/{uuid.uuid4().hex}_file2.txt"
    await provider.upload(key1, b"Hello file 1", content_type="text/plain")
    await provider.upload(key2, b"Hello file 2", content_type="text/plain")

    entity_id = uuid.uuid4()
    doc1 = Storage(
        name="file1.txt",
        file_key=key1,
        content_type="text/plain",
        size_bytes=12,
        is_uploaded=True,
        entity_type="companies",
        entity_id=entity_id,
        owner_id=user.id,
    )
    doc2 = Storage(
        name="file2.txt",
        file_key=key2,
        content_type="text/plain",
        size_bytes=12,
        is_uploaded=True,
        entity_type="companies",
        entity_id=entity_id,
        owner_id=user.id,
    )
    dbsession.add_all([doc1, doc2])
    await dbsession.flush()

    payload = StorageCompressJobPayload(
        storage_ids=[doc1.id, doc2.id],
        zip_filename="my_archive.zip",
        scope={
            "scope": "GLOBAL",
            "user_id": str(user.id),
            "is_super_admin": True,
        },
    )

    mock_update_progress = AsyncMock()
    ctx = JobContext(
        job_id=uuid.uuid4(),
        name="storage.compress",
        payload=payload,
        entity_type="storage",
        entity_id=None,
        lease_token=1,
        session=dbsession,
        _update_progress_fn=mock_update_progress,
        _check_cancelled_fn=AsyncMock(return_value=False),
    )

    result = await handle_storage_compress(ctx)

    assert result is not None
    assert result["file_count"] == 2
    assert result["filename"] == "my_archive.zip"
    assert result["file_key"].startswith("temp_zips/")
    assert result["size_bytes"] > 0
    assert "download_url" in result
    mock_update_progress.assert_awaited()


@pytest.mark.anyio
async def test_api_storage_zip_async_job(dbsession: AsyncSession) -> None:
    """Test POST /storage/zip?async_job=true returns 202 Accepted with JobResponse."""

    user = User(
        name="SuperAdmin",
        email=f"super-{uuid.uuid4().hex[:8]}@example.com",
        is_super_admin=True,
    )
    dbsession.add(user)
    await dbsession.flush()

    user_resp = _user_to_response(user)
    app = get_app()
    app.dependency_overrides[get_db_session] = lambda: dbsession
    app.dependency_overrides[get_current_user] = lambda: user_resp
    app.dependency_overrides[get_current_active_superuser] = lambda: user_resp

    test_id = uuid.uuid4()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/storage/zip?async_job=true",
                json={"storage_ids": [str(test_id)]},
            )
            assert resp.status_code == 202
            body = resp.json()
            assert body["name"] == "storage.compress"
            assert body["status"] == "PENDING"
            assert body["entity_type"] == "storage"
            assert "payload" in body
            assert body["payload"]["storage_ids"] == [str(test_id)]
    finally:
        app.dependency_overrides.clear()
