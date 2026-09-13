from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.settings.models import SystemSetting
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.schema import SettingUpdate
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.storage.providers import LocalStorageProvider
from fastapi_plantilla.modules.storage.repository import DocumentRepository
from fastapi_plantilla.modules.storage.schema import PresignedUploadRequest
from fastapi_plantilla.modules.storage.service import DocumentService


def _mock_user(is_super: bool = False) -> UserResponse:
    uid = generate_uuid7()
    now = datetime.now(UTC)
    return UserResponse(
        id=uid,
        name="Test User",
        email=f"user_{uid}@test.com",
        email_verified=True,
        is_active=True,
        is_system=False,
        is_super_admin=is_super,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_settings_service_cache_and_invalidation(dbsession: AsyncSession) -> None:
    """Test setting retrieval, memory caching and cache invalidation."""
    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)
    service.invalidate_cache()

    # Seed test settings directly
    s1 = SystemSetting(
        id=generate_uuid7(),
        key="test.timeout",
        value=30,
        description="Timeout setting",
        category="general",
        is_public=True,
    )
    s2 = SystemSetting(
        id=generate_uuid7(),
        key="test.secret",
        value="supersecret",
        description="Secret key",
        category="security",
        is_public=False,
    )
    dbsession.add_all([s1, s2])
    await dbsession.flush()

    # 1. First get_value loads from DB and caches
    val = await service.get_value("test.timeout")
    assert val == 30
    assert "test.timeout" in service.cache

    # 2. Modify in DB directly without service to prove memory cache is used
    s1.value = 60
    await dbsession.flush()
    cached_val = await service.get_value("test.timeout")
    assert cached_val == 30  # Still cached in memory!

    # 3. Invalidate cache and fetch again
    service.invalidate_cache()
    fresh_val = await service.get_value("test.timeout")
    assert fresh_val == 60

    # 4. Public settings map
    public_map = await service.get_public_settings()
    assert "test.timeout" in public_map
    assert "test.secret" not in public_map
    assert public_map["test.timeout"] == 60

    # 5. Update through service updates memory cache automatically
    await service.update_setting("test.timeout", SettingUpdate(value=120))
    assert await service.get_value("test.timeout") == 120
    public_map_updated = await service.get_public_settings()
    assert public_map_updated["test.timeout"] == 120


@pytest.mark.anyio
async def test_settings_api_public_and_admin(
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Test public and admin routes for system settings."""
    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)
    service.invalidate_cache()

    # Clean create a public setting
    s = SystemSetting(
        id=generate_uuid7(),
        key="storage.max_upload_size_bytes",
        value=1048576,
        category="storage",
        is_public=True,
    )
    dbsession.add(s)
    await dbsession.flush()

    user = _mock_user(is_super=False)
    superuser = _mock_user(is_super=True)

    fastapi_app.dependency_overrides[get_current_user] = lambda: user
    fastapi_app.dependency_overrides[get_current_active_superuser] = lambda: superuser

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Public settings endpoint (any authenticated user)
        res_public = await client.get("/api/settings/public")
        assert res_public.status_code == status.HTTP_200_OK
        data = res_public.json()
        assert "storage.max_upload_size_bytes" in data
        assert data["storage.max_upload_size_bytes"] == 1048576

        # Admin list endpoint
        res_admin = await client.get("/api/settings")
        assert res_admin.status_code == status.HTTP_200_OK
        admin_data = res_admin.json()
        assert any(
            item["key"] == "storage.max_upload_size_bytes" for item in admin_data
        )

        # Admin update setting
        res_patch = await client.patch(
            "/api/settings/storage.max_upload_size_bytes",
            json={"value": 2097152},
        )
        assert res_patch.status_code == status.HTTP_200_OK
        assert res_patch.json()["value"] == 2097152

        # Public endpoint immediately reflects new value
        res_public_after = await client.get("/api/settings/public")
        assert res_public_after.json()["storage.max_upload_size_bytes"] == 2097152

    fastapi_app.dependency_overrides.clear()
    await dbsession.delete(s)
    await dbsession.flush()
    service.invalidate_cache()


@pytest.mark.anyio
async def test_storage_validation_against_system_settings(
    dbsession: AsyncSession,
) -> None:
    """Verify DocumentService validates file limits against dynamic settings."""
    settings_repo = SystemSettingRepository(dbsession)
    settings_service = SystemSettingService(settings_repo)
    settings_service.invalidate_cache()

    # Configure limits: max 500 bytes and only PDF allowed
    limit_setting = SystemSetting(
        id=generate_uuid7(),
        key="storage.max_upload_size_bytes",
        value=500,
        category="storage",
        is_public=True,
    )
    ext_setting = SystemSetting(
        id=generate_uuid7(),
        key="storage.allowed_extensions",
        value=["pdf"],
        category="storage",
        is_public=True,
    )
    dbsession.add_all([limit_setting, ext_setting])
    await dbsession.flush()

    doc_repo = DocumentRepository(dbsession)
    doc_service = DocumentService(
        repository=doc_repo,
        storage_provider=LocalStorageProvider(),
        settings_service=settings_service,
    )

    # 1. File size exceeds limit -> HTTP 400
    with pytest.raises(Exception) as exc_info:
        await doc_service.request_presigned_upload(
            PresignedUploadRequest(
                entity_type="Company",
                entity_id=generate_uuid7(),
                name="document.pdf",
                size_bytes=1000,
            )
        )
    assert "File size exceeds" in str(exc_info.value)

    # 2. File extension not allowed -> HTTP 400
    with pytest.raises(Exception) as exc_info_ext:
        await doc_service.request_presigned_upload(
            PresignedUploadRequest(
                entity_type="Company",
                entity_id=generate_uuid7(),
                name="malicious.exe",
                size_bytes=100,
            )
        )
    assert "File extension '.exe' is not permitted" in str(exc_info_ext.value)

    # 3. Valid file size and extension -> Success
    res = await doc_service.request_presigned_upload(
        PresignedUploadRequest(
            entity_type="Company",
            entity_id=generate_uuid7(),
            name="valid_doc.pdf",
            size_bytes=200,
        )
    )
    assert res.upload_url is not None
    assert res.document_id is not None

    await dbsession.delete(limit_setting)
    await dbsession.delete(ext_setting)
    await dbsession.flush()
    settings_service.invalidate_cache()
