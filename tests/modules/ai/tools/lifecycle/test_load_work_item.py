"""Tests for the load_work_item lifecycle tool (FEAT-005 / T-090)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.load_work_item import handle, tool_definition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory
from app.modules.ai.tools.lifecycle.work_items import parse_work_item

_FIXTURES_DIR = (
    Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "work-items"
)


class TestToolDefinition:
    def test_tool_advertises_path_parameter(self) -> None:
        td = tool_definition()
        assert td.name == "load_work_item"
        assert td.parameters["required"] == ["path"]
        assert "path" in td.parameters["properties"]


class TestParseWorkItemHappyPaths:
    @pytest.mark.parametrize(
        ("fixture", "expected_type"),
        [("FEAT-fixture.md", "FEAT"), ("BUG-fixture.md", "BUG"), ("IMP-fixture.md", "IMP")],
    )
    def test_parses_each_type(self, fixture: str, expected_type: str) -> None:
        ref = parse_work_item(_FIXTURES_DIR / fixture)
        assert ref.type == expected_type
        assert ref.id == fixture.removesuffix(".md")
        assert ref.title


class TestParseWorkItemFailures:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyError, match="work item file not found"):
            parse_work_item(tmp_path / "nope.md")

    def test_terminal_status_completed(self, tmp_path: Path) -> None:
        path = tmp_path / "IMP-done.md"
        path.write_text(
            "| **ID** | IMP-done |\n"
            "| **Name** | Done |\n"
            "| **Status** | Completed |\n"
        )
        with pytest.raises(PolicyError, match="already terminal"):
            parse_work_item(path)

    def test_terminal_status_cancelled(self, tmp_path: Path) -> None:
        path = tmp_path / "IMP-dead.md"
        path.write_text(
            "| **ID** | IMP-dead |\n"
            "| **Name** | Dead |\n"
            "| **Status** | Cancelled |\n"
        )
        with pytest.raises(PolicyError, match="already terminal"):
            parse_work_item(path)

    def test_unsupported_type(self, tmp_path: Path) -> None:
        path = tmp_path / "DOC-001.md"
        path.write_text(
            "| **ID** | DOC-001 |\n"
            "| **Name** | Doc |\n"
            "| **Status** | In Progress |\n"
        )
        with pytest.raises(PolicyError, match="unsupported work item type"):
            parse_work_item(path)

    def test_missing_identity_table(self, tmp_path: Path) -> None:
        path = tmp_path / "IMP-bad.md"
        path.write_text("no identity here\njust words\n")
        with pytest.raises(PolicyError, match="missing identity table"):
            parse_work_item(path)

    def test_filename_id_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "IMP-renamed.md"
        path.write_text(
            "| **ID** | IMP-original |\n"
            "| **Name** | X |\n"
            "| **Status** | In Progress |\n"
        )
        with pytest.raises(PolicyError, match="filename/id mismatch"):
            parse_work_item(path)


class TestHandle:
    @pytest.mark.asyncio
    async def test_populates_memory_work_item(self) -> None:
        memory = LifecycleMemory.empty()
        result = await handle(
            {"path": str(_FIXTURES_DIR / "IMP-fixture.md")},
            memory=memory,
        )
        assert result.work_item is not None
        assert result.work_item.id == "IMP-fixture"
        assert result.work_item.type == "IMP"
