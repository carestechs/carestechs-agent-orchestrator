"""Typer CLI entry point.

Every subcommand is a thin adapter that talks to the orchestrator HTTP
service — never the database directly (CLAUDE.md anti-pattern).  Only
``runs trace`` remains stubbed; streaming lands in FEAT-004.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import httpx
import typer

from app.cli_output import (
    render_json,
    render_run_summary,
    render_table,
    render_trace_line,
)

# ---------------------------------------------------------------------------
# App and sub-groups
# ---------------------------------------------------------------------------

main = typer.Typer(name="orchestrator", no_args_is_help=True)
runs_app = typer.Typer(name="runs", help="Inspect and manage agent runs.", no_args_is_help=True)
agents_app = typer.Typer(name="agents", help="Discover agent definitions.", no_args_is_help=True)
tasks_app = typer.Typer(
    name="tasks",
    help="Operator signals for in-flight runs (FEAT-005).",
    no_args_is_help=True,
)

main.add_typer(runs_app)
main.add_typer(agents_app)
main.add_typer(tasks_app)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------


class _GlobalState:
    api_base: str = "http://localhost:8000"
    api_key: str = ""
    json_output: bool = False
    quiet: bool = False
    verbose: bool = False


_state = _GlobalState()


@main.callback()
def root_callback(
    ctx: typer.Context,
    api_base: Annotated[
        str,
        typer.Option("--api-base", envvar="ORCHESTRATOR_API_BASE", help="Orchestrator HTTP base URL."),
    ] = "http://localhost:8000",
    api_key: Annotated[
        str,
        typer.Option("--api-key", envvar="ORCHESTRATOR_API_KEY", help="Bearer token for the control plane."),
    ] = "",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of human-formatted output."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress chatter."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show trace-level detail."),
    ] = False,
) -> None:
    """Agent-driven orchestration layer on top of carestechs-flow-engine."""
    _state.api_base = api_base
    _state.api_key = api_key
    _state.json_output = json_output
    _state.quiet = quiet
    _state.verbose = verbose


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




def _require_api_key() -> None:
    if not _state.api_key:
        typer.echo(
            "error: --api-key or ORCHESTRATOR_API_KEY is required for this command",
            err=True,
        )
        raise SystemExit(2)


def _client() -> httpx.Client:
    headers = {"Authorization": f"Bearer {_state.api_key}"} if _state.api_key else {}
    return httpx.Client(base_url=_state.api_base, headers=headers, timeout=30.0)


def _handle_response(resp: httpx.Response) -> dict[str, Any]:
    """Return the envelope body on 2xx; exit with a clear error on 4xx/5xx.

    Exit codes: 401 → 2 (auth), 404 → 1 (not found / user error), other 4xx
    → 1, 5xx → 3.  The error message is the Problem Details ``detail`` when
    present, otherwise the raw body.
    """
    if 200 <= resp.status_code < 300:
        return cast("dict[str, Any]", resp.json())

    detail = resp.text
    title = "error"
    try:
        problem_raw: Any = resp.json()
        if isinstance(problem_raw, dict):
            problem = cast("dict[str, Any]", problem_raw)
            title = str(problem.get("title") or title)
            detail = str(problem.get("detail") or detail)
    except ValueError:
        pass

    typer.echo(f"{title}: {detail}", err=True)
    if resp.status_code == 401:
        raise SystemExit(2)
    if 400 <= resp.status_code < 500:
        raise SystemExit(1)
    raise SystemExit(3)


def _parse_intake(
    intake_kv: Optional[list[str]],
    intake_file: Optional[str],
) -> dict[str, Any]:
    """Merge ``--intake-file`` (if any) with ``--intake KEY=VAL`` pairs."""
    result: dict[str, Any] = {}
    if intake_file:
        path = Path(intake_file)
        raw = path.read_text()
        try:
            import yaml  # deferred so the CLI still imports on a minimal env

            loaded: Any = yaml.safe_load(raw) or {}
        except Exception as exc:
            typer.echo(f"error: could not parse {intake_file}: {exc}", err=True)
            raise SystemExit(1) from exc
        if not isinstance(loaded, dict):
            typer.echo(f"error: {intake_file} must parse to a mapping", err=True)
            raise SystemExit(1)
        result.update(cast("dict[str, Any]", loaded))
    for kv in intake_kv or []:
        if "=" not in kv:
            typer.echo(f"error: --intake expects KEY=VAL, got {kv!r}", err=True)
            raise SystemExit(1)
        key, _, value = kv.partition("=")
        result[key] = value
    return result


def _emit(envelope: dict[str, Any], human_renderer: Any = None) -> None:
    """Either print the full envelope (``--json``) or delegate to *human_renderer*."""
    if _state.json_output or human_renderer is None:
        typer.echo(render_json(envelope))
        return
    typer.echo(human_renderer(envelope.get("data")))


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@main.command()
def run(
    agent_ref: Annotated[str, typer.Argument(help="Agent reference, e.g. lifecycle-agent@0.3.0")],
    intake: Annotated[
        Optional[list[str]],
        typer.Option("--intake", help="Intake field as KEY=VAL (repeatable)."),
    ] = None,
    intake_file: Annotated[
        Optional[str],
        typer.Option("--intake-file", help="YAML/JSON file providing the intake payload."),
    ] = None,
    budget_steps: Annotated[
        Optional[int],
        typer.Option("--budget-steps", help="Maximum steps."),
    ] = None,
    budget_tokens: Annotated[
        Optional[int],
        typer.Option("--budget-tokens", help="Maximum tokens."),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("--follow", help="Stream trace events live (not implemented, FEAT-004)."),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option("--wait", help="Block until run terminates."),
    ] = False,
    wait_timeout: Annotated[
        int,
        typer.Option("--wait-timeout", help="Maximum seconds to block for --wait."),
    ] = 300,
) -> None:
    """Start an agent run; optionally block until it terminates."""
    _require_api_key()
    intake_payload = _parse_intake(intake, intake_file)
    body: dict[str, Any] = {"agentRef": agent_ref, "intake": intake_payload}
    if budget_steps is not None or budget_tokens is not None:
        body["budget"] = {"maxSteps": budget_steps, "maxTokens": budget_tokens}

    with _client() as client:
        resp = client.post("/api/v1/runs", json=body)
        envelope = _handle_response(resp)
        _emit(envelope, render_run_summary)

        if not wait:
            return

        run_id = envelope["data"]["id"]
        deadline = time.monotonic() + wait_timeout
        while True:
            if time.monotonic() >= deadline:
                typer.echo(f"error: timed out after {wait_timeout}s", err=True)
                raise SystemExit(3)
            poll = client.get(f"/api/v1/runs/{run_id}")
            poll_env = _handle_response(poll)
            status = poll_env["data"]["status"]
            if status in {"completed", "failed", "cancelled"}:
                _emit(poll_env, render_run_summary)
                _exit_for_status(status)
                return
            time.sleep(0.5)


def _exit_for_status(status: str) -> None:
    code_map = {"completed": 0, "failed": 1, "cancelled": 2}
    raise SystemExit(code_map.get(status, 3))


@main.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload.")] = False,
) -> None:
    """Start the FastAPI service (webhooks + control plane)."""
    import uvicorn

    try:
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc


@main.command()
def doctor() -> None:
    """Diagnose local setup: config, providers, engine, signing keys."""
    from app.doctor import format_human, format_json, run_checks

    results = run_checks()
    if _state.json_output:
        typer.echo(format_json(results))
    else:
        typer.echo(format_human(results))

    has_failure = any(r.status == "fail" for r in results)
    raise SystemExit(2 if has_failure else 0)


# ---------------------------------------------------------------------------
# runs sub-commands
# ---------------------------------------------------------------------------


_RUN_COLS = ["id", "agentRef", "status", "stopReason", "startedAt", "endedAt"]
_STEP_COLS = ["stepNumber", "nodeName", "status", "dispatchedAt", "completedAt"]
_POLICY_COLS = ["id", "selectedTool", "provider", "model", "inputTokens", "outputTokens", "latencyMs"]


@runs_app.command("ls")
def runs_ls(
    status: Annotated[Optional[str], typer.Option("--status", help="Filter by run status.")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", help="Filter by agent ref.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.")] = 20,
) -> None:
    """List recent runs."""
    _require_api_key()
    params: dict[str, Any] = {"pageSize": limit}
    if status:
        params["status"] = status
    if agent:
        params["agentRef"] = agent
    with _client() as client:
        envelope = _handle_response(client.get("/api/v1/runs", params=params))
    _render_list(envelope, columns=_RUN_COLS)


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run UUID.")],
) -> None:
    """Show a run summary."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(client.get(f"/api/v1/runs/{run_id}"))
    _emit(envelope, render_run_summary)


@runs_app.command("cancel")
def runs_cancel(
    run_id: Annotated[str, typer.Argument(help="Run UUID.")],
    reason: Annotated[Optional[str], typer.Option("--reason", help="Cancellation reason.")] = None,
) -> None:
    """Cancel a running run."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(
            client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": reason})
        )
    _emit(envelope, render_run_summary)


@runs_app.command("trace")
def runs_trace(
    run_id: Annotated[str, typer.Argument(help="Run UUID.")],
    follow: Annotated[bool, typer.Option("--follow", help="Stream live.")] = False,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Only entries at or after this ISO-8601 timestamp."),
    ] = None,
    kind: Annotated[
        Optional[list[str]],
        typer.Option("--kind", help="Filter by kind (step, policy_call, webhook_event).  Repeatable."),
    ] = None,
) -> None:
    """Dump or stream a run's trace as NDJSON.

    Without ``--json`` each record is rendered as a compact human line.
    With ``--json`` the NDJSON is forwarded to stdout verbatim.  SIGINT
    during ``--follow`` exits ``0`` silently.
    """
    _require_api_key()
    params: dict[str, Any] = {}
    if follow:
        params["follow"] = "true"
    if since:
        params["since"] = since
    if kind:
        params["kind"] = kind  # httpx emits a repeated key for list values

    try:
        with _client() as client, client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/trace",
            params=params,
            timeout=None,
        ) as response:
            if response.status_code >= 400:
                response.read()
                _handle_response(response)
                return
            try:
                for raw in response.iter_lines():
                    if not raw:
                        continue
                    if _state.json_output:
                        typer.echo(raw)
                        continue
                    try:
                        record = json.loads(raw)
                    except ValueError:
                        typer.echo(raw)
                        continue
                    if not isinstance(record, dict):
                        typer.echo(raw)
                        continue
                    typer.echo(render_trace_line(cast("dict[str, Any]", record)))
            except KeyboardInterrupt:
                raise SystemExit(0) from None
    except httpx.ReadError:
        # Server closed mid-stream (e.g., process restart) — treat as clean end.
        pass


@runs_app.command("steps")
def runs_steps(
    run_id: Annotated[str, typer.Argument(help="Run UUID.")],
) -> None:
    """List steps for a run."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(client.get(f"/api/v1/runs/{run_id}/steps"))
    _render_list(envelope, columns=_STEP_COLS)


@runs_app.command("policy")
def runs_policy(
    run_id: Annotated[str, typer.Argument(help="Run UUID.")],
) -> None:
    """List policy decisions for a run."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(client.get(f"/api/v1/runs/{run_id}/policy-calls"))
    _render_list(envelope, columns=_POLICY_COLS)


# ---------------------------------------------------------------------------
# agents sub-commands
# ---------------------------------------------------------------------------


_AGENT_COLS = ["ref", "definitionHash", "path"]


@agents_app.command("ls")
def agents_ls() -> None:
    """List discoverable agent definitions."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(client.get("/api/v1/agents"))
    _render_list(envelope, columns=_AGENT_COLS)


@agents_app.command("show")
def agents_show(
    ref: Annotated[str, typer.Argument(help="Agent reference, e.g. lifecycle-agent@0.3.0")],
) -> None:
    """Print an agent definition and its intake schema."""
    _require_api_key()
    with _client() as client:
        envelope = _handle_response(client.get("/api/v1/agents"))
    items_raw: Any = envelope.get("data") or []
    items: list[dict[str, Any]] = [
        cast("dict[str, Any]", a) for a in items_raw if isinstance(a, dict)
    ]
    match = next((a for a in items if a.get("ref") == ref), None)
    if match is None:
        typer.echo(f"error: agent not found: {ref}", err=True)
        raise SystemExit(1)
    if _state.json_output:
        typer.echo(render_json({"data": match}))
    else:
        typer.echo(render_json(match))


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_list(envelope: dict[str, Any], *, columns: list[str]) -> None:
    if _state.json_output:
        typer.echo(render_json(envelope))
        return
    data_raw: Any = envelope.get("data") or []
    if not data_raw:
        typer.echo("(no rows)")
        return
    rows: list[dict[str, Any]] = [
        cast("dict[str, Any]", item) for item in data_raw if isinstance(item, dict)
    ]
    typer.echo(render_table(rows, columns))
    meta_raw: Any = envelope.get("meta")
    if isinstance(meta_raw, dict):
        meta = cast("dict[str, Any]", meta_raw)
        total = meta.get("totalCount")
        if total is not None:
            typer.echo(f"\ntotal: {total}")


# ---------------------------------------------------------------------------
# Operator signals (FEAT-005 / T-099)
# ---------------------------------------------------------------------------


def _handle_signal_response(resp: httpx.Response, task_id: str) -> None:
    """Signal-specific exit-code mapping.

    ``202`` → ``0`` (noting whether the signal was already received).
    ``404`` → ``1`` (run or task missing — operator fixable).
    ``409`` → ``2`` (run already terminal — distinct from other 4xx).
    ``401`` / ``5xx`` / connection error → ``3``.
    """
    if resp.status_code == 202:
        body_raw: Any = resp.json()
        body = cast("dict[str, Any]", body_raw) if isinstance(body_raw, dict) else {}
        meta_raw: Any = body.get("meta") or {}
        meta = cast("dict[str, Any]", meta_raw) if isinstance(meta_raw, dict) else {}
        if meta.get("alreadyReceived"):
            typer.echo(f"signal already received for {task_id}", err=True)
        else:
            typer.echo(f"signal accepted for {task_id}")
        raise SystemExit(0)

    detail = resp.text
    try:
        problem_raw: Any = resp.json()
        if isinstance(problem_raw, dict):
            problem = cast("dict[str, Any]", problem_raw)
            detail = str(problem.get("detail") or detail)
    except ValueError:
        pass

    if resp.status_code == 404:
        typer.echo(f"error: run or task not found ({detail})", err=True)
        raise SystemExit(1)
    if resp.status_code == 409:
        typer.echo(f"error: run already terminal ({detail})", err=True)
        raise SystemExit(2)
    if resp.status_code == 401:
        typer.echo("error: unauthorized — set --api-key or ORCHESTRATOR_API_KEY", err=True)
        raise SystemExit(3)
    typer.echo(f"error: unexpected status {resp.status_code} ({detail})", err=True)
    raise SystemExit(3)


@tasks_app.command("mark-implemented")
def mark_implemented(
    task_id: Annotated[str, typer.Argument(help="Task id, e.g. T-001")],
    run_id: Annotated[
        str,
        typer.Option("--run-id", help="The run currently awaiting this signal."),
    ],
    commit_sha: Annotated[
        Optional[str],
        typer.Option("--commit-sha", help="Commit SHA the operator just landed."),
    ] = None,
    notes: Annotated[
        Optional[str],
        typer.Option("--notes", help="Freeform operator notes."),
    ] = None,
) -> None:
    """POST an ``implementation-complete`` signal for *task_id* on *run_id*."""
    _require_api_key()

    payload: dict[str, Any] = {}
    if commit_sha:
        payload["commit_sha"] = commit_sha
    if notes:
        payload["notes"] = notes

    body: dict[str, Any] = {
        "name": "implementation-complete",
        "taskId": task_id,
        "payload": payload,
    }

    try:
        with _client() as client:
            resp = client.post(f"/api/v1/runs/{run_id}/signals", json=body)
    except httpx.HTTPError as exc:
        typer.echo(f"error: connection failed ({exc})", err=True)
        raise SystemExit(3) from exc

    _handle_signal_response(resp, task_id)


# ---------------------------------------------------------------------------
# Silence linters on unused params that are part of the CLI contract.
# ---------------------------------------------------------------------------

_ = (json,)


if __name__ == "__main__":
    main()
