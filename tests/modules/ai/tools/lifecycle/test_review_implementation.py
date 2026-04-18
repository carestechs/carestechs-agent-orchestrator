"""Tests for the review_implementation lifecycle tool (FEAT-005 / T-094)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask
from app.modules.ai.tools.lifecycle.review_implementation import (
    handle,
    tool_definition,
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "plans").mkdir()

    def _fake_settings() -> Settings:
        return get_settings().model_copy(update={"repo_root": tmp_path})

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.review_implementation.get_settings",
        _fake_settings,
    )
    return tmp_path


def _memory_with_task() -> LifecycleMemory:
    return LifecycleMemory(tasks=[LifecycleTask(id="T-001", title="First task")])


class TestToolDefinition:
    def test_required_fields(self) -> None:
        td = tool_definition()
        assert td.name == "review_implementation"
        assert set(td.parameters["required"]) == {"task_id", "verdict", "feedback"}
        assert td.parameters["properties"]["verdict"]["enum"] == ["pass", "fail"]


@pytest.mark.asyncio
class TestHandle:
    async def test_happy_pass(self, repo: Path) -> None:
        result = await handle(
            {"task_id": "T-001", "verdict": "pass", "feedback": "looks good"},
            memory=_memory_with_task(),
        )
        review_file = repo / "plans" / "plan-T-001-first-task-review-1.md"
        assert review_file.is_file()
        assert "pass" in review_file.read_text()
        assert result.review_history[0].verdict == "pass"
        assert result.review_history[0].attempt == 1
        assert result.review_history[0].written_to == "plans/plan-T-001-first-task-review-1.md"

    async def test_happy_fail(self, repo: Path) -> None:
        result = await handle(
            {"task_id": "T-001", "verdict": "fail", "feedback": "diff doesn't match"},
            memory=_memory_with_task(),
        )
        assert result.review_history[0].verdict == "fail"

    async def test_invalid_verdict(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="invalid review verdict"):
            await handle(
                {"task_id": "T-001", "verdict": "maybe", "feedback": "x"},
                memory=_memory_with_task(),
            )

    async def test_unknown_task(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="unknown task"):
            await handle(
                {"task_id": "T-999", "verdict": "pass", "feedback": "x"},
                memory=_memory_with_task(),
            )

    async def test_attempt_increments(self, repo: Path) -> None:
        memory = _memory_with_task()
        once = await handle(
            {"task_id": "T-001", "verdict": "fail", "feedback": "first attempt failed"},
            memory=memory,
        )
        twice = await handle(
            {"task_id": "T-001", "verdict": "pass", "feedback": "retry succeeded"},
            memory=once,
        )
        assert len(twice.review_history) == 2
        assert twice.review_history[0].attempt == 1
        assert twice.review_history[1].attempt == 2
        assert (repo / "plans" / "plan-T-001-first-task-review-1.md").is_file()
        assert (repo / "plans" / "plan-T-001-first-task-review-2.md").is_file()
