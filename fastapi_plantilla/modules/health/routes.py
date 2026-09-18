import asyncio
import time

from fastapi import APIRouter, Depends, Response, status
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import StorageBackend, settings
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.health.schema import (
    ComponentCheck,
    ComponentStatus,
    HealthStatus,
    LivenessResponse,
    ReadinessResponse,
)
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.service import SystemSettingService

DB_CHECK_TIMEOUT_SECONDS: float = 2.0

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness Probe",
    description="Liveness probe to confirm the server process is alive and responsive.",
)
def live_probe() -> LivenessResponse:
    """Liveness probe to confirm the server process is alive and responsive."""
    return LivenessResponse(status=HealthStatus.OK)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
    description=(
        "Readiness probe to verify database connectivity and dependency health."
    ),
)
async def ready_probe(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    settings_service: SystemSettingService | None = Depends(get_settings_service),
) -> ReadinessResponse:
    """Readiness probe to verify database, dependencies, and maintenance status."""
    checks: dict[str, ComponentCheck] = {}
    is_healthy = True

    try:
        start_time = time.perf_counter()
        await asyncio.wait_for(
            db.execute(text("SELECT 1")), timeout=DB_CHECK_TIMEOUT_SECONDS
        )
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        checks["database"] = ComponentCheck(
            status=ComponentStatus.OK,
            latency_ms=latency_ms,
        )
    except TimeoutError:
        is_healthy = False
        logger.error(
            "Healthcheck readiness: database query timed out after {}s",
            DB_CHECK_TIMEOUT_SECONDS,
        )
        checks["database"] = ComponentCheck(
            status=ComponentStatus.ERROR,
            error=f"Database query timed out (exceeded {DB_CHECK_TIMEOUT_SECONDS}s)",
        )
    except Exception as exc:
        is_healthy = False
        logger.error(f"Healthcheck readiness: database connection error: {exc}")
        err_msg = (
            str(exc)
            if settings.environment in ("development", "test", "dev", "pytest")
            else "Database connectivity check failed"
        )
        checks["database"] = ComponentCheck(
            status=ComponentStatus.ERROR,
            error=err_msg,
        )

    if settings.storage_backend == StorageBackend.S3:
        try:
            from fastapi_plantilla.modules.storage.dependencies import (  # noqa: PLC0415
                get_storage_provider,
            )

            storage_prov = get_storage_provider()
            if hasattr(storage_prov, "check_bucket_exists"):
                bucket_exists = await storage_prov.check_bucket_exists()
                if not bucket_exists:
                    is_healthy = False
                    checks["storage"] = ComponentCheck(
                        status=ComponentStatus.ERROR,
                        error=(
                            f"Bucket '{settings.storage_bucket}' does not exist "
                            "or is not accessible"
                        ),
                    )
                else:
                    checks["storage"] = ComponentCheck(status=ComponentStatus.OK)
        except Exception as exc:
            is_healthy = False
            logger.error(f"Healthcheck readiness: storage check error: {exc}")
            checks["storage"] = ComponentCheck(
                status=ComponentStatus.ERROR,
                error=str(exc) if settings.is_dev else "Storage check failed",
            )

    maintenance_mode = False
    if is_healthy and settings_service is not None:
        try:
            maintenance_mode = bool(
                await settings_service.get_value(
                    "app.maintenance_mode", default=False, use_cache=False
                )
            )
        except Exception as exc:
            logger.warning(
                f"Could not query maintenance_mode during healthcheck: {exc}"
            )

    overall_status = (
        HealthStatus.OK
        if is_healthy and not maintenance_mode
        else HealthStatus.UNHEALTHY
    )

    if overall_status == HealthStatus.UNHEALTHY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status=overall_status,
        checks=checks,
        maintenance_mode=maintenance_mode,
        environment=settings.environment,
        app_name=settings.app_name,
    )
