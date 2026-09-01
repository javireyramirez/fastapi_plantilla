from __future__ import annotations

import logging
import sys
from types import FrameType
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record


from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.middlewares import get_request_id

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<magenta>[{extra[request_id]}]</magenta> - "
    "<level>{message}</level>"
)


def log_patcher(record: Record) -> None:
    """Inject the current request ID into the log record extra context."""
    req_id = get_request_id() or "-"
    record["extra"]["request_id"] = req_id


class InterceptHandler(logging.Handler):
    """Intercept standard logging messages and redirect them to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        """Propagate log record to Loguru with correct depth and level."""
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def configure_logging() -> None:  # pragma: no cover
    """Configure application logging using Loguru."""
    intercept_handler = InterceptHandler()

    logging.basicConfig(handlers=[intercept_handler], level=logging.NOTSET)

    for logger_name in logging.root.manager.loggerDict:
        if logger_name.startswith("uvicorn."):
            logging.getLogger(logger_name).handlers = []

    logging.getLogger("uvicorn").handlers = [intercept_handler]
    logging.getLogger("uvicorn.access").handlers = [intercept_handler]

    logger.remove()
    logger.configure(patcher=log_patcher)
    logger.add(
        sys.stdout,
        format=LOG_FORMAT,
        level=settings.log_level.value,
    )
