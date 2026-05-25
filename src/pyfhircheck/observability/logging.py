from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from pyfhircheck.observability.context import current_context

_CONFIGURED = False
_STANDARD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "taskName",
}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(current_context())
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


class ContextLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_context().items():
            setattr(record, key, value)
        return True


def configure_logging(level: str | None = None, log_format: str | None = None, *, force: bool = False) -> None:
    global _CONFIGURED
    explicit = level is not None or log_format is not None
    if _CONFIGURED and not force and not explicit:
        return
    resolved_level = (level or os.environ.get("PYFHIRCHECK_LOG_LEVEL", "WARNING")).upper()
    resolved_format = (log_format or os.environ.get("PYFHIRCHECK_LOG_FORMAT", "json")).lower()
    root = logging.getLogger("pyfhircheck")
    root.handlers.clear()
    root.setLevel(getattr(logging, resolved_level, logging.WARNING))
    root.propagate = False
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(ContextLogFilter())
    if resolved_format == "console":
        handler.setFormatter(logging.Formatter("%(levelname)s pyfhircheck %(message)s"))
    else:
        handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"pyfhircheck.{name}")
