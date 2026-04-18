"""Tests for the generate_tasks lifecycle tool (FEAT-005 / T-091)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.generate_tasks import handle, tool_definition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

_VALID_TASKS_DOC = """# Task Breakdown: IMP-fixture

### T-001: First task
Body.

### T-002: Second task
Body.
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings at an empty tmp repo with a writable tasks/ dir."""
    (tmp_path / "tasks").mkdir()

    def _fake_settings() -> Settings:
        base = get_settings().model_copy(update={"repo_root": tmp_path})
        return base

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.generate_tasks.get_settings",
        _fake_settings,
    )
    return tmp_path


class TestToolDefinition:
    def test_advertises_required_fields(self) -> None:
        td = tool_definition()
        assert td.name == "generate_tasks"
        assert set(td.parameters["required"]) == {"work_item_id", "tasks_markdown"}


@pytest.mark.asyncio
class TestHandle:
    async def test_happy(self, repo: Path) -> None:
        memory = LifecycleMemory.empty()
        result = await handle(
            {"work_item_id": "IMP-fixture", "tasks_markdown": _VALID_TASKS_DOC},
            memory=memory,
        )
        assert [t.id for t in result.tasks] == ["T-001", "T-002"]
        assert result.tasks[0].title == "First task"
        written = (repo / "tasks" / "IMP-fixture-tasks.md").read_text()
        assert written == _VALID_TASKS_DOC

    async def test_refuse_to_overwrite(self, repo: Path) -> None:
        target = repo / "tasks" / "IMP-fixture-tasks.md"
        target.write_text("pre-existing\n")

        with pytest.raises(PolicyError, match="file already exists"):
            await handle(
                {"work_item_id": "IMP-fixture", "tasks_markdown": _VALID_TASKS_DOC},
                memory=LifecycleMemory.empty(),
            )
        assert target.read_text() == "pre-existing\n"

    async def test_empty_body_rejected(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="empty"):
            await handle(
                {"work_item_id": "IMP-fixture", "tasks_markdown": "   \n\n"},
                memory=LifecycleMemory.empty(),
            )

    async def test_no_parseable_rows_rejected(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="no parseable"):
            await handle(
                {
                    "work_item_id": "IMP-fixture",
                    "tasks_markdown": "# Tasks\n\nno T-rows here\n",
                },
                memory=LifecycleMemory.empty(),
            )
        # And nothing was written.
        assert not (repo / "tasks" / "IMP-fixture-tasks.md").exists()
