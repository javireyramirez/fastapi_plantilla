from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from fastapi_plantilla.core.database import get_db_session


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
