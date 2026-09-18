import copy
import time
from typing import Any, ClassVar, Final

from fastapi import HTTPException, status
from loguru import logger

from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.crud.service_audit import dispatch_audit_event
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.settings.models import SystemSetting
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.schema import SettingUpdate

__all__ = [
    "DEFAULT_SETTINGS_CACHE_TTL_SECONDS",
    "SETTING_NUMERIC_CONSTRAINTS",
    "SystemSettingService",
]

DEFAULT_SETTINGS_CACHE_TTL_SECONDS: Final[float] = 60.0

SETTING_NUMERIC_CONSTRAINTS: Final[dict[str, tuple[int, int]]] = {
    "storage.max_upload_size_bytes": (1024, 1073741824),  # 1 KB to 1 GB
    "storage.max_zip_total_bytes": (1048576, 2147483648),  # 1 MB to 2 GB
    "storage.max_zip_file_count": (1, 1000),
    "storage.presigned_expiry_seconds": (60, 86400),  # 1 min to 24 h
    "storage.orphan_retention_seconds": (60, 2592000),  # 1 min to 30 days
    "auth.password_reset_expiry_minutes": (5, 1440),  # 5 min to 24 h
    "auth.magic_link_expiry_minutes": (5, 120),  # 5 min to 2 h
    "auth.email_verification_expiry_hours": (1, 168),  # 1 h to 7 days
    "auth.invitation_expiry_hours": (1, 168),  # 1 h to 7 days
    "pagination.default_page_size": (1, 100),
    "pagination.max_page_size": (10, 1000),
    "trash.retention_days": (1, 3650),  # 1 day to 10 years
    "trash.purge_limit": (1, 5000),
    "audit.retention_days": (1, 3650),
    "audit.purge_limit": (1, 5000),
    "security.rate_limit_global_requests": (1, 10000),
    "security.rate_limit_global_window_seconds": (1, 3600),
    "security.rate_limit_auth_requests": (1, 1000),
    "security.rate_limit_auth_window_seconds": (1, 3600),
}


def _get_value_category(val: Any) -> type:
    return bool if isinstance(val, bool) else type(val)


def _validate_setting_value(key: str, existing: Any, new_val: Any) -> None:
    """Validate value consistency with existing type and domain rules."""
    if existing is not None and new_val is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setting value cannot be null",
        )
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

    if key in SETTING_NUMERIC_CONSTRAINTS and isinstance(new_val, int):
        min_val, max_val = SETTING_NUMERIC_CONSTRAINTS[key]
        if not (min_val <= new_val <= max_val):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Setting '{key}' value must be between {min_val} and {max_val}",
            )

    if (
        orig_type is str
        and isinstance(existing, str)
        and existing.strip()
        and (not isinstance(new_val, str) or not new_val.strip())
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setting value cannot be an empty string",
        )


class SystemSettingService:
    """Service managing system configuration with in-memory caching and TTL."""

    cache: ClassVar[dict[str, tuple[Any, float]]] = {}
    public_cache: ClassVar[dict[str, Any] | None] = None
    public_cache_at: ClassVar[float] = 0.0
    ttl_seconds: ClassVar[float] = DEFAULT_SETTINGS_CACHE_TTL_SECONDS

    def __init__(
        self,
        repository: SystemSettingRepository,
    ) -> None:
        self.repository = repository

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear all in-memory setting caches and reset expiration timestamps."""
        cls.cache.clear()
        cls.public_cache = None
        cls.public_cache_at = 0.0

    async def get_value(
        self,
        key: str,
        default: Any = None,
        *,
        use_cache: bool = True,
    ) -> Any:
        """
        Retrieve setting value by key with memory cache fallback and TTL check.

        Reads from process memory first if within TTL and use_cache is True. On
        miss/expiration or when use_cache is False, loads directly from database,
        logs a warning on missing keys, and populates the cache.
        """
        clean_key = key.strip()
        now = time.monotonic()
        if use_cache and clean_key in self.cache:
            cached_val, cached_at = self.cache[clean_key]
            if (now - cached_at) < self.ttl_seconds:
                return copy.deepcopy(cached_val)

        setting = await self.repository.get_by_key(clean_key)
        if setting is None:
            logger.warning(
                "Setting key '{}' not found in database, using default: {}",
                clean_key,
                default,
            )
            return copy.deepcopy(default)

        self.cache[clean_key] = (copy.deepcopy(setting.value), now)
        return copy.deepcopy(setting.value)

    async def get_public_settings(self) -> dict[str, Any]:
        """
        Retrieve all public settings as a key-value dictionary for frontend.

        Cached in process memory with TTL for multi-worker freshness.
        Returns a defensive copy.
        """
        now = time.monotonic()
        if (
            self.public_cache is not None
            and (now - self.public_cache_at) < self.ttl_seconds
        ):
            return copy.deepcopy(self.public_cache)

        public_settings = await self.repository.get_all_public()
        mapping: dict[str, Any] = {}
        for s in public_settings:
            mapping[s.key] = copy.deepcopy(s.value)
            self.cache[s.key] = (copy.deepcopy(s.value), now)

        self.__class__.public_cache = mapping
        self.__class__.public_cache_at = now
        return copy.deepcopy(mapping)

    async def list_settings(self, category: str | None = None) -> list[SystemSetting]:
        """List all settings without pagination truncation (Admin)."""
        if category:
            items = await self.repository.get_by_category(category)
        else:
            items = await self.repository.get_all()
        return list(items)

    async def get_categories(self) -> list[str]:
        """List all distinct setting categories (Admin)."""
        categories = await self.repository.get_categories()
        return list(categories)

    async def get_setting(self, key: str) -> SystemSetting:
        """Get full setting entity by key or raise 404."""
        clean_key = key.strip()
        setting = await self.repository.get_by_key(clean_key)
        if not setting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{clean_key}' not found",
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
        if "value" in data.model_fields_set and setting.value != data.value:
            _validate_setting_value(setting.key, setting.value, data.value)
            update_payload["value"] = data.value
            changes["value"] = {"old": setting.value, "new": data.value}
        if (
            "description" in data.model_fields_set
            and setting.description != data.description
        ):
            clean_desc = (
                data.description.strip()
                if isinstance(data.description, str)
                else data.description
            )
            update_payload["description"] = clean_desc
            changes["description"] = {
                "old": setting.description,
                "new": clean_desc,
            }

        if update_payload:
            updated = await self.repository.update(setting.id, update_payload)
            if updated:
                setting = updated

            if changes and actor is not None:
                await dispatch_audit_event(
                    self.repository.session,
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
                    ),
                )

        # Invalidate / update cache with current timestamp
        now = time.monotonic()
        self.cache[setting.key] = (copy.deepcopy(setting.value), now)
        self.__class__.public_cache = None
        self.__class__.public_cache_at = 0.0

        return setting
