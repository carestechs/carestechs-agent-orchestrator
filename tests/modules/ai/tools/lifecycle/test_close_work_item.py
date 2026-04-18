"""Tests for the close_work_item lifecycle tool (FEAT-005 / T-095)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.close_work_item import handle, tool_definition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, WorkItemRef
from app.modules.ai.tools.lifecycle.work_items import parse_work_item

_IN_PROGRESS_BRIEF = """# Improvement Proposal: IMP-fixture — Fixture

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-fixture |
| **Name** | Fixture |
| **Status** | In Progress |
| **Priority** | Low |
| **Date Created** | 2026-04-18 |

## 2. Body

Placeholder.
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def _fake_settings() -> Settings:
        return get_settings().model_copy(update={"repo_root": tmp_path})

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.close_work_item.get_settings", _fake_settings
    )
    return tmp_path


def _memory_for(repo_root: Path, relative: str) -> LifecycleMemory:
    ref = WorkItemRef(id="IMP-fixture", type="IMP", title="Fixture", path=relative)
    return LifecycleMemory(work_item=ref)


class TestToolDefinition:
    def test_work_item_id_parameter(self) -> None:
        td = tool_definition()
        assert td.name == "close_work_item"
        assert td.parameters["required"] == ["work_item_id"]


@pytest.mark.asyncio
class TestHandle:
    async def test_happy_flips_status(self, repo: Path) -> None:
        brief = repo / "docs" / "work-items" / "IMP-fixture.md"
        brief.parent.mkdir(parents=True)
        brief.write_text(_IN_PROGRESS_BRIEF)

        await handle(
            {"work_item_id": "IMP-fixture"},
            memory=_memory_for(repo, "docs/work-items/IMP-fixture.md"),
        )
        body = brief.read_text()
        assert "| **Status** | Completed |" in body
        assert re.search(r"\| \*\*Completed\*\* \| 20\d\d-\d\d-\d\dT", body)

    async def test_refuses_if_not_in_progress(self, repo: Path) -> None:
        brief = repo / "docs" / "work-items" / "IMP-fixture.md"
        brief.parent.mkdir(parents=True)
        brief.write_text(_IN_PROGRESS_BRIEF.replace("In Progress", "Not Started"))

        with pytest.raises(PolicyError, match="not in progress"):
            await handle(
                {"work_item_id": "IMP-fixture"},
                memory=_memory_for(repo, "docs/work-items/IMP-fixture.md"),
            )
        assert "Not Started" in brief.read_text()

    async def test_missing_file(self, repo: Path) -> None:
        with pytest.raises(PolicyError, match="work item file not found"):
            await handle(
                {"work_item_id": "IMP-fixture"},
                memory=_memory_for(repo, "docs/work-items/IMP-fixture.md"),
            )

    async def test_malformed_table(self, repo: Path) -> None:
        brief = repo / "docs" / "work-items" / "IMP-fixture.md"
        brief.parent.mkdir(parents=True)
        brief.write_text("no identity table here\njust text\n")

        with pytest.raises(PolicyError, match="identity table not parseable"):
            await handle(
                {"work_item_id": "IMP-fixture"},
                memory=_memory_for(repo, "docs/work-items/IMP-fixture.md"),
            )

    async def test_work_item_id_mismatch(self, repo: Path) -> None:
        memory = _memory_for(repo, "docs/work-items/IMP-fixture.md")
        with pytest.raises(PolicyError, match="work_item_id mismatch"):
            await handle({"work_item_id": "IMP-other"}, memory=memory)

    async def test_parses_cleanly_after_close(self, repo: Path) -> None:
        """After closing, parse_work_item sees the now-terminal status and refuses."""
        brief = repo / "docs" / "work-items" / "IMP-fixture.md"
        brief.parent.mkdir(parents=True)
        brief.write_text(_IN_PROGRESS_BRIEF)

        await handle(
            {"work_item_id": "IMP-fixture"},
            memory=_memory_for(repo, "docs/work-items/IMP-fixture.md"),
        )
        with pytest.raises(PolicyError, match="already terminal"):
            parse_work_item(brief)
