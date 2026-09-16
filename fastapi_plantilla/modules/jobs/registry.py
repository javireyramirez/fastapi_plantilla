from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from fastapi_plantilla.modules.jobs.exceptions import JobRegistrationError
from fastapi_plantilla.modules.jobs.schema import JobContext

__all__ = [
    "JobHandlerDefinition",
    "JobRegistry",
    "job_registry",
    "register_job",
]


@dataclass(frozen=True)
class JobHandlerDefinition:
    """Metadata and execution function for a registered background job."""

    name: str
    handler: Callable[[JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]]
    payload_model: type[BaseModel] | None = None


class JobRegistry:
    """Central registry of executable job handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandlerDefinition] = {}

    def register(
        self,
        name: str,
        handler: Callable[
            [JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]
        ],
        payload_model: type[BaseModel] | None = None,
        override: bool = False,
    ) -> None:
        """Register a handler for a job name.

        Raises:
            JobRegistrationError: If job name is already registered
                and override is False.
        """
        if name in self._handlers and not override:
            raise JobRegistrationError(f"Job '{name}' is already registered.")

        self._handlers[name] = JobHandlerDefinition(
            name=name,
            handler=handler,
            payload_model=payload_model,
        )

    def get(self, name: str) -> JobHandlerDefinition | None:
        """Retrieve handler definition by job name."""
        return self._handlers.get(name)

    def is_registered(self, name: str) -> bool:
        """Check if job name is registered."""
        return name in self._handlers

    def clear(self) -> None:
        """Clear all registered handlers (useful in tests)."""
        self._handlers.clear()


job_registry = JobRegistry()


def register_job(
    name: str,
    payload_model: type[BaseModel] | None = None,
    override: bool = False,
) -> Callable[
    [Callable[[JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]]],
    Callable[[JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]],
]:
    """Decorator to register a job handler with optional Pydantic validation."""

    def decorator(
        func: Callable[[JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]],
    ) -> Callable[[JobContext[Any]], Coroutine[Any, Any, dict[str, Any] | None]]:
        job_registry.register(
            name=name,
            handler=func,
            payload_model=payload_model,
            override=override,
        )
        return func

    return decorator
