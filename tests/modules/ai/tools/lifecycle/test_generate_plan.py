"""Tests for the generate_plan lifecycle tool (FEAT-005 / T-093)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.generate_plan import handle, tool_definition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask

_PLAN_DOC = "# Plan\n\n## Overview\n\nBody.\n"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "plans").mkdir()

    def _fake_settings() -> Settings:
        return get_settings().model_copy(update={"repo_root": tmp_path})

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.generate_plan.get_settings", _fake_settings
    )
    return tmp_path


def _memory(*tasks: tuple[str, str]) -> LifecycleMemory:
    return LifecycleMemory(tasks=[LifecycleTask(id=tid, title=title) for tid, title in tasks])


class TestToolDefinition:
    def test_required_fields(self) -> None:
        td = tool_definition()
        assert td.name == "generate_plan"
        assert set(td.parameters["required"]) == {"task_id", "plan_markdown"}
        assert "slug" in td.parameters["properties"]


@pytest.mark.asyncio
class TestHandle:
    async def test_happy_with_explicit_slug(self, repo: Path) -> None:
        memory = _memory(("T-001", "Refactor auth"))
        result = await handle(
            {"task_id": "T-001", "plan_markdown": _PLAN_DOC, "slug": "my-slug"},
            memory=memory,
        )
        assert (repo / "plans" / "plan-T-001-my-slug.md").read_text() == _PLAN_DOC
        assert result.tasks[0].plan_path == "plans/plan-T-001-my-slug.md"

    async def test_happy_with_derived_slug(self, repo: Path) -> None:
        memory = _memory(("T-001", "Refactor Auth Module"))
        result = await handle(
            {"task_id": "T-001", "plan_markdown": _PLAN_DOC},
            memory=memory,
        )
        target = repo / "plans" / "plan-T-001-refactor-auth-module.md"
        assert target.read_text() == _PLAN_DOC
        assert result.tasks[0].plan_path == "plans/plan-T-001-refactor-auth-module.md"

    async def test_unknown_task(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="unknown task"):
            await handle(
                {"task_id": "T-999", "plan_markdown": _PLAN_DOC},
                memory=_memory(("T-001", "First")),
            )

    async def test_empty_plan(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="empty"):
            await handle(
                {"task_id": "T-001", "plan_markdown": "   \n\n"},
                memory=_memory(("T-001", "First")),
            )

    async def test_refuse_to_overwrite(self, repo: Path) -> None:
        existing = repo / "plans" / "plan-T-001-first.md"
        existing.write_text("pre\n")
        with pytest.raises(PolicyError, match="file already exists"):
            await handle(
                {"task_id": "T-001", "plan_markdown": _PLAN_DOC},
                memory=_memory(("T-001", "First")),
            )

    async def test_all_punctuation_title_raises(self, repo: Path) -> None:
        memory = _memory(("T-001", "!!!"))
        with pytest.raises(PolicyError, match="cannot derive slug"):
            await handle(
                {"task_id": "T-001", "plan_markdown": _PLAN_DOC},
                memory=memory,
            )
