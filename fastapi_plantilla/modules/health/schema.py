import enum

from pydantic import BaseModel, Field

__all__ = [
    "ComponentCheck",
    "ComponentStatus",
    "HealthStatus",
    "LivenessResponse",
    "ReadinessResponse",
]


class HealthStatus(enum.StrEnum):
    """Overall system health status."""

    OK = "ok"
    UNHEALTHY = "unhealthy"


class ComponentStatus(enum.StrEnum):
    """Health status of an individual dependency component."""

    OK = "ok"
    ERROR = "error"


class ComponentCheck(BaseModel):
    """Health check result for an individual dependency component."""

    status: ComponentStatus
    latency_ms: float | None = Field(
        default=None,
        description="Response latency in milliseconds if applicable",
    )
    error: str | None = Field(
        default=None,
        description="Error description if the component check failed",
    )


class LivenessResponse(BaseModel):
    """Response schema for liveness probe."""

    status: HealthStatus = Field(
        default=HealthStatus.OK,
        description="Server process liveness indicator",
    )


class ReadinessResponse(BaseModel):
    """Response schema for readiness probe."""

    status: HealthStatus = Field(
        description="Overall readiness indicator",
    )
    checks: dict[str, ComponentCheck] = Field(
        default_factory=dict,
        description="Health check outcomes for critical dependencies",
    )
    maintenance_mode: bool = Field(
        default=False,
        description="Flag indicating if platform maintenance mode is currently active",
    )
    environment: str = Field(
        description="Runtime environment identifier",
    )
    app_name: str = Field(
        description="Application name identifier",
    )
