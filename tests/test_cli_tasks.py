"""CLI tests for ``orchestrator tasks mark-implemented`` (FEAT-005 / T-099)."""

from __future__ import annotations

import json

import httpx
import respx
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_BASE = "http://cli-test.local"
_AUTH = {"ORCHESTRATOR_API_BASE": _BASE, "ORCHESTRATOR_API_KEY": "k"}
_RUN_ID = "11111111-2222-3333-4444-555555555555"
_SIGNAL_URL = f"{_BASE}/api/v1/runs/{_RUN_ID}/signals"


def _accepted_body(*, already: bool = False) -> dict[str, object]:
    return {
        "data": {
            "id": "00000000-0000-0000-0000-000000000001",
            "runId": _RUN_ID,
            "name": "implementation-complete",
            "taskId": "T-001",
            "payload": {},
            "receivedAt": "2026-04-18T12:00:00+00:00",
            "dedupeKey": "abc",
        },
        "meta": {"alreadyReceived": True} if already else None,
    }


class TestMarkImplementedHappy:
    @respx.mock
    def test_accepted_exits_zero(self) -> None:
        route = respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        assert route.called
        assert "signal accepted" in result.output

        body = json.loads(route.calls.last.request.content)
        assert body["name"] == "implementation-complete"
        assert body["taskId"] == "T-001"
        assert body["payload"] == {}

    @respx.mock
    def test_already_received_exits_zero_with_stderr(self) -> None:
        respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body(already=True)),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 0
        assert "already received" in result.output.lower()

    @respx.mock
    def test_forwards_commit_sha_and_notes(self) -> None:
        route = respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(202, json=_accepted_body()),
        )
        result = runner.invoke(
            main,
            [
                "tasks",
                "mark-implemented",
                "T-001",
                "--run-id",
                _RUN_ID,
                "--commit-sha",
                "abc1234",
                "--notes",
                "done",
            ],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        body = json.loads(route.calls.last.request.content)
        assert body["payload"] == {"commit_sha": "abc1234", "notes": "done"}


class TestMarkImplementedErrors:
    @respx.mock
    def test_404_exits_one(self) -> None:
        respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(
                404,
                json={
                    "type": "https://orchestrator.local/problems/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "run not found",
                },
            ),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @respx.mock
    def test_409_exits_two(self) -> None:
        respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(
                409,
                json={
                    "type": "https://orchestrator.local/problems/conflict",
                    "title": "Conflict",
                    "status": 409,
                    "detail": "run already terminal",
                },
            ),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 2
        assert "terminal" in result.output.lower()

    @respx.mock
    def test_401_exits_three(self) -> None:
        respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(
                401,
                json={
                    "type": "https://orchestrator.local/problems/unauthorized",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "bad token",
                },
            ),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 3
        assert "unauthorized" in result.output.lower()

    @respx.mock
    def test_5xx_exits_three(self) -> None:
        respx.post(_SIGNAL_URL).mock(
            return_value=httpx.Response(500, json={}),
        )
        result = runner.invoke(
            main,
            ["tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env=_AUTH,
        )
        assert result.exit_code == 3

    def test_missing_api_key_exits_two(self) -> None:
        result = runner.invoke(
            main,
            ["--api-key", "", "tasks", "mark-implemented", "T-001", "--run-id", _RUN_ID],
            env={"ORCHESTRATOR_API_BASE": _BASE},
        )
        assert result.exit_code == 2
