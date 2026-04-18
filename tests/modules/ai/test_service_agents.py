"""Service-layer tests for ``list_agents`` (T-044)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.modules.ai.service import list_agents

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_SAMPLE = _REPO_ROOT / "tests" / "fixtures" / "agents" / "sample-linear.yaml"


def _settings_with_dir(agents_dir: Path) -> Settings:
    return get_settings().model_copy(update={"agents_dir": agents_dir})


class TestListAgents:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        items = await list_agents(settings=_settings_with_dir(tmp_path))
        assert items == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_dir_returns_empty_list(self, tmp_path: Path) -> None:
        items = await list_agents(
            settings=_settings_with_dir(tmp_path / "nope")
        )
        assert items == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_two_yamls_return_two_sorted_dtos(self, tmp_path: Path) -> None:
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")
        src = _SAMPLE.read_text().replace("sample-linear", "zulu-agent")
        (tmp_path / "zulu-agent@1.0.yaml").write_text(src)

        items = await list_agents(settings=_settings_with_dir(tmp_path))

        refs = [i.ref for i in items]
        assert refs == ["sample-linear@1.0", "zulu-agent@1.0"]

        first = items[0]
        assert first.path.endswith("sample-linear@1.0.yaml")
        assert len(first.definition_hash) == 64
        assert "analyze_brief" in first.available_nodes
        assert first.intake_schema.get("type") == "object"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_malformed_yaml_is_skipped(self, tmp_path: Path) -> None:
        """A broken YAML is logged+skipped; the endpoint still returns the good ones."""
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")
        (tmp_path / "broken@1.0.yaml").write_text("not: {valid: yaml")

        items = await list_agents(settings=_settings_with_dir(tmp_path))

        assert len(items) == 1
        assert items[0].ref == "sample-linear@1.0"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dto_round_trips_camel_case(self, tmp_path: Path) -> None:
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")
        items = await list_agents(settings=_settings_with_dir(tmp_path))
        dumped = items[0].model_dump(by_alias=True)
        assert "definitionHash" in dumped
        assert "availableNodes" in dumped
        assert "intakeSchema" in dumped
