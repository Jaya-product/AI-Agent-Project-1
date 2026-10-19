"""
src/utils/logger.py
───────────────────
Structured JSON logger for the Groww Review Intelligence Agent.

All application code should obtain a logger via `get_logger(__name__)`.
Log records are emitted as newline-delimited JSON to stdout, making them
easy to parse in CI pipelines, log aggregators, and local development.

Usage:
    from src.utils.logger import get_logger
    logger = get_logger(__name__)

    logger.info("Pipeline started", extra={"weeks": 10})
    logger.warning("Truncation applied", extra={"original_words": 270, "trimmed_to": 250})
    logger.error("MCP call failed", extra={"tool": "create_document", "error": str(e)})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.

    Each record includes:
        - timestamp  (ISO-8601 UTC)
        - level      (DEBUG / INFO / WARNING / ERROR / CRITICAL)
        - logger     (dotted module name, e.g. src.ingestion.fetcher)
        - message    (the formatted log message)
        - *extra     (any keyword fields passed via extra={...})
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        # Build the base log object
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields the caller passed in
        reserved = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in reserved:
                log_obj[key] = value

        # Attach exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str, ensure_ascii=True)


def _build_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    return handler


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """
    Return a named logger configured with JSON output.

    Args:
        name:  Typically ``__name__`` of the calling module.
        level: Optional override for this logger's level.
               Falls back to the value in ``settings.log_level``.

    Returns:
        A ``logging.Logger`` instance. Multiple calls with the same ``name``
        return the same cached instance (Python's logging module behaviour).
    """
    # Lazy import to avoid a circular dependency at module load time
    try:
        from src.utils.config import settings  # noqa: PLC0415
        default_level = settings.log_level
    except Exception:
        default_level = "INFO"

    logger = logging.getLogger(name)

    if not logger.handlers:
        # Only configure once per name
        logger.addHandler(_build_handler())
        logger.propagate = False

    effective_level = (level or default_level).upper()
    logger.setLevel(getattr(logging, effective_level, logging.INFO))

    return logger
