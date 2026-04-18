"""CLI tests for the ``agents`` sub-commands (T-046)."""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_BASE = "http://cli-test.local"
_AUTH = {"ORCHESTRATOR_API_BASE": _BASE, "ORCHESTRATOR_API_KEY": "k"}


def _agents_envelope() -> dict[str, object]:
    return {
        "data": [
            {
                "ref": "sample-linear@1.0",
                "definitionHash": "a" * 64,
                "path": "/tmp/sample-linear@1.0.yaml",
                "intakeSchema": {"type": "object"},
                "availableNodes": ["analyze_brief", "draft_plan", "review_plan"],
            },
            {
                "ref": "other@0.2",
                "definitionHash": "b" * 64,
                "path": "/tmp/other@0.2.yaml",
                "intakeSchema": {},
                "availableNodes": ["run"],
            },
        ]
    }


class TestAgentsLs:
    @respx.mock
    def test_lists_all_agents(self) -> None:
        respx.get(f"{_BASE}/api/v1/agents").mock(
            return_value=httpx.Response(200, json=_agents_envelope())
        )
        result = runner.invoke(main, ["agents", "ls"], env=_AUTH)
        assert result.exit_code == 0, result.output
        assert "sample-linear@1.0" in result.output
        assert "other@0.2" in result.output

    @respx.mock
    def test_empty_list_prints_no_rows(self) -> None:
        respx.get(f"{_BASE}/api/v1/agents").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        result = runner.invoke(main, ["agents", "ls"], env=_AUTH)
        assert result.exit_code == 0
        assert "(no rows)" in result.output


class TestAgentsShow:
    @respx.mock
    def test_filters_by_ref(self) -> None:
        respx.get(f"{_BASE}/api/v1/agents").mock(
            return_value=httpx.Response(200, json=_agents_envelope())
        )
        result = runner.invoke(main, ["agents", "show", "other@0.2"], env=_AUTH)
        assert result.exit_code == 0
        assert "other@0.2" in result.output
        assert "sample-linear@1.0" not in result.output

    @respx.mock
    def test_unknown_ref_exits_1(self) -> None:
        respx.get(f"{_BASE}/api/v1/agents").mock(
            return_value=httpx.Response(200, json=_agents_envelope())
        )
        result = runner.invoke(
            main, ["agents", "show", "does-not-exist@9.9"], env=_AUTH
        )
        assert result.exit_code == 1
        assert "not found" in result.output
