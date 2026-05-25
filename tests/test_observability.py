from __future__ import annotations

import json
import logging

import pytest

from pyfhircheck.observability import bind_context, configure_logging, log_operation, set_correlation_id
from pyfhircheck.observability.logging import JsonLogFormatter


def test_json_formatter_includes_context_fields() -> None:
    configure_logging(level="INFO", log_format="json", force=True)
    set_correlation_id("run-test-123")
    bind_context(command="file", input_source="/tmp/patient.json")
    record = logging.LogRecord(
        name="pyfhircheck.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="validation finished",
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["level"] == "info"
    assert payload["event"] == "validation finished"
    assert payload["correlation_id"] == "run-test-123"
    assert payload["command"] == "file"
    assert payload["input_source"] == "/tmp/patient.json"


def test_log_operation_emits_start_and_complete() -> None:
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    configure_logging(level="INFO", log_format="json", force=True)
    root = logging.getLogger("pyfhircheck")
    root.handlers.clear()
    capture = _CaptureHandler()
    root.addHandler(capture)
    root.setLevel(logging.INFO)
    with log_operation("validate", command="file"):
        pass
    messages = [record.getMessage() for record in records if record.name == "pyfhircheck.operation"]
    assert "validate started" in messages
    assert "validate completed" in messages
