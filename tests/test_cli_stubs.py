"""Tests for app.cli: help output, stub exit codes."""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------

_EXPECTED_COMMANDS = [
    "run",
    "serve",
    "doctor",
    "runs",
    "agents",
]


class TestHelp:
    def test_root_help_lists_all_commands(self) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        for cmd in _EXPECTED_COMMANDS:
            assert cmd in result.output, f"Missing command: {cmd}"

    def test_runs_help(self) -> None:
        result = runner.invoke(main, ["runs", "--help"])
        assert result.exit_code == 0
        for sub in ["ls", "show", "cancel", "trace", "steps", "policy"]:
            assert sub in result.output, f"Missing runs subcommand: {sub}"

    def test_agents_help(self) -> None:
        result = runner.invoke(main, ["agents", "--help"])
        assert result.exit_code == 0
        for sub in ["ls", "show"]:
            assert sub in result.output, f"Missing agents subcommand: {sub}"


# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------


class TestGlobalOptions:
    def test_json_flag_accepted(self) -> None:
        result = runner.invoke(main, ["--json", "--help"])
        assert result.exit_code == 0

    def test_quiet_flag_accepted(self) -> None:
        result = runner.invoke(main, ["-q", "--help"])
        assert result.exit_code == 0

    def test_verbose_flag_accepted(self) -> None:
        result = runner.invoke(main, ["-v", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stub commands exit 2
# ---------------------------------------------------------------------------

# The full CLI surface is now live — no stubs remain.  Kept as an empty
# regression guard: any future stub reintroduced must re-populate this list.
_STUB_INVOCATIONS: list[list[str]] = []


class TestStubsExit2:
    def test_stubs(self) -> None:
        for args in _STUB_INVOCATIONS:
            result = runner.invoke(main, args)
            assert result.exit_code == 2, f"Expected exit 2 for {args!r}, got {result.exit_code}"
            assert "not implemented" in result.output.lower() or "not implemented" in (result.stderr or "").lower()
