from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fastapi_plantilla.modules.monitoring.metrics import (
    get_registry,
    update_db_pool_metrics,
)


async def metrics_endpoint(request: Request) -> Response:
    """
    Expose Prometheus / OpenMetrics scrape target.

    Updates dynamic pool metrics from current engine state and serializes
    all registered collectors into OpenMetrics standard text format.
    """
    update_db_pool_metrics(request.app)
    registry = get_registry()
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
