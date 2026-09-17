"""Canonical domain invariants and defaults for the background jobs module."""

from typing import Final

__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_BACKOFF_MAX_SECONDS",
    "DEFAULT_LEASE_DURATION_SECONDS",
    "DEFAULT_MAX_RETRIES",
]

DEFAULT_MAX_RETRIES: Final[int] = 3
DEFAULT_LEASE_DURATION_SECONDS: Final[int] = 300
DEFAULT_BACKOFF_BASE_SECONDS: Final[float] = 5.0
DEFAULT_BACKOFF_MAX_SECONDS: Final[float] = 300.0
