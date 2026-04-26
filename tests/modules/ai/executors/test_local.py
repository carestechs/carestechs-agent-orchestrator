"""LocalExecutor unit tests (FEAT-009 / T-214)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.local import LocalExecutor

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _ctx(intake: dict[str, Any] | None = None) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="agent@1",
        node_name="node_a",
        intake=intake or {},
    )


class TestSuccessPath:
    async def test_handler_result_becomes_completed_envelope(self) -> None:
        async def handler(ctx: DispatchContext) -> Mapping[str, Any]:
            return {"echoed": dict(ctx.intake)}

        executor = LocalExecutor(ref="local:test", handler=handler)
        env = await executor.dispatch(_ctx({"x": 1}))

        assert env.state.value == "completed"
        assert env.outcome is not None
        assert env.outcome.value == "ok"
        assert env.result == {"echoed": {"x": 1}}
        assert env.executor_ref == "local:test"
        assert env.mode.value == "local"
        assert env.finished_at is not None


class TestFailurePaths:
    async def test_handler_exception_becomes_failed_envelope(self) -> None:
        async def handler(_ctx: DispatchContext) -> Mapping[str, Any]:
            raise ValueError("boom")

        executor = LocalExecutor(ref="local:bad", handler=handler)
        env = await executor.dispatch(_ctx())

        assert env.state.value == "failed"
        assert env.outcome is not None
        assert env.outcome.value == "error"
        assert env.detail is not None
        assert "ValueError" in env.detail
        assert "boom" in env.detail

    async def test_non_mapping_return_becomes_failed_envelope(self) -> None:
        async def handler(_ctx: DispatchContext) -> Any:
            return [1, 2, 3]  # not a Mapping

        executor = LocalExecutor(ref="local:wrong-shape", handler=handler)
        env = await executor.dispatch(_ctx())

        assert env.state.value == "failed"
        assert env.detail is not None
        assert "expected Mapping" in env.detail


async def test_executor_name_returns_ref() -> None:
    executor = LocalExecutor(ref="local:x", handler=lambda _ctx: ...)  # type: ignore[arg-type]
    assert executor.name == "local:x"
