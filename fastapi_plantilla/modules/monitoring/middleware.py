import re
import time
from typing import Any

from fastapi import FastAPI
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.monitoring.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)

EXCLUDED_PATHS = frozenset(
    {
        "/metrics",
        "/api/health/live",
        "/api/health/ready",
    }
)


class PrometheusASGIMiddleware:
    """
    Pure ASGI middleware for Prometheus HTTP metrics collection.

    Does not buffer response bodies, ensuring full compatibility with
    Server-Sent Events (SSE) and streaming responses.
    Prevents cardinality explosion by matching incoming requests against
    registered FastAPI and Starlette route templates.
    """

    def __init__(self, app: ASGIApp, fastapi_app: FastAPI) -> None:
        self.app = app
        self.fastapi_app = fastapi_app
        self.route_map: list[tuple[Any, str, set[str]]] = self._build_route_map(
            fastapi_app
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process incoming ASGI HTTP request and record Prometheus metrics."""
        if scope["type"] != "http" or not settings.prometheus_enabled:
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("path", "")
        clean_path = raw_path.rstrip("/") or "/"
        if clean_path in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = max(time.perf_counter() - start_time, 0.0)
            endpoint = self._resolve_endpoint(raw_path, method)

            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()

            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

    def _resolve_endpoint(self, path: str, method: str) -> str:
        """
        Resolve normalized route template against precompiled patterns.

        Falls back to 'unmatched' for 404s or unrouted requests.
        """
        for regex, template, methods in self.route_map:
            if methods and method not in methods:
                continue
            if regex.match(path):
                return template
        return "unmatched"

    @classmethod
    def _build_route_map(cls, app: FastAPI) -> list[tuple[Any, str, set[str]]]:
        """
        Recursively extract and compile route templates from app and included routers.

        Handles FastAPI _IncludedRouter hierarchy without prefix duplication.
        """
        compiled_routes: list[tuple[Any, str, set[str]]] = []

        def collect(router: Any, current_prefix: str = "") -> None:
            for r in getattr(router, "routes", []):
                if hasattr(r, "original_router"):
                    parent_prefix = current_prefix + (
                        getattr(router, "prefix", "") if router != app.router else ""
                    )
                    collect(r.original_router, parent_prefix)
                elif hasattr(r, "path"):
                    raw_path = getattr(r, "path", None)
                    if not raw_path:
                        continue
                    prefix = current_prefix.rstrip("/")
                    path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
                    full_path = f"{prefix}{path}"
                    clean_template = re.sub(r"/+", "/", full_path)
                    regex, _, _ = compile_path(clean_template)
                    methods = getattr(r, "methods", None) or set()
                    compiled_routes.append((regex, clean_template, set(methods)))

        collect(app.router)
        return compiled_routes
