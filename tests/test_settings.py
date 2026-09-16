from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.audit.models import AuditLog
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
from fastapi_plantilla.modules.storage.repository import StorageRepository
from fastapi_plantilla.modules.storage.schema import PresignedUploadRequest
from fastapi_plantilla.modules.storage.service import StorageService


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

    # 2b. Bypassing cache loads fresh DB value immediately
    # without invalidating the whole in-memory cache
    bypass_val = await service.get_value("test.timeout", use_cache=False)
    assert bypass_val == 60

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
            json={"value": 2097152, "is_public": False},
        )
        assert res_patch.status_code == status.HTTP_200_OK
        assert res_patch.json()["value"] == 2097152
        # is_public is immutable and cannot be changed via update
        assert res_patch.json()["is_public"] is True

        # Verify audit log was recorded for the setting update
        audit_stmt = select(AuditLog).where(
            AuditLog.entity_type == "settings",
            AuditLog.entity_id == s.id,
            AuditLog.action == "UPDATE",
        )
        audit_res = await dbsession.execute(audit_stmt)
        audit_log = audit_res.scalar_one_or_none()
        assert audit_log is not None
        assert audit_log.actor_id == superuser.id
        assert audit_log.actor_name == superuser.name
        assert audit_log.entity_name == "storage.max_upload_size_bytes"
        assert audit_log.changes == {"value": {"old": 1048576, "new": 2097152}}

        # Type validation: reject string value for integer setting
        res_invalid_type = await client.patch(
            "/api/settings/storage.max_upload_size_bytes",
            json={"value": "invalid_string"},
        )
        assert res_invalid_type.status_code == status.HTTP_400_BAD_REQUEST

        # Type validation: reject negative integer
        res_negative = await client.patch(
            "/api/settings/storage.max_upload_size_bytes",
            json={"value": -100},
        )
        assert res_negative.status_code == status.HTTP_400_BAD_REQUEST

        # Public endpoint immediately reflects new valid value
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
    """Verify StorageService validates file limits against dynamic settings."""
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

    storage_repo = StorageRepository(dbsession)
    storage_service = StorageService(
        repository=storage_repo,
        storage_provider=LocalStorageProvider(),
        settings_service=settings_service,
    )

    # 1. File size exceeds limit -> HTTP 400
    with pytest.raises(Exception) as exc_info:
        await storage_service.request_presigned_upload(
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
        await storage_service.request_presigned_upload(
            PresignedUploadRequest(
                entity_type="Company",
                entity_id=generate_uuid7(),
                name="malicious.exe",
                size_bytes=100,
            )
        )
    assert "File extension '.exe' is not permitted" in str(exc_info_ext.value)

    # 3. Valid file size and extension -> Success
    res = await storage_service.request_presigned_upload(
        PresignedUploadRequest(
            entity_type="Company",
            entity_id=generate_uuid7(),
            name="valid_doc.pdf",
            size_bytes=200,
        )
    )
    assert res.upload_url is not None
    assert res.storage_id is not None

    await dbsession.delete(limit_setting)
    await dbsession.delete(ext_setting)
    await dbsession.flush()
    settings_service.invalidate_cache()


@pytest.mark.anyio
async def test_get_export_formats(client: AsyncClient) -> None:
    """Verify GET /api/settings/export-formats returns supported formats list."""
    response = await client.get("/api/settings/export-formats")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)
    assert "csv" in data
    assert "json" in data


@pytest.mark.anyio
async def test_settings_cache_defensive_copy(dbsession: AsyncSession) -> None:
    """Verify that mutable values retrieved from cache are defensively copied."""
    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)
    service.invalidate_cache()

    s = SystemSetting(
        id=generate_uuid7(),
        key="test.mutable_list",
        value=["alpha", "beta"],
        category="general",
        is_public=True,
    )
    dbsession.add(s)
    await dbsession.flush()

    # 1. First get_value returns copy
    val1 = await service.get_value("test.mutable_list")
    assert val1 == ["alpha", "beta"]

    # 2. Mutate returned list in-place
    val1.append("gamma")
    assert val1 == ["alpha", "beta", "gamma"]

    # 3. Subsequent get_value is NOT contaminated
    val2 = await service.get_value("test.mutable_list")
    assert val2 == ["alpha", "beta"]

    # 4. Same defensive behavior on public_settings
    pub_map1 = await service.get_public_settings()
    pub_map1["test.mutable_list"].append("corrupted")

    pub_map2 = await service.get_public_settings()
    assert pub_map2["test.mutable_list"] == ["alpha", "beta"]

    await dbsession.delete(s)
    await dbsession.flush()
    service.invalidate_cache()


@pytest.mark.anyio
async def test_get_settings_categories_endpoint(
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Verify GET /api/settings/categories returns distinct sorted categories."""
    superuser = _mock_user(is_super=True)
    fastapi_app.dependency_overrides[get_current_active_superuser] = lambda: superuser

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/settings/categories")
        assert res.status_code == status.HTTP_200_OK
        cats = res.json()
        assert isinstance(cats, list)
        assert cats == sorted(cats)

    fastapi_app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_settings_validation_null_and_empty(
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Verify validation rules reject null or empty strings when inappropriate."""
    s = SystemSetting(
        id=generate_uuid7(),
        key="test.non_null_str",
        value="valid_string",
        category="general",
        is_public=False,
    )
    dbsession.add(s)
    await dbsession.flush()

    superuser = _mock_user(is_super=True)
    fastapi_app.dependency_overrides[get_current_active_superuser] = lambda: superuser

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Null value rejection
        res_null = await client.patch(
            "/api/settings/test.non_null_str",
            json={"value": None},
        )
        assert res_null.status_code == status.HTTP_400_BAD_REQUEST
        assert "Setting value cannot be null" in res_null.json()["detail"]

        # Empty string rejection
        res_empty = await client.patch(
            "/api/settings/test.non_null_str",
            json={"value": "   "},
        )
        assert res_empty.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot be an empty string" in res_empty.json()["detail"]

    fastapi_app.dependency_overrides.clear()
    await dbsession.delete(s)
    await dbsession.flush()


@pytest.mark.anyio
async def test_list_settings_returns_all_without_truncation(
    dbsession: AsyncSession,
) -> None:
    """Verify list_settings returns all settings without 10-item truncation bug."""
    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)

    # Insert 15 items to definitively exceed the 10-item default
    created = []
    for i in range(15):
        st = SystemSetting(
            id=generate_uuid7(),
            key=f"bulk.test.key_{i:02d}",
            value=i,
            category="bulk_test",
            is_public=False,
        )
        created.append(st)
        dbsession.add(st)
    await dbsession.flush()

    all_items = await service.list_settings()
    # Must contain at least all 15 newly created items + seeds
    bulk_items = [s for s in all_items if s.category == "bulk_test"]
    assert len(bulk_items) == 15
    assert len(all_items) >= 15

    for st in created:
        await dbsession.delete(st)
    await dbsession.flush()


@pytest.mark.anyio
async def test_settings_cache_ttl_expiration(dbsession: AsyncSession) -> None:
    """Verify in-memory cache expires and refreshes from database after TTL."""
    import asyncio

    from fastapi_plantilla.modules.settings.service import (
        DEFAULT_SETTINGS_CACHE_TTL_SECONDS,
    )

    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)
    service.invalidate_cache()

    s = SystemSetting(
        id=generate_uuid7(),
        key="test.ttl_setting",
        value="initial_value",
        category="general",
        is_public=True,
    )
    dbsession.add(s)
    await dbsession.flush()

    # Shorten TTL to 50ms for testing
    service.__class__.ttl_seconds = 0.05
    try:
        # First read caches initial value
        val1 = await service.get_value("test.ttl_setting")
        assert val1 == "initial_value"

        # Update in database directly behind the cache's back
        s.value = "updated_in_db"
        await dbsession.flush()

        # Immediate read returns cached value (still within 50ms)
        assert await service.get_value("test.ttl_setting") == "initial_value"

        # Wait for TTL to expire
        await asyncio.sleep(0.06)

        # Post-TTL read automatically refreshes from database
        val_refreshed = await service.get_value("test.ttl_setting")
        assert val_refreshed == "updated_in_db"
    finally:
        service.__class__.ttl_seconds = DEFAULT_SETTINGS_CACHE_TTL_SECONDS
        await dbsession.delete(s)
        await dbsession.flush()
        service.invalidate_cache()


@pytest.mark.anyio
async def test_settings_numeric_domain_constraints(
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Verify numeric domain constraints reject out-of-range values."""
    s = SystemSetting(
        id=generate_uuid7(),
        key="trash.purge_limit",
        value=500,
        category="trash",
        is_public=False,
    )
    dbsession.add(s)
    await dbsession.flush()

    superuser = _mock_user(is_super=True)
    fastapi_app.dependency_overrides[get_current_active_superuser] = lambda: superuser

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Reject purge_limit > 5000
        res_high = await client.patch(
            "/api/settings/trash.purge_limit",
            json={"value": 10000},
        )
        assert res_high.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be between 1 and 5000" in res_high.json()["detail"]

        # Reject purge_limit < 1 (e.g. 0)
        res_low = await client.patch(
            "/api/settings/trash.purge_limit",
            json={"value": 0},
        )
        assert res_low.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be between 1 and 5000" in res_low.json()["detail"]

        # Accept valid limit within range
        res_valid = await client.patch(
            "/api/settings/trash.purge_limit",
            json={"value": 1000},
        )
        assert res_valid.status_code == status.HTTP_200_OK
        assert res_valid.json()["value"] == 1000

        # storage.max_zip_total_bytes constraints: 1 MB to 2 GB
        s_zip = SystemSetting(
            id=generate_uuid7(),
            key="storage.max_zip_total_bytes",
            value=104857600,
            category="storage",
            is_public=True,
        )
        dbsession.add(s_zip)
        await dbsession.flush()

        res_zip_low = await client.patch(
            "/api/settings/storage.max_zip_total_bytes",
            json={"value": 500},
        )
        assert res_zip_low.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be between 1048576 and 2147483648" in res_zip_low.json()["detail"]

        res_zip_high = await client.patch(
            "/api/settings/storage.max_zip_total_bytes",
            json={"value": 3000000000},
        )
        assert res_zip_high.status_code == status.HTTP_400_BAD_REQUEST

        # storage.orphan_retention_seconds constraints: 60 to 2592000
        s_orphan = SystemSetting(
            id=generate_uuid7(),
            key="storage.orphan_retention_seconds",
            value=86400,
            category="storage",
            is_public=False,
        )
        dbsession.add(s_orphan)
        await dbsession.flush()

        res_orphan_low = await client.patch(
            "/api/settings/storage.orphan_retention_seconds",
            json={"value": 10},
        )
        assert res_orphan_low.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be between 60 and 2592000" in res_orphan_low.json()["detail"]

        res_orphan_valid = await client.patch(
            "/api/settings/storage.orphan_retention_seconds",
            json={"value": 604800},
        )
        assert res_orphan_valid.status_code == status.HTTP_200_OK
        assert res_orphan_valid.json()["value"] == 604800

    fastapi_app.dependency_overrides.clear()
    await dbsession.delete(s)
    await dbsession.delete(s_zip)
    await dbsession.delete(s_orphan)
    await dbsession.flush()


@pytest.mark.anyio
async def test_settings_cache_miss_returns_default(dbsession: AsyncSession) -> None:
    """Verify cache miss returns default and doesn't crash."""
    repo = SystemSettingRepository(dbsession)
    service = SystemSettingService(repo)

    result = await service.get_value("non.existent.typo.key", default="fallback_val")
    assert result == "fallback_val"
