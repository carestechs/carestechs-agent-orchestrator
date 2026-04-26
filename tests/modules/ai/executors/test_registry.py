"""ExecutorRegistry unit tests (FEAT-009 / T-213)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from app.modules.ai.executors import (
    DispatchContext,
    Executor,
    ExecutorRegistry,
)
from app.modules.ai.executors.base import ExecutorMode
from app.modules.ai.executors.registry import ExecutorRegistryError
from app.modules.ai.schemas import DispatchEnvelope


class _StubExecutor:
    name: ClassVar[str] = "stub"
    mode: ClassVar[ExecutorMode] = "local"

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self.name,
            mode=self.mode,
            state="completed",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            outcome="ok",  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def test_executor_protocol_runtime_check() -> None:
    """``Executor`` is ``@runtime_checkable`` — duck typing must work."""
    assert isinstance(_StubExecutor(), Executor)


class TestRegister:
    def test_register_then_resolve(self) -> None:
        reg = ExecutorRegistry()
        executor = _StubExecutor()
        binding = reg.register("agent@1", "node_a", executor)
        assert binding.executor is executor
        assert binding.agent_ref == "agent@1"
        assert binding.node_name == "node_a"
        assert reg.has("agent@1", "node_a")
        assert reg.resolve("agent@1", "node_a") is binding

    def test_extras_and_timeout_threaded(self) -> None:
        reg = ExecutorRegistry()
        binding = reg.register(
            "agent@1",
            "node_a",
            _StubExecutor(),
            timeout_seconds=42.0,
            extras={"system_prompt": "be concise"},
        )
        assert binding.timeout_seconds == 42.0
        assert binding.extras["system_prompt"] == "be concise"

    def test_duplicate_registration_raises(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "node_a", _StubExecutor())
        with pytest.raises(ExecutorRegistryError, match="already registered"):
            reg.register("agent@1", "node_a", _StubExecutor())


class TestResolveMissing:
    def test_unknown_key_raises(self) -> None:
        reg = ExecutorRegistry()
        with pytest.raises(ExecutorRegistryError, match="no executor registered"):
            reg.resolve("agent@1", "ghost")

    def test_has_returns_false_for_missing(self) -> None:
        reg = ExecutorRegistry()
        assert reg.has("agent@1", "ghost") is False


class TestInspection:
    def test_registered_keys_returns_frozenset(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "node_a", _StubExecutor())
        reg.register("agent@1", "node_b", _StubExecutor())
        keys = reg.registered_keys()
        assert keys == frozenset({("agent@1", "node_a"), ("agent@1", "node_b")})

    def test_bindings_yields_all(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "node_a", _StubExecutor())
        reg.register("agent@2", "node_x", _StubExecutor())
        bindings = list(reg.bindings())
        assert len(bindings) == 2


def test_dispatch_context_immutable() -> None:
    ctx = DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="agent@1",
        node_name="node_a",
        intake={"x": 1},
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.node_name = "other"  # type: ignore[misc]
    # extras default is an empty mapping
    assert dict(ctx.extras) == {}
