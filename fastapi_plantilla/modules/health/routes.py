import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", summary="Liveness Probe")
def live_probe() -> dict[str, str]:
    """Liveness probe to confirm the server process is alive and responsive."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness Probe")
async def ready_probe(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Readiness probe to verify database and dependency connectivity."""
    checks: dict[str, Any] = {}
    is_healthy = True

    try:
        start_time = time.perf_counter()
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        checks["database"] = {"status": "ok", "latency_ms": latency_ms}
    except TimeoutError:
        is_healthy = False
        checks["database"] = {
            "status": "error",
            "error": "Database query timed out (exceeded 2.0s)",
        }
    except Exception as exc:
        is_healthy = False
        checks["database"] = {"status": "error", "error": str(exc)}

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unhealthy", "checks": checks}

    return {"status": "ok", "checks": checks}
