from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.api_keys.dependencies import (
    require_api_key_scope,
)
from fastapi_plantilla.modules.api_keys.repository import ApiKeyRepository
from fastapi_plantilla.modules.api_keys.schema import ApiKeyCreate
from fastapi_plantilla.modules.api_keys.service import ApiKeyService
from fastapi_plantilla.modules.auth.models import User


@pytest.mark.anyio
async def test_api_key_generation_and_validation(dbsession: AsyncSession) -> None:
    """Test API key creation produces raw token and hash validates correctly."""
    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "name": "API Key Owner",
            "email": "keyowner@example.com",
            "is_active": True,
            "email_verified": True,
        }
    )

    repo = ApiKeyRepository(dbsession)
    service = ApiKeyService(repo)

    api_key, raw_key = await service.create_key(
        schema=ApiKeyCreate(name="Stripe Webhooks", scopes=["webhooks:read"]),
        owner_id=user.id,
    )

    assert raw_key.startswith("ak_live_")
    assert api_key.name == "Stripe Webhooks"
    assert api_key.key_hash == ApiKeyService.hash_key(raw_key)
    assert api_key.masked_key.startswith("ak_live_")
    assert "..." in api_key.masked_key

    # Valid validation
    validated = await service.validate_key(raw_key)
    assert validated is not None
    assert validated.id == api_key.id
    assert validated.last_used_at is not None

    # Invalid validation
    tampered_key = raw_key[:-4] + "xxxx"
    assert await service.validate_key(tampered_key) is None
    assert await service.validate_key("non-existent-key") is None


@pytest.mark.anyio
async def test_api_key_expiration(dbsession: AsyncSession) -> None:
    """Test expired API key is rejected by validate_key."""
    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "name": "Expired Key Owner",
            "email": "expired@example.com",
            "is_active": True,
            "email_verified": True,
        }
    )

    repo = ApiKeyRepository(dbsession)
    service = ApiKeyService(repo)

    # Key that expired 1 hour ago
    past = datetime.now(UTC) - timedelta(hours=1)
    _api_key, raw_key = await service.create_key(
        schema=ApiKeyCreate(name="Expired Key", scopes=["*"], expires_at=past),
        owner_id=user.id,
    )

    validated = await service.validate_key(raw_key)
    assert validated is None


@pytest.mark.anyio
async def test_require_api_key_scope(dbsession: AsyncSession) -> None:
    """Test require_api_key_scope checks for specific scope or wildcard."""
    from fastapi import HTTPException

    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "name": "Scope Test User",
            "email": "scopetest@example.com",
            "is_active": True,
            "email_verified": True,
        }
    )

    repo = ApiKeyRepository(dbsession)
    service = ApiKeyService(repo)

    # Key with limited scope
    key_scoped, _ = await service.create_key(
        schema=ApiKeyCreate(name="Reports Key", scopes=["reports:read"]),
        owner_id=user.id,
    )

    # Key with wildcard scope
    key_wildcard, _ = await service.create_key(
        schema=ApiKeyCreate(name="Admin Key", scopes=["*"]),
        owner_id=user.id,
    )

    checker_reports = require_api_key_scope("reports:read")
    checker_billing = require_api_key_scope("billing:write")

    # Scoped key matches reports:read
    res = await checker_reports(api_key=key_scoped)
    assert res.id == key_scoped.id

    # Scoped key fails billing:write with 403
    with pytest.raises(HTTPException) as exc_info:
        await checker_billing(api_key=key_scoped)
    assert exc_info.value.status_code == 403

    # Wildcard key passes billing:write
    res = await checker_billing(api_key=key_wildcard)
    assert res.id == key_wildcard.id


@pytest.mark.anyio
async def test_auth_dependencies_coexistence_with_api_key(
    dbsession: AsyncSession,
    client: AsyncClient,
) -> None:
    """Test get_current_user authenticates via X-API-Key or Bearer ak_."""
    user_repo = BaseRepository(User, dbsession)
    user = await user_repo.create(
        {
            "name": "Federated API User",
            "email": "feduser@example.com",
            "is_active": True,
            "email_verified": True,
            "is_super_admin": True,
        }
    )

    repo = ApiKeyRepository(dbsession)
    service = ApiKeyService(repo)
    _, raw_key = await service.create_key(
        schema=ApiKeyCreate(name="Federated Service Key", scopes=["*"]),
        owner_id=user.id,
    )

    # 1. Using X-API-Key header to hit a protected user endpoint
    res = await client.get("/api/users/list", headers={"X-API-Key": raw_key})
    assert res.status_code == 200

    # 2. Using Authorization: Bearer ak_live_... header
    auth_hdr = {"Authorization": f"Bearer {raw_key}"}
    res2 = await client.get("/api/users/list", headers=auth_hdr)
    assert res2.status_code == 200

    # 3. Invalid API key returns 401
    invalid_hdr = {"X-API-Key": "ak_live_invalidkey123"}
    res3 = await client.get("/api/users/list", headers=invalid_hdr)
    assert res3.status_code == 401
