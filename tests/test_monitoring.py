from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from fastapi_plantilla.app import get_app
from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.monitoring.metrics import (
    update_db_pool_metrics,
)


@pytest.mark.anyio
async def test_metrics_endpoint_returns_openmetrics(client: AsyncClient) -> None:
    """Validate that GET /metrics returns 200 with standard OpenMetrics Content-Type."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


@pytest.mark.anyio
async def test_http_requests_total_increments_on_api_call(
    client: AsyncClient,
) -> None:
    """Verify that calling an API route increments HTTP_REQUESTS_TOTAL."""
    # /api/companies/list without auth returns 401
    resp = await client.get("/api/companies/list")
    assert resp.status_code == 401

    metrics_resp = await client.get("/metrics")
    assert metrics_resp.status_code == 200
    body = metrics_resp.text
    assert 'endpoint="/api/companies/list"' in body
    assert 'status_code="401"' in body


@pytest.mark.anyio
async def test_route_normalization_anti_cardinality(client: AsyncClient) -> None:
    """Verify route template normalization and unmatched path handling."""
    fake_uuid = "01923456-789a-7b3c-8d4e-5f60718293a4"
    resp = await client.get(f"/api/companies/{fake_uuid}")
    assert resp.status_code in (401, 404)

    # Call a non-existent path
    unmatched_resp = await client.get("/api/does-not-exist/random/test")
    assert unmatched_resp.status_code == 404

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.text

    # Route template must be used, not raw UUID
    assert fake_uuid not in body
    assert 'endpoint="/api/companies/{id}"' in body
    assert 'endpoint="unmatched"' in body


@pytest.mark.anyio
async def test_excluded_paths_not_tracked(client: AsyncClient) -> None:
    """Verify that /metrics, /api/health/live and /ready are excluded from metrics."""
    await client.get("/api/health/live")
    await client.get("/api/health/ready")
    await client.get("/metrics")

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.text

    assert 'endpoint="/api/health/live"' not in body
    assert 'endpoint="/api/health/ready"' not in body
    assert 'endpoint="/metrics"' not in body


@pytest.mark.anyio
async def test_db_pool_metrics_reported(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Verify that SQLAlchemy connection pool gauges are introspected and emitted."""
    update_db_pool_metrics(fastapi_app)

    metrics_resp = await client.get("/metrics")
    body = metrics_resp.text

    assert "db_pool_size" in body
    assert "db_pool_checked_in_connections" in body
    assert "db_pool_checked_out_connections" in body
    assert "db_pool_overflow_connections" in body


@pytest.mark.anyio
async def test_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when prometheus_enabled is False, /metrics is not mounted."""
    monkeypatch.setattr(settings, "prometheus_enabled", False)
    app = get_app()

    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as disabled_client:
        resp = await disabled_client.get("/metrics")
        assert resp.status_code == 404

        # Regular routes still work seamlessly
        health_resp = await disabled_client.get("/api/health/live")
        assert health_resp.status_code == 200


@pytest.mark.anyio
async def test_sse_compatibility() -> None:
    """Verify that pure ASGI middleware does not interfere with streaming responses."""
    app = get_app()

    async def event_generator() -> Any:
        yield b"event: ping\ndata: 1\n\n"
        yield b"event: ping\ndata: 2\n\n"

    @app.get("/test-streaming")
    async def stream_route() -> StreamingResponse:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as stream_client:
        resp = await stream_client.get("/test-streaming")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        content = resp.text
        assert "event: ping\ndata: 1" in content
        assert "event: ping\ndata: 2" in content
