from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from uuid import uuid4

_log_context: ContextVar[dict[str, Any]] = ContextVar("_log_context", default={})
_correlation_id: ContextVar[str] = ContextVar("_correlation_id", default="")


def correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str | None = None) -> str:
    run_id = value or str(uuid4())
    _correlation_id.set(run_id)
    bind_context(correlation_id=run_id)
    return run_id


def bind_context(**fields: Any) -> None:
    current = dict(_log_context.get())
    current.update({key: value for key, value in fields.items() if value is not None})
    _log_context.set(current)


def clear_context() -> None:
    _log_context.set({})
    _correlation_id.set("")


def current_context() -> dict[str, Any]:
    return dict(_log_context.get())
