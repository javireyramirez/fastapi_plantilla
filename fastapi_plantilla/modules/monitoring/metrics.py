import os
from typing import Any

from fastapi import FastAPI
from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    multiprocess,
)

LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

# HTTP metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total count of HTTP requests processed by the application.",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "Histogram of HTTP request processing duration in seconds.",
    ["method", "endpoint"],
    buckets=LATENCY_BUCKETS,
)

# Database Connection Pool metrics
DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured base size of the database connection pool.",
)

DB_POOL_CHECKED_IN = Gauge(
    "db_pool_checked_in_connections",
    "Number of database connections currently idle in the pool.",
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out_connections",
    "Number of database connections currently checked out (in use).",
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow_connections",
    "Number of database connections opened beyond the base pool size.",
)


def get_registry() -> CollectorRegistry:
    """
    Provide the active Prometheus registry.

    Supports multiprocess aggregation (e.g. Gunicorn multi-worker) when
    PROMETHEUS_MULTIPROC_DIR is defined in the environment.
    """
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def update_db_pool_metrics(app: FastAPI) -> None:
    """
    Inspect the SQLAlchemy engine pool and safely update gauges.

    Handles AsyncAdaptedQueuePool, QueuePool, NullPool, or missing state
    defensively without raising exceptions.
    """
    engine: Any = getattr(app.state, "db_engine", None)
    if not engine:
        return

    try:
        sync_engine = getattr(engine, "sync_engine", None)
        pool = getattr(sync_engine, "pool", None) if sync_engine else None
        if pool is None:
            return

        size = getattr(pool, "size", lambda: 0)()
        checked_in = getattr(pool, "checkedin", lambda: 0)()
        checked_out = getattr(pool, "checkedout", lambda: 0)()
        overflow = getattr(pool, "overflow", lambda: 0)()

        DB_POOL_SIZE.set(size if isinstance(size, (int, float)) else 0)
        DB_POOL_CHECKED_IN.set(
            checked_in if isinstance(checked_in, (int, float)) else 0
        )
        DB_POOL_CHECKED_OUT.set(
            checked_out if isinstance(checked_out, (int, float)) else 0
        )
        DB_POOL_OVERFLOW.set(overflow if isinstance(overflow, (int, float)) else 0)
    except Exception:  # noqa: S110
        # Defensive fallback: do not crash metric scraping if pool introspection fails
        pass
