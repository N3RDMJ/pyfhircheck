from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pyfhircheck.observability.logging import get_logger


@contextmanager
def log_operation(event: str, **fields: Any) -> Iterator[None]:
    logger = get_logger("operation")
    start = time.perf_counter()
    logger.info("%s started", event, extra={"operation": event, **fields, "phase": "start"})
    try:
        yield
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "%s failed",
            event,
            extra={
                "operation": event,
                **fields,
                "phase": "error",
                "duration_ms": duration_ms,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
        raise
    else:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "%s completed",
            event,
            extra={"operation": event, **fields, "phase": "complete", "duration_ms": duration_ms},
        )
