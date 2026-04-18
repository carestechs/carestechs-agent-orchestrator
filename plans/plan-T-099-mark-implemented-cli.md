# Implementation Plan: T-099 — `orchestrator tasks mark-implemented` CLI

## Task Reference
- **Task ID:** T-099
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-098

## Overview
Add a new Typer subcommand group `orchestrator tasks` with one command: `mark-implemented T-XXX --run-id <id> [--commit-sha SHA] [--notes TEXT]`. Thin HTTP client over `/api/v1/runs/{id}/signals`. Exit codes: 0 accepted, 1 not found, 2 terminal, 3 auth/5xx.

## Steps

### 1. Modify `src/app/cli.py`
Add a new Typer app + command:
```python
tasks_app = typer.Typer(name="tasks", help="Operator signals for in-flight runs.")
app.add_typer(tasks_app)

@tasks_app.command("mark-implemented")
def mark_implemented(
    task_id: str = typer.Argument(..., help="Task id, e.g. T-001"),
    run_id: UUID = typer.Option(..., "--run-id", help="The run currently awaiting this signal."),
    commit_sha: str | None = typer.Option(None, "--commit-sha"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    api_key = _require_api_key()
    payload: dict[str, Any] = {}
    if commit_sha:
        payload["commit_sha"] = commit_sha
    if notes:
        payload["notes"] = notes

    body = {"name": "implementation-complete", "task_id": task_id, "payload": payload}
    url = f"{_api_base()}/api/v1/runs/{run_id}/signals"
    with httpx.Client(timeout=10.0) as client:
        response = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
    _handle_signal_response(response, run_id, task_id)
```

Add `_handle_signal_response` helper:
```python
def _handle_signal_response(response: httpx.Response, run_id: UUID, task_id: str) -> None:
    if response.status_code in {200, 202}:
        meta = response.json().get("meta") or {}
        if meta.get("alreadyReceived"):
            typer.echo(f"signal already received for {task_id}", err=True)
        else:
            typer.echo(f"signal accepted for {task_id} (run {run_id})")
        raise typer.Exit(code=0)
    if response.status_code == 404:
        typer.echo("error: run or task not found", err=True)
        raise typer.Exit(code=1)
    if response.status_code == 409:
        typer.echo("error: run already terminal", err=True)
        raise typer.Exit(code=2)
    if response.status_code == 401:
        typer.echo("error: unauthorized — check ORCHESTRATOR_API_KEY", err=True)
        raise typer.Exit(code=3)
    typer.echo(f"error: unexpected status {response.status_code}", err=True)
    raise typer.Exit(code=3)
```

### 2. Modify `docs/ui-specification.md`
Draft the command under the Command Inventory table. Defer changelog entry to T-106.

### 3. Create `tests/test_cli_tasks.py`
Four respx-mocked cases:
- Happy (202) → exit 0, stdout contains `signal accepted`.
- Duplicate (202 + `alreadyReceived=true`) → exit 0, stderr contains `already received`.
- 404 → exit 1, stderr `run or task not found`.
- 409 → exit 2.
- 401 → exit 3.
- Connection error / 5xx → exit 3.
Also: `--commit-sha` + `--notes` forwarded correctly in request body.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/cli.py` | Modify | New `tasks` Typer group + `mark-implemented` command. |
| `docs/ui-specification.md` | Modify | Command inventory entry (changelog in T-106). |
| `tests/test_cli_tasks.py` | Create | 6 respx-mocked CLI tests. |

## Edge Cases & Risks
- **Adapter-thin rule**: CLI imports `httpx` and `typer` only. No `anthropic`, `sqlalchemy`, `subprocess`, `yaml`. T-106's extension of `test_adapters_are_thin.py` enforces this.
- **Required `--run-id`**: Typer's `...` sentinel makes it required. Users who forget get a clear Typer error, not a confusing HTTP 404.
- **Payload shape**: omit empty `commit_sha` / `notes` so the JSON is clean (not `{"commit_sha": null, "notes": null}`).
- **API-base resolution**: reuse the existing `_api_base()` helper from FEAT-002's CLI, which reads `--api-base` / env / config in that precedence.
- **5xx not distinguished from 401 in exit code**: both are exit 3. Operators checking exit codes get "something infrastructure-y went wrong, check logs." Acceptable — finer-grained exit codes would add no automation value.

## Acceptance Verification
- [ ] `orchestrator tasks mark-implemented T-001 --run-id <id>` returns exit 0 on 202.
- [ ] 404 → exit 1 + stderr message.
- [ ] 409 → exit 2 + stderr message.
- [ ] 401 / 5xx / connection error → exit 3.
- [ ] `--commit-sha` + `--notes` forwarded under `payload`.
- [ ] `name == "implementation-complete"` hardcoded (no CLI option to override).
- [ ] 6 respx tests pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean; adapter-thin rule still passes.
