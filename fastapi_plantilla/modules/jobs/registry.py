from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from fastapi_plantilla.modules.jobs.exceptions import JobRegistrationError
from fastapi_plantilla.modules.jobs.schema import JobContext, JobDefinitionResponse

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
    title: str = ""
    description: str = ""
    category: str = "system"
    icon: str = "cpu"
    is_dispatchable: bool = False


def _load_builtin_jobs(force_reload: bool = False) -> None:
    """Ensure all core registered jobs are imported and available in job_registry."""
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415

    modules = [
        "fastapi_plantilla.core.crud.export_job",
        "fastapi_plantilla.core.crud.importer",
        "fastapi_plantilla.modules.audit.jobs",
        "fastapi_plantilla.modules.email.jobs",
        "fastapi_plantilla.modules.notifications.jobs",
        "fastapi_plantilla.modules.storage.jobs",
        "fastapi_plantilla.modules.trash.jobs",
    ]
    for mod_name in modules:
        try:
            if mod_name in sys.modules and force_reload:
                importlib.reload(sys.modules[mod_name])
            elif mod_name not in sys.modules:
                importlib.import_module(mod_name)
        except Exception:  # noqa: S110
            pass


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
        title: str | None = None,
        description: str | None = None,
        category: str = "system",
        icon: str = "cpu",
        is_dispatchable: bool = False,
    ) -> None:
        """Register a handler for a job name.

        Raises:
            JobRegistrationError: If job name is already registered
                and override is False.
        """
        if name in self._handlers and not override:
            raise JobRegistrationError(f"Job '{name}' is already registered.")

        resolved_title = title or name.replace(".", " ").replace("_", " ").title()
        resolved_desc = description or f"Background job handler for {name}"

        self._handlers[name] = JobHandlerDefinition(
            name=name,
            handler=handler,
            payload_model=payload_model,
            title=resolved_title,
            description=resolved_desc,
            category=category,
            icon=icon,
            is_dispatchable=is_dispatchable,
        )

    def get(self, name: str) -> JobHandlerDefinition | None:
        """Retrieve handler definition by job name."""
        if name not in self._handlers:
            _load_builtin_jobs(force_reload=True)
        return self._handlers.get(name)

    def is_registered(self, name: str) -> bool:
        """Check if job name is registered."""
        if name not in self._handlers:
            _load_builtin_jobs(force_reload=True)
        return name in self._handlers

    def get_definitions(self) -> list[JobDefinitionResponse]:
        """Return catalog of all registered jobs for backend-driven UI."""
        if len(self._handlers) < 7:
            _load_builtin_jobs(force_reload=True)
        else:
            _load_builtin_jobs(force_reload=False)
        definitions: list[JobDefinitionResponse] = []
        for defn in self._handlers.values():
            schema = None
            if defn.payload_model is not None:
                schema = defn.payload_model.model_json_schema()
            definitions.append(
                JobDefinitionResponse(
                    name=defn.name,
                    title=defn.title,
                    description=defn.description,
                    category=defn.category,
                    icon=defn.icon,
                    is_dispatchable=defn.is_dispatchable,
                    payload_schema=schema,
                )
            )
        return sorted(definitions, key=lambda d: (d.category, d.name))

    def clear(self) -> None:
        """Clear all registered handlers (useful in tests)."""
        self._handlers.clear()


job_registry = JobRegistry()


def register_job(
    name: str,
    payload_model: type[BaseModel] | None = None,
    override: bool = False,
    title: str | None = None,
    description: str | None = None,
    category: str = "system",
    icon: str = "cpu",
    is_dispatchable: bool = False,
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
            title=title,
            description=description,
            category=category,
            icon=icon,
            is_dispatchable=is_dispatchable,
        )
        return func

    return decorator
