from typing import Any, ClassVar

from fastapi import Depends, HTTPException, status

from fastapi_plantilla.modules.settings.models import SystemSetting
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.schema import SettingCreate, SettingUpdate

__all__ = ["SystemSettingService"]


class SystemSettingService:
    """Service managing system configuration with in-memory caching."""

    cache: ClassVar[dict[str, Any]] = {}
    public_cache: ClassVar[dict[str, Any] | None] = None

    def __init__(
        self,
        repository: SystemSettingRepository = Depends(),
    ) -> None:
        self.repository = repository

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear all in-memory setting caches."""
        cls.cache.clear()
        cls.public_cache = None

    async def get_value(self, key: str, default: Any = None) -> Any:
        """
        Retrieve setting value by key with memory cache fallback.

        Reads from process memory first. On cache miss, loads from PostgreSQL
        and populates the cache.
        """
        if key in self.cache:
            return self.cache[key]

        setting = await self.repository.get_by_key(key)
        if setting is None:
            return default

        self.cache[key] = setting.value
        return setting.value

    async def get_public_settings(self) -> dict[str, Any]:
        """
        Retrieve all public settings as a key-value dictionary for frontend consumption.

        Cached in process memory for zero-latency responses.
        """
        if self.public_cache is not None:
            return self.public_cache

        public_settings = await self.repository.get_all_public()
        mapping: dict[str, Any] = {}
        for s in public_settings:
            mapping[s.key] = s.value
            self.cache[s.key] = s.value

        self.__class__.public_cache = mapping
        return mapping

    async def list_settings(self, category: str | None = None) -> list[SystemSetting]:
        """List settings optionally filtered by category (Admin)."""
        if category:
            items = await self.repository.get_by_category(category)
        else:
            items = await self.repository.find_many()
        return list(items)

    async def get_setting(self, key: str) -> SystemSetting:
        """Get full setting entity by key or raise 404."""
        setting = await self.repository.get_by_key(key)
        if not setting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{key}' not found",
            )
        return setting

    async def create_setting(self, data: SettingCreate) -> SystemSetting:
        """Create a new setting and update cache (SuperAdmin)."""
        existing = await self.repository.get_by_key(data.key)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Setting '{data.key}' already exists",
            )

        setting = SystemSetting(
            key=data.key,
            value=data.value,
            description=data.description,
            category=data.category,
            is_public=data.is_public,
        )
        created = await self.repository.create(setting)

        # Update cache
        self.cache[created.key] = created.value
        if created.is_public:
            self.__class__.public_cache = None

        return created

    async def update_setting(self, key: str, data: SettingUpdate) -> SystemSetting:
        """Update setting value and metadata, automatically invalidating cache."""
        setting = await self.get_setting(key)

        update_payload: dict[str, Any] = {}
        if data.value is not None:
            update_payload["value"] = data.value
        if data.description is not None:
            update_payload["description"] = data.description
        if data.is_public is not None:
            update_payload["is_public"] = data.is_public

        if update_payload:
            updated = await self.repository.update(setting.id, update_payload)
            if updated:
                setting = updated

        # Invalidate / update cache
        self.cache[setting.key] = setting.value
        self.__class__.public_cache = None

        return setting
