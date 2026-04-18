"""Tests for app.core.logging: JSON formatter, contextvars, configure_logging."""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest

from app.core.logging import (
    JsonFormatter,
    bind_run_id,
    bind_step_id,
    configure_logging,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger(name: str = "test") -> tuple[logging.Logger, io.StringIO]:
    """Return a logger wired to a StringIO via JsonFormatter."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger(name)
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return log, buf


def _parse_line(buf: io.StringIO) -> dict[str, object]:
    """Parse the last JSON line from *buf*."""
    buf.seek(0)
    lines = [line for line in buf.read().strip().splitlines() if line]
    return json.loads(lines[-1])


def _parse_all_lines(buf: io.StringIO) -> list[dict[str, object]]:
    buf.seek(0)
    return [json.loads(line) for line in buf.read().strip().splitlines() if line]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUnboundOmission:
    def test_no_run_id_or_step_id(self) -> None:
        log, buf = _make_logger("test_unbound")
        log.info("hello")
        payload = _parse_line(buf)
        assert "run_id" not in payload
        assert "step_id" not in payload
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert "timestamp" in payload
        assert payload["logger"] == "test_unbound"

    def test_json_is_valid(self) -> None:
        log, buf = _make_logger("test_json")
        log.info("check")
        json.loads(buf.getvalue())  # should not raise


class TestBindRoundtrip:
    def test_bind_run_id(self) -> None:
        log, buf = _make_logger("test_bind_run")
        with bind_run_id("r-1"):
            log.info("inside")
        payload = _parse_line(buf)
        assert payload["run_id"] == "r-1"

    def test_bind_step_id(self) -> None:
        log, buf = _make_logger("test_bind_step")
        with bind_step_id("s-1"):
            log.info("inside")
        payload = _parse_line(buf)
        assert payload["step_id"] == "s-1"

    def test_both_bound(self) -> None:
        log, buf = _make_logger("test_bind_both")
        with bind_run_id("r-1"), bind_step_id("s-1"):
            log.info("inside")
        payload = _parse_line(buf)
        assert payload["run_id"] == "r-1"
        assert payload["step_id"] == "s-1"


class TestNesting:
    def test_nesting_resets(self) -> None:
        log, buf = _make_logger("test_nesting")
        with bind_run_id("r-1"):
            log.info("outer")
            with bind_run_id("r-2"):
                log.info("inner")
            log.info("outer-again")
        lines = _parse_all_lines(buf)
        assert lines[0]["run_id"] == "r-1"
        assert lines[1]["run_id"] == "r-2"
        assert lines[2]["run_id"] == "r-1"


class TestAsyncIsolation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_tasks(self) -> None:
        log, buf = _make_logger("test_async")
        results: dict[str, str] = {}

        async def task(name: str, rid: str) -> None:
            with bind_run_id(rid):
                await asyncio.sleep(0)  # yield to force interleaving
                log.info(f"from {name}")
                results[name] = rid

        await asyncio.gather(task("a", "r-a"), task("b", "r-b"))

        lines = _parse_all_lines(buf)
        by_message = {str(line["message"]): line for line in lines}
        assert by_message["from a"]["run_id"] == "r-a"
        assert by_message["from b"]["run_id"] == "r-b"


class TestExtras:
    def test_extra_fields_carried(self) -> None:
        log, buf = _make_logger("test_extra")
        log.info("event", extra={"foo": 1, "bar": "baz"})
        payload = _parse_line(buf)
        assert payload["foo"] == 1
        assert payload["bar"] == "baz"


class TestConfigureLogging:
    def test_sets_root_level(self) -> None:
        configure_logging("DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        # Restore default
        configure_logging("INFO")

    def test_uvicorn_access_quieted(self) -> None:
        configure_logging("DEBUG")
        uv_access = logging.getLogger("uvicorn.access")
        assert uv_access.level >= logging.WARNING
        configure_logging("INFO")
