"""FEAT-014 / T-290 — ``orchestrator import-work-items`` smoke tests.

Coverage:

- happy path with FEAT / BUG / IMP fixtures.
- idempotent re-run (200s reported as reused).
- ``--dry-run`` makes zero HTTP requests.
- malformed filenames skipped without failing.
- 409 conflict exits 1.
- non-.md files ignored silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_BASE = "http://cli-test.local"
_AUTH = {"ORCHESTRATOR_API_BASE": _BASE, "ORCHESTRATOR_API_KEY": "k"}
_UPLOAD_URL = f"{_BASE}/api/v1/work-items/upload"


def _ok_201(ref: str) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "data": {
                "id": "11111111-2222-3333-4444-555555555555",
                "externalRef": ref,
                "type": "FEAT",
                "title": "x",
                "status": "open",
                "openedBy": "import",
                "createdAt": "2026-05-12T12:00:00+00:00",
                "updatedAt": "2026-05-12T12:00:00+00:00",
            }
        },
    )


def _ok_200(ref: str) -> httpx.Response:
    body = _ok_201(ref).json()
    body["meta"] = {"alreadyReceived": True}
    return httpx.Response(200, json=body)


def _conflict_409() -> httpx.Response:
    return httpx.Response(
        409,
        json={
            "type": "https://orchestrator.local/problems/work-item-content-conflict",
            "title": "Work item content conflict",
            "status": 409,
            "detail": "body sha256 mismatch",
        },
    )


@pytest.fixture
def briefs_dir(tmp_path: Path) -> Path:
    (tmp_path / "FEAT-100-test.md").write_text("# Test FEAT\nbody")
    (tmp_path / "BUG-1-test.md").write_text("# Test BUG\nbody")
    (tmp_path / "IMP-3-test.md").write_text("# Test IMP\nbody")
    (tmp_path / "random.md").write_text("not a work item")
    (tmp_path / "notes.txt").write_text("ignored")
    return tmp_path


class TestImportHappyPaths:
    @respx.mock
    def test_imports_three_kinds(self, briefs_dir: Path) -> None:
        respx.post(_UPLOAD_URL).mock(side_effect=lambda req: _ok_201(json.loads(req.content)["id"]))
        result = runner.invoke(main, ["import-work-items", str(briefs_dir)], env=_AUTH)
        assert result.exit_code == 0, result.output
        assert "Inserted: 3" in result.output
        assert "Reused:   0" in result.output
        assert "Malformed (skipped): 1" in result.output

    @respx.mock
    def test_rerun_is_idempotent(self, briefs_dir: Path) -> None:
        respx.post(_UPLOAD_URL).mock(side_effect=lambda req: _ok_200(json.loads(req.content)["id"]))
        result = runner.invoke(main, ["import-work-items", str(briefs_dir)], env=_AUTH)
        assert result.exit_code == 0, result.output
        assert "Inserted: 0" in result.output
        assert "Reused:   3" in result.output

    @respx.mock
    def test_dry_run_makes_no_requests(self, briefs_dir: Path) -> None:
        route = respx.post(_UPLOAD_URL).mock(return_value=_ok_201("FEAT-1"))
        result = runner.invoke(
            main, ["import-work-items", str(briefs_dir), "--dry-run"], env=_AUTH
        )
        assert result.exit_code == 0, result.output
        assert not route.called
        assert "[DRY RUN" in result.output
        assert "Inserted: 3" in result.output


class TestImportErrorPaths:
    @respx.mock
    def test_conflict_exits_with_code_1(self, briefs_dir: Path) -> None:
        # First file succeeds; second file conflicts; third succeeds.
        responses = [_ok_201("BUG-1"), _conflict_409(), _ok_201("IMP-3")]
        respx.post(_UPLOAD_URL).mock(side_effect=responses)
        result = runner.invoke(main, ["import-work-items", str(briefs_dir)], env=_AUTH)
        assert result.exit_code == 1, result.output
        assert "Conflicts:" in result.output
        assert "Conflicted: 1" in result.output

    @respx.mock
    def test_non_md_files_ignored(self, briefs_dir: Path) -> None:
        # ``notes.txt`` exists in the fixture; not malformed, just not selected.
        respx.post(_UPLOAD_URL).mock(side_effect=lambda req: _ok_201(json.loads(req.content)["id"]))
        result = runner.invoke(main, ["import-work-items", str(briefs_dir)], env=_AUTH)
        assert result.exit_code == 0, result.output
        # 3 real briefs + ``random.md`` (malformed); ``notes.txt`` is silently
        # skipped (not in the malformed list, not POSTed).
        assert "notes.txt" not in result.output

    @respx.mock
    def test_missing_directory_exits_1(self, tmp_path: Path) -> None:
        route = respx.post(_UPLOAD_URL).mock(return_value=_ok_201("FEAT-1"))
        result = runner.invoke(
            main, ["import-work-items", str(tmp_path / "nope")], env=_AUTH
        )
        assert result.exit_code == 1, result.output
        assert not route.called
