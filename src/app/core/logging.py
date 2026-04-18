"""Structured JSON logging with ``run_id`` / ``step_id`` contextvars."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Context variables — per-asyncio-task, safe for concurrent coroutines
# ---------------------------------------------------------------------------

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_step_id: ContextVar[str | None] = ContextVar("step_id", default=None)


def get_run_id() -> str | None:
    """Return the current ``run_id`` or ``None``."""
    return _run_id.get()


def get_step_id() -> str | None:
    """Return the current ``step_id`` or ``None``."""
    return _step_id.get()


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """Bind *run_id* to the current context; reset on exit."""
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


@contextmanager
def bind_step_id(step_id: str) -> Iterator[None]:
    """Bind *step_id* to the current context; reset on exit."""
    token = _step_id.set(step_id)
    try:
        yield
    finally:
        _step_id.reset(token)


# ---------------------------------------------------------------------------
# Standard LogRecord fields — used to filter extras
# ---------------------------------------------------------------------------

_LOGRECORD_STANDARD_FIELDS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}

# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object.

    ``run_id`` and ``step_id`` keys are **omitted** when the corresponding
    contextvar is ``None`` (never emitted as ``null``).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        rid = _run_id.get()
        sid = _step_id.get()
        if rid is not None:
            payload["run_id"] = rid
        if sid is not None:
            payload["step_id"] = sid

        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Carry extras passed via logger.info("msg", extra={...})
        for key, value in record.__dict__.items():
            if key not in _LOGRECORD_STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def configure_logging(level: str = "INFO") -> None:
    """Set up the root logger with :class:`JsonFormatter` on stdout."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
