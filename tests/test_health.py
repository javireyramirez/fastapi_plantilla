from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.settings.dependencies import get_settings_service


async def test_liveness(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Check liveness probe returns 200 OK and generates X-Request-ID."""
    url = fastapi_app.url_path_for("live_probe")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


async def test_readiness_healthy(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Check readiness probe returns 200 OK with database metrics."""
    url = fastapi_app.url_path_for("ready_probe")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["database"]["status"] == "ok"
    assert isinstance(data["checks"]["database"]["latency_ms"], float | int)


async def test_readiness_unhealthy(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Check readiness probe returns 503 Service Unavailable when DB fails."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.side_effect = ConnectionRefusedError(
        "Database connection lost"
    )

    async def mock_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_get_db_session

    try:
        url = fastapi_app.url_path_for("ready_probe")
        response = await client.get(url)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"]["status"] == "error"
        assert "Database connection lost" in data["checks"]["database"]["error"]
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


async def test_request_id_propagation(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify that an incoming X-Request-ID is preserved in the response."""
    url = fastapi_app.url_path_for("live_probe")
    custom_id = "test-custom-request-id-12345"
    response = await client.get(url, headers={"X-Request-ID": custom_id})
    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("X-Request-ID") == custom_id


async def test_readiness_error_sanitization_in_production(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Ensure database errors are sanitized in production to prevent leakage."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.side_effect = ConnectionRefusedError(
        "Sensitive internal connection details: host=10.0.0.5 user=postgres"
    )

    async def mock_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_get_db_session

    try:
        url = fastapi_app.url_path_for("ready_probe")
        with patch.object(settings, "environment", "production"):
            response = await client.get(url)
            assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["checks"]["database"]["status"] == "error"
            # Sensitive internal details must not be exposed
            assert "10.0.0.5" not in data["checks"]["database"]["error"]
            assert data["checks"]["database"]["error"] == (
                "Database connectivity check failed"
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


async def test_readiness_maintenance_mode_triggers_503(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Ensure active maintenance mode causes readiness probe to return 503."""
    mock_settings_service = AsyncMock()
    mock_settings_service.get_value.return_value = True

    fastapi_app.dependency_overrides[get_settings_service] = lambda: (
        mock_settings_service
    )

    try:
        url = fastapi_app.url_path_for("ready_probe")
        response = await client.get(url)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["maintenance_mode"] is True
        assert data["checks"]["database"]["status"] == "ok"
        mock_settings_service.get_value.assert_called_once_with(
            "app.maintenance_mode", default=False, use_cache=False
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_settings_service, None)


def test_health_module_exports() -> None:
    """Verify health module exposes clean facade via __all__."""
    import fastapi_plantilla.modules.health as health_pkg

    expected = {
        "ComponentCheck",
        "ComponentStatus",
        "DB_CHECK_TIMEOUT_SECONDS",
        "HealthStatus",
        "LivenessResponse",
        "ReadinessResponse",
        "router",
    }
    assert set(health_pkg.__all__) == expected
    for name in expected:
        assert hasattr(health_pkg, name)
    assert health_pkg.DB_CHECK_TIMEOUT_SECONDS == 2.0


@pytest.mark.anyio
async def test_readiness_s3_storage_check(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify readiness probe checks S3 storage when backend is S3."""
    from fastapi_plantilla.core.config import StorageBackend
    from fastapi_plantilla.modules.storage.providers.s3 import S3StorageProvider

    monkeypatch.setattr(settings, "storage_backend", StorageBackend.S3)

    mock_provider = AsyncMock(spec=S3StorageProvider)
    mock_provider.check_bucket_exists.return_value = True

    with patch(
        "fastapi_plantilla.modules.storage.dependencies.get_storage_provider",
        return_value=mock_provider,
    ):
        url = fastapi_app.url_path_for("ready_probe")
        response = await client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ok"
        assert data["checks"]["storage"]["status"] == "ok"

    mock_provider.check_bucket_exists.return_value = False

    with patch(
        "fastapi_plantilla.modules.storage.dependencies.get_storage_provider",
        return_value=mock_provider,
    ):
        response = await client.get(url)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["storage"]["status"] == "error"
