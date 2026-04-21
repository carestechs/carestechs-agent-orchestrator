"""Self-tests for :mod:`tests.integration._reactor_helpers`.

Exercises the polling loop without a database — a stub ``AsyncSession``
stand-in is enough since the helper never touches the session itself,
only hands it to the predicate.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest

from tests.integration._reactor_helpers import (
    ReactorWaitTimeout,
    await_reactor,
)

pytestmark = pytest.mark.asyncio


class _FakeSession:
    """Minimal stand-in; the helper never calls anything on it."""


_SESSION = cast("Any", _FakeSession())


async def test_match_on_first_poll_returns_immediately() -> None:
    calls = 0

    async def predicate(_: Any) -> str:
        nonlocal calls
        calls += 1
        return "hit"

    start = time.monotonic()
    result = await await_reactor(_SESSION, predicate, interval=0.5)
    elapsed = time.monotonic() - start

    assert result == "hit"
    assert calls == 1
    assert elapsed < 0.1  # no sleep on first-poll match


async def test_match_after_a_few_polls() -> None:
    calls = 0

    async def predicate(_: Any) -> list[int]:
        nonlocal calls
        calls += 1
        return [1] if calls >= 3 else []

    start = time.monotonic()
    result = await await_reactor(_SESSION, predicate, interval=0.02)
    elapsed = time.monotonic() - start

    assert result == [1]
    assert calls == 3
    # 2 sleeps of 0.02s each — generous upper bound for CI jitter.
    assert elapsed < 0.5


async def test_timeout_raises_with_description_and_last_result() -> None:
    async def predicate(_: Any) -> None:
        return None

    with pytest.raises(ReactorWaitTimeout) as exc_info:
        await await_reactor(
            _SESSION,
            predicate,
            timeout=0.05,
            interval=0.01,
            description="nothing ever arrives",
        )

    msg = str(exc_info.value)
    assert "nothing ever arrives" in msg
    assert "last result: None" in msg


@pytest.mark.parametrize("falsy", [None, [], 0, ""])
async def test_falsy_types_trigger_retry(falsy: object) -> None:
    """Empty list / None / 0 / '' all count as 'not yet'."""
    calls = 0

    async def predicate(_: Any) -> object:
        nonlocal calls
        calls += 1
        if calls < 2:
            return falsy
        return "arrived"

    result = await await_reactor(_SESSION, predicate, interval=0.01)
    assert result == "arrived"
    assert calls == 2


async def test_predicate_is_awaited_and_can_use_asyncio() -> None:
    """Predicate is async — it can await things (real queries in practice)."""

    async def predicate(_: Any) -> str:
        await asyncio.sleep(0)
        return "ok"

    assert await await_reactor(_SESSION, predicate) == "ok"
