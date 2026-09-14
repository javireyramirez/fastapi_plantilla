from typing import Any, ClassVar

from fastapi import Depends, HTTPException, status

from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.settings.models import SystemSetting
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.schema import SettingUpdate

__all__ = ["SystemSettingService"]


def _get_value_category(val: Any) -> type:
    return bool if isinstance(val, bool) else type(val)


def _validate_setting_value(existing: Any, new_val: Any) -> None:
    """Validate that new value type is consistent with existing setting type."""
    if existing is None or new_val is None:
        return

    orig_type = _get_value_category(existing)
    new_type = _get_value_category(new_val)

    if orig_type in (bool, int, list, str, dict) and orig_type is not new_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected {orig_type.__name__} value, got {new_type.__name__}",
        )

    if orig_type is int and isinstance(new_val, int) and new_val < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integer value cannot be negative",
        )


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

    async def update_setting(
        self,
        key: str,
        data: SettingUpdate,
        actor: UserResponse | None = None,
    ) -> SystemSetting:
        """Update setting value and metadata, invalidating cache and recording audit."""
        setting = await self.get_setting(key)

        update_payload: dict[str, Any] = {}
        changes: dict[str, Any] = {}
        if data.value is not None and setting.value != data.value:
            _validate_setting_value(setting.value, data.value)
            update_payload["value"] = data.value
            changes["value"] = {"old": setting.value, "new": data.value}
        if data.description is not None and setting.description != data.description:
            update_payload["description"] = data.description
            changes["description"] = {
                "old": setting.description,
                "new": data.description,
            }

        if update_payload:
            updated = await self.repository.update(setting.id, update_payload)
            if updated:
                setting = updated

            if changes and actor is not None:
                # Deferred import to break circular import between settings and audit
                from fastapi_plantilla.modules.audit.repository import (  # noqa: PLC0415
                    AuditRepository,
                )

                audit_repo = AuditRepository(self.repository.session)
                await audit_repo.record_entry(
                    AuditEntry(
                        entity_type="settings",
                        entity_id=setting.id,
                        entity_name=setting.key,
                        action="UPDATE",
                        actor_id=actor.id,
                        actor_name=actor.name,
                        actor_email=actor.email,
                        changes=changes,
                        details=f"System setting '{setting.key}' updated",
                    )
                )

        # Invalidate / update cache
        self.cache[setting.key] = setting.value
        self.__class__.public_cache = None

        return setting
