"""Shared fixtures for integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio


@pytest.fixture
def fast_tail_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the trace-store + service poll cadences to ~0.01 s.

    Follow-mode stream tests otherwise take multiples of 200 ms per test
    just waiting for the terminal-state close detection to fire.
    """
    monkeypatch.setattr("app.modules.ai.trace_jsonl._TAIL_POLL_SECONDS", 0.01)
    monkeypatch.setattr("app.modules.ai.service._TAIL_POLL_SECONDS", 0.01)


@pytest_asyncio.fixture(loop_scope="function")
async def fresh_pool() -> AsyncIterator[None]:
    """Dispose the module-level engine pool between tests.

    Asyncpg connections are bound to the event loop they were created on;
    reusing them across function-scoped loops can surface as "Future
    attached to a different loop" errors or hung request tasks.  Tests
    that spin up their own app (via :func:`integration_env` or
    :func:`create_app`) should depend on this fixture to guarantee a
    fresh connection pool.
    """
    from app.core.database import get_engine

    await get_engine().dispose()
    yield
    await get_engine().dispose()
