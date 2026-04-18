"""Tests for the wait_for_implementation lifecycle tool (FEAT-005 / T-096)."""

from __future__ import annotations

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.runtime_helpers import PauseForSignal
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask
from app.modules.ai.tools.lifecycle.wait_for_implementation import (
    handle,
    tool_definition,
)


def _memory() -> LifecycleMemory:
    return LifecycleMemory(tasks=[LifecycleTask(id="T-001", title="First")])


class TestToolDefinition:
    def test_task_id_parameter(self) -> None:
        td = tool_definition()
        assert td.name == "wait_for_implementation"
        assert td.parameters["required"] == ["task_id"]


@pytest.mark.asyncio
class TestHandle:
    async def test_returns_pause_sentinel(self) -> None:
        result = await handle({"task_id": "T-001"}, memory=_memory())
        assert isinstance(result, tuple)
        new_memory, pause = result
        assert isinstance(pause, PauseForSignal)
        assert pause.task_id == "T-001"
        assert pause.name == "implementation-complete"

    async def test_sets_current_task_id(self) -> None:
        new_memory, _ = await handle({"task_id": "T-001"}, memory=_memory())
        assert new_memory.current_task_id == "T-001"

    async def test_unknown_task(self) -> None:
        with pytest.raises(PolicyError, match="unknown task"):
            await handle({"task_id": "T-999"}, memory=_memory())
