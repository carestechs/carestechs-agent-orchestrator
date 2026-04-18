"""CLI tests for the ``run`` and ``runs`` sub-commands (T-046).

Responses are mocked with :mod:`respx` so we can assert the exact URLs,
headers and bodies the CLI issues without touching the real service.
"""

from __future__ import annotations

import json
import uuid

import httpx
import respx
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_BASE = "http://cli-test.local"
_AUTH = {"ORCHESTRATOR_API_BASE": _BASE, "ORCHESTRATOR_API_KEY": "k"}
_RUN_ID = str(uuid.uuid4())


def _summary(status: str = "pending") -> dict[str, object]:
    return {
        "data": {
            "id": _RUN_ID,
            "agentRef": "a@1.0",
            "status": status,
            "stopReason": None,
            "startedAt": "2026-04-18T00:00:00Z",
            "endedAt": None,
        }
    }


class TestRunCommand:
    @respx.mock
    def test_run_posts_and_returns_summary(self) -> None:
        route = respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(202, json=_summary())
        )
        result = runner.invoke(
            main,
            ["run", "a@1.0", "--intake", "brief=hello"],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body == {"agentRef": "a@1.0", "intake": {"brief": "hello"}}

    @respx.mock
    def test_run_requires_api_key(self) -> None:
        result = runner.invoke(
            main,
            ["--api-key", "", "run", "a@1.0"],
            env={"ORCHESTRATOR_API_BASE": _BASE},
        )
        assert result.exit_code == 2
        assert "api-key" in result.output.lower() or "api_key" in result.output.lower()

    @respx.mock
    def test_run_surfaces_problem_details_detail(self) -> None:
        respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(
                404,
                json={
                    "type": "https://orchestrator.local/problems/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "agent not found: a@1.0",
                },
            )
        )
        result = runner.invoke(main, ["run", "a@1.0"], env=_AUTH)
        assert result.exit_code == 1
        assert "agent not found" in result.output

    @respx.mock
    def test_run_wait_exits_0_on_completed(self) -> None:
        respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(202, json=_summary())
        )
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}").mock(
            return_value=httpx.Response(200, json=_summary("completed"))
        )
        result = runner.invoke(
            main, ["run", "a@1.0", "--wait", "--wait-timeout", "5"], env=_AUTH
        )
        assert result.exit_code == 0

    @respx.mock
    def test_run_wait_exits_1_on_failed(self) -> None:
        respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(202, json=_summary())
        )
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}").mock(
            return_value=httpx.Response(200, json=_summary("failed"))
        )
        result = runner.invoke(
            main, ["run", "a@1.0", "--wait", "--wait-timeout", "5"], env=_AUTH
        )
        assert result.exit_code == 1

    @respx.mock
    def test_run_wait_exits_2_on_cancelled(self) -> None:
        respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(202, json=_summary())
        )
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}").mock(
            return_value=httpx.Response(200, json=_summary("cancelled"))
        )
        result = runner.invoke(
            main, ["run", "a@1.0", "--wait", "--wait-timeout", "5"], env=_AUTH
        )
        assert result.exit_code == 2


class TestRunsSubcommands:
    @respx.mock
    def test_runs_ls_hits_endpoint_with_filters(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [],
                    "meta": {"totalCount": 0, "page": 1, "pageSize": 5},
                },
            )
        )
        result = runner.invoke(
            main,
            ["runs", "ls", "--status", "pending", "--agent", "a@1.0", "--limit", "5"],
            env=_AUTH,
        )
        assert result.exit_code == 0, result.output
        assert route.called
        req = route.calls.last.request
        assert req.url.params["status"] == "pending"
        assert req.url.params["agentRef"] == "a@1.0"
        assert req.url.params["pageSize"] == "5"

    @respx.mock
    def test_runs_show(self) -> None:
        detail = {
            "data": {
                "id": _RUN_ID,
                "agentRef": "a@1.0",
                "agentDefinitionHash": "sha256:" + "0" * 64,
                "intake": {},
                "status": "completed",
                "stopReason": "done_node",
                "startedAt": "2026-04-18T00:00:00Z",
                "endedAt": "2026-04-18T00:01:00Z",
                "traceUri": "file:///tmp/t.jsonl",
                "stepCount": 3,
                "lastStep": None,
            }
        }
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}").mock(
            return_value=httpx.Response(200, json=detail)
        )
        result = runner.invoke(main, ["runs", "show", _RUN_ID], env=_AUTH)
        assert result.exit_code == 0
        assert "completed" in result.output

    @respx.mock
    def test_runs_cancel_posts_reason(self) -> None:
        route = respx.post(f"{_BASE}/api/v1/runs/{_RUN_ID}/cancel").mock(
            return_value=httpx.Response(200, json=_summary("cancelled"))
        )
        result = runner.invoke(
            main,
            ["runs", "cancel", _RUN_ID, "--reason", "operator abort"],
            env=_AUTH,
        )
        assert result.exit_code == 0
        body = json.loads(route.calls.last.request.content)
        assert body == {"reason": "operator abort"}

    @respx.mock
    def test_runs_steps(self) -> None:
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/steps").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": str(uuid.uuid4()),
                            "stepNumber": 1,
                            "nodeName": "analyze",
                            "status": "completed",
                            "nodeInputs": {},
                            "nodeResult": None,
                            "error": None,
                            "dispatchedAt": None,
                            "completedAt": None,
                        }
                    ],
                    "meta": {"totalCount": 1, "page": 1, "pageSize": 20},
                },
            )
        )
        result = runner.invoke(main, ["runs", "steps", _RUN_ID], env=_AUTH)
        assert result.exit_code == 0
        assert "analyze" in result.output

    @respx.mock
    def test_runs_policy(self) -> None:
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/policy-calls").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [],
                    "meta": {"totalCount": 0, "page": 1, "pageSize": 20},
                },
            )
        )
        result = runner.invoke(main, ["runs", "policy", _RUN_ID], env=_AUTH)
        assert result.exit_code == 0


class TestJsonOutput:
    @respx.mock
    def test_json_flag_emits_full_envelope(self) -> None:
        respx.post(f"{_BASE}/api/v1/runs").mock(
            return_value=httpx.Response(202, json=_summary())
        )
        result = runner.invoke(
            main, ["--json", "run", "a@1.0"], env=_AUTH
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "data" in parsed
        assert parsed["data"]["id"] == _RUN_ID


# ---------------------------------------------------------------------------
# runs trace (T-085)
# ---------------------------------------------------------------------------


_TRACE_NDJSON = (
    b'{"kind":"step","data":{"id":"s1","stepNumber":1,'
    b'"nodeName":"analyze_brief","status":"completed",'
    b'"nodeInputs":{},"engineRunId":"eng-1"}}\n'
    b'{"kind":"policy_call","data":{"id":"p1","stepId":"s1",'
    b'"provider":"stub","model":"stub-v1","selectedTool":"analyze_brief",'
    b'"toolArguments":{},"availableTools":[],"inputTokens":5,'
    b'"outputTokens":1,"latencyMs":12,"createdAt":"2026-04-18T00:00:00Z"}}\n'
    b'{"kind":"webhook_event","data":{"id":"w1",'
    b'"eventType":"node_finished","engineRunId":"eng-1","payload":{},'
    b'"signatureOk":true,"receivedAt":"2026-04-18T00:00:01Z"}}\n'
)


class TestRunsTrace:
    @respx.mock
    def test_trace_renders_human_lines_by_default(self) -> None:
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                200,
                content=_TRACE_NDJSON,
                headers={"content-type": "application/x-ndjson"},
            )
        )
        result = runner.invoke(main, ["runs", "trace", _RUN_ID], env=_AUTH)
        assert result.exit_code == 0, result.output
        assert "step #1" in result.output
        assert "policy →" in result.output
        assert "webhook" in result.output

    @respx.mock
    def test_trace_json_flag_forwards_raw_lines_verbatim(self) -> None:
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                200,
                content=_TRACE_NDJSON,
                headers={"content-type": "application/x-ndjson"},
            )
        )
        result = runner.invoke(
            main, ["--json", "runs", "trace", _RUN_ID], env=_AUTH
        )
        assert result.exit_code == 0
        expected_lines = [
            line for line in _TRACE_NDJSON.decode().splitlines() if line
        ]
        actual_lines = [
            line for line in result.output.splitlines() if line
        ]
        assert actual_lines == expected_lines

    @respx.mock
    def test_trace_follow_sets_query_param(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                200,
                content=b"",
                headers={"content-type": "application/x-ndjson"},
            )
        )
        result = runner.invoke(
            main, ["runs", "trace", _RUN_ID, "--follow"], env=_AUTH
        )
        assert result.exit_code == 0
        assert route.called
        assert route.calls.last.request.url.params["follow"] == "true"

    @respx.mock
    def test_trace_kind_filter_repeatable(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                200,
                content=b"",
                headers={"content-type": "application/x-ndjson"},
            )
        )
        result = runner.invoke(
            main,
            ["runs", "trace", _RUN_ID, "--kind", "step", "--kind", "policy_call"],
            env=_AUTH,
        )
        assert result.exit_code == 0
        assert route.called
        params = route.calls.last.request.url.params
        assert list(params.get_list("kind")) == ["step", "policy_call"]

    @respx.mock
    def test_trace_since_flag(self) -> None:
        route = respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                200,
                content=b"",
                headers={"content-type": "application/x-ndjson"},
            )
        )
        result = runner.invoke(
            main,
            ["runs", "trace", _RUN_ID, "--since", "2026-04-17T12:00:00Z"],
            env=_AUTH,
        )
        assert result.exit_code == 0
        assert route.called
        assert route.calls.last.request.url.params["since"] == "2026-04-17T12:00:00Z"

    def test_trace_missing_api_key_exits_2(self) -> None:
        result = runner.invoke(
            main,
            ["--api-key", "", "runs", "trace", _RUN_ID],
            env={"ORCHESTRATOR_API_BASE": _BASE},
        )
        assert result.exit_code == 2
        assert "api" in result.output.lower()

    @respx.mock
    def test_trace_404_exits_1_with_problem_details_message(self) -> None:
        respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(
            return_value=httpx.Response(
                404,
                json={
                    "type": "https://orchestrator.local/problems/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": f"run not found: {_RUN_ID}",
                },
            )
        )
        result = runner.invoke(main, ["runs", "trace", _RUN_ID], env=_AUTH)
        assert result.exit_code == 1
        assert "run not found" in result.output
