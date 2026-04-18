"""Tests for the corrections lifecycle tool (FEAT-005 / T-097)."""

from __future__ import annotations

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.corrections import handle, tool_definition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask


def _memory(**corrections: int) -> LifecycleMemory:
    return LifecycleMemory(
        tasks=[LifecycleTask(id="T-001", title="First")],
        correction_attempts=dict(corrections),
    )


class TestToolDefinition:
    def test_task_id_parameter(self) -> None:
        td = tool_definition()
        assert td.name == "corrections"
        assert td.parameters["required"] == ["task_id"]


@pytest.mark.asyncio
class TestHandle:
    async def test_first_increment_from_zero(self) -> None:
        result = await handle({"task_id": "T-001"}, memory=_memory())
        assert result.correction_attempts == {"T-001": 1}

    async def test_subsequent_increment(self) -> None:
        memory = _memory(**{"T-001": 2})
        result = await handle({"task_id": "T-001"}, memory=memory)
        assert result.correction_attempts == {"T-001": 3}

    async def test_unknown_task_raises(self) -> None:
        with pytest.raises(PolicyError, match="unknown task"):
            await handle({"task_id": "T-999"}, memory=_memory())

    async def test_does_not_mutate_input(self) -> None:
        memory = _memory(**{"T-001": 1})
        await handle({"task_id": "T-001"}, memory=memory)
        assert memory.correction_attempts == {"T-001": 1}
