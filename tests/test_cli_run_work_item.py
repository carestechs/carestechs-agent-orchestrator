"""FEAT-014 / T-289 — ``orchestrator run --work-item`` smoke tests.

Coverage:

- happy path: file is read client-side and uploaded as ``intake.workItem``.
- missing file: exit 2 with no HTTP call.
- unrecognized filename: exit 2 (typer.BadParameter).
- mutual exclusion with legacy ``--intake workItemPath=...`` (exit 2, no HTTP).
- BUG / IMP filename parses produce the right kind.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_BASE = "http://cli-test.local"
_AUTH = {"ORCHESTRATOR_API_BASE": _BASE, "ORCHESTRATOR_API_KEY": "k"}
_RUNS_URL = f"{_BASE}/api/v1/runs"


def _accepted_body() -> dict[str, object]:
    return {
        "data": {
            "id": "11111111-2222-3333-4444-555555555555",
            "agentRef": "lifecycle-agent@0.3.0",
            "status": "running",
            "startedAt": "2026-05-12T12:00:00+00:00",
        }
    }


class TestWorkItemHappyPath:
    @respx.mock
    def test_uploads_content(self, tmp_path: Path) -> None:
        brief = tmp_path / "FEAT-100-example.md"
        brief.write_text("# Test brief\nbody content here", encoding="utf-8")

        route = respx.post(_RUNS_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(brief)],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        assert route.called

        sent = json.loads(route.calls.last.request.content)
        assert sent["intake"]["workItem"] == {
            "id": "FEAT-100",
            "kind": "FEAT",
            "content": "# Test brief\nbody content here",
        }

    @respx.mock
    def test_bug_filename_kind(self, tmp_path: Path) -> None:
        brief = tmp_path / "BUG-7-something.md"
        brief.write_text("# Bug\n", encoding="utf-8")
        respx.post(_RUNS_URL).mock(return_value=httpx.Response(202, json=_accepted_body()))
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(brief)],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        sent = json.loads(respx.calls.last.request.content)
        assert sent["intake"]["workItem"]["id"] == "BUG-7"
        assert sent["intake"]["workItem"]["kind"] == "BUG"

    @respx.mock
    def test_imp_filename_no_slug(self, tmp_path: Path) -> None:
        brief = tmp_path / "IMP-42.md"
        brief.write_text("# Imp\n", encoding="utf-8")
        respx.post(_RUNS_URL).mock(return_value=httpx.Response(202, json=_accepted_body()))
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(brief)],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        sent = json.loads(respx.calls.last.request.content)
        assert sent["intake"]["workItem"]["id"] == "IMP-42"
        assert sent["intake"]["workItem"]["kind"] == "IMP"


class TestWorkItemErrorPaths:
    @respx.mock
    def test_missing_file_exits_2_no_http_call(self, tmp_path: Path) -> None:
        route = respx.post(_RUNS_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        bogus = tmp_path / "does-not-exist.md"
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(bogus)],
            env=_AUTH,
        )
        assert result.exit_code == 2
        assert not route.called
        assert "not found" in result.output.lower()

    @respx.mock
    def test_unrecognized_filename_exits_2(self, tmp_path: Path) -> None:
        bogus = tmp_path / "random.md"
        bogus.write_text("# stuff\n", encoding="utf-8")
        route = respx.post(_RUNS_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(bogus)],
            env=_AUTH,
        )
        assert result.exit_code == 2
        assert not route.called

    @respx.mock
    def test_mutually_exclusive_with_legacy_flag(self, tmp_path: Path) -> None:
        brief = tmp_path / "FEAT-1-x.md"
        brief.write_text("# x\n", encoding="utf-8")
        route = respx.post(_RUNS_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            [
                "run",
                "lifecycle-agent@0.3.0",
                "--work-item",
                str(brief),
                "--intake",
                f"workItemPath={brief}",
            ],
            env=_AUTH,
        )
        assert result.exit_code == 2
        assert not route.called
        assert "mutually exclusive" in result.output.lower()

    @respx.mock
    def test_lowercase_filename_rejected(self, tmp_path: Path) -> None:
        bogus = tmp_path / "feat-100.md"
        bogus.write_text("# x\n", encoding="utf-8")
        route = respx.post(_RUNS_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            ["run", "lifecycle-agent@0.3.0", "--work-item", str(bogus)],
            env=_AUTH,
        )
        assert result.exit_code == 2
        assert not route.called
