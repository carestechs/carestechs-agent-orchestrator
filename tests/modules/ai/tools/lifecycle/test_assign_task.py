"""Tests for the assign_task lifecycle tool (FEAT-005 / T-092)."""

from __future__ import annotations

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.assign_task import (
    DEFAULT_EXECUTOR,
    handle,
    tool_definition,
)
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask


def _memory_with_tasks() -> LifecycleMemory:
    return LifecycleMemory(
        tasks=[
            LifecycleTask(id="T-001", title="First"),
            LifecycleTask(id="T-002", title="Second"),
        ],
    )


class TestToolDefinition:
    def test_task_id_parameter(self) -> None:
        td = tool_definition()
        assert td.name == "assign_task"
        assert td.parameters["required"] == ["task_id"]


@pytest.mark.asyncio
class TestHandle:
    async def test_assigns_executor(self) -> None:
        memory = _memory_with_tasks()
        result = await handle({"task_id": "T-001"}, memory=memory)
        assert result.tasks[0].executor == DEFAULT_EXECUTOR
        assert result.tasks[1].executor is None
        # Input not mutated.
        assert memory.tasks[0].executor is None

    async def test_unknown_task_raises(self) -> None:
        memory = _memory_with_tasks()
        with pytest.raises(PolicyError, match="unknown task"):
            await handle({"task_id": "T-999"}, memory=memory)

    async def test_reassignment_idempotent(self) -> None:
        """Loop-back from corrections must not corrupt state on re-assignment."""
        memory = _memory_with_tasks()
        once = await handle({"task_id": "T-001"}, memory=memory)
        twice = await handle({"task_id": "T-001"}, memory=once)
        assert twice.tasks[0].executor == DEFAULT_EXECUTOR
        assert [t.id for t in twice.tasks] == ["T-001", "T-002"]

    async def test_ordering_preserved(self) -> None:
        memory = LifecycleMemory(
            tasks=[LifecycleTask(id=f"T-{n:03d}", title=f"t{n}") for n in range(1, 6)],
        )
        result = await handle({"task_id": "T-003"}, memory=memory)
        assert [t.id for t in result.tasks] == ["T-001", "T-002", "T-003", "T-004", "T-005"]
