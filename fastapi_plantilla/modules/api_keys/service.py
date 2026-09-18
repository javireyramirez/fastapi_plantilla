import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from fastapi_plantilla.core.crud.schema import ScopeContext, WriteOptions
from fastapi_plantilla.core.crud.service_owned import BaseOwnedService
from fastapi_plantilla.modules.api_keys.models import ApiKey
from fastapi_plantilla.modules.api_keys.repository import ApiKeyRepository
from fastapi_plantilla.modules.api_keys.schema import ApiKeyCreate

__all__ = ["ApiKeyService"]


class ApiKeyService(BaseOwnedService[ApiKey]):
    """Service for managing external API keys with multi-tenant ownership."""

    owner_field: str = "owner_id"

    def __init__(self, repository: ApiKeyRepository) -> None:
        super().__init__(repository)
        self.repository: ApiKeyRepository = repository

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """Derive authoritative SHA-256 digest for an API Key."""
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    async def create_key(
        self,
        schema: ApiKeyCreate,
        owner_id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> tuple[ApiKey, str]:
        """Generate a cryptographically secure API key and persist its hash."""
        token = secrets.token_urlsafe(32)
        raw_key = f"ak_live_{token}"
        key_hash = self.hash_key(raw_key)
        masked_key = f"ak_live_{token[:4]}...{token[-4:]}"

        data = {
            "name": schema.name,
            "prefix": "ak_live_",
            "key_hash": key_hash,
            "masked_key": masked_key,
            "owner_id": owner_id,
            "scopes": schema.scopes or ["*"],
            "is_active": True,
            "expires_at": schema.expires_at,
        }
        saved = await self.create(
            data, owner_id=owner_id, options=WriteOptions(scope=scope)
        )
        return saved, raw_key

    async def validate_key(self, raw_key: str) -> ApiKey | None:
        """Validate raw API Key against stored hash using constant-time comparison."""
        computed_hash = self.hash_key(raw_key)
        api_key = await self.repository.get_by_key_hash(computed_hash)
        if not api_key or not api_key.is_active:
            return None

        # Constant-time comparison
        if not secrets.compare_digest(api_key.key_hash, computed_hash):
            return None

        # Expiration check
        if api_key.expires_at is not None:
            now = datetime.now(UTC)
            expires = (
                api_key.expires_at.replace(tzinfo=UTC)
                if api_key.expires_at.tzinfo is None
                else api_key.expires_at
            )
            if now > expires:
                return None

        # Update last used timestamp
        await self.repository.update_last_used(api_key.id)
        return api_key
