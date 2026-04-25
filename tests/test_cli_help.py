"""Tests that ``orchestrator --help`` advertises the full Command Inventory."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

# Mirrors docs/ui-specification.md → Command Inventory (Command | Subcommand).
_COMMAND_INVENTORY: list[tuple[str, list[str]]] = [
    ("orchestrator", ["run", "serve", "doctor", "reconcile-aux", "runs", "agents"]),
    ("runs", ["ls", "show", "cancel", "trace", "steps", "policy"]),
    ("agents", ["ls", "show"]),
]


class TestCommandInventory:
    @pytest.mark.parametrize(
        ("group", "expected"),
        [
            (group, expected)
            for group, expected in _COMMAND_INVENTORY
        ],
        ids=[group for group, _ in _COMMAND_INVENTORY],
    )
    def test_help_lists_all_commands(
        self, group: str, expected: list[str]
    ) -> None:
        args = ["--help"] if group == "orchestrator" else [group, "--help"]
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output
        for cmd in expected:
            assert cmd in result.output, (
                f"Expected `{cmd}` in `orchestrator {group} --help` output"
            )


class TestGlobalOptionsInHelp:
    def test_root_help_mentions_global_options(self) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        for option in ("--api-base", "--api-key", "--json", "--quiet", "--verbose"):
            assert option in result.output, f"Missing global option: {option}"
