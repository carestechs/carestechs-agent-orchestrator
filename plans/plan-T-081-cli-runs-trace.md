# Implementation Plan: T-081 — `orchestrator runs trace` real body

## Task Reference
- **Task ID:** T-081
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-080

## Overview
Replace the `_not_implemented("runs trace")` stub with a real body that opens `httpx.Client.stream(...)` against the endpoint, iterates `iter_lines()`, and renders each NDJSON record either as a human-formatted line or raw JSON (`--json`).  SIGINT during `--follow` exits 0 silently.

## Steps

### 1. Modify `src/app/cli_output.py`
- Add `render_trace_line(record: dict[str, Any]) -> str`:
  ```python
  def render_trace_line(record: dict[str, Any]) -> str:
      """Human-format one NDJSON trace record."""
      kind = record.get("kind", "?")
      data = record.get("data", {})
      if kind == "step":
          return (
              f"step #{data.get('stepNumber', '?')} "
              f"{data.get('nodeName', '?')} "
              f"{data.get('status', '?')}"
              + (f"  ({data.get('engineRunId')})" if data.get("engineRunId") else "")
          )
      if kind == "policy_call":
          return (
              f"policy → {data.get('selectedTool', '?')}  "
              f"tokens={data.get('inputTokens', 0)}/{data.get('outputTokens', 0)}"
          )
      if kind == "webhook_event":
          return (
              f"webhook {data.get('eventType', '?')} "
              f"engine_run={data.get('engineRunId', '?')}"
          )
      return json.dumps(record)  # unknown kind — fall back to raw
  ```

### 2. Modify `src/app/cli.py`
- Replace the `runs_trace` body.  Keep the existing signature (`--follow`, `--since`, `--kind`, inherits global `--json`).
- New body:
  ```python
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
          typer.Option("--kind", help="Filter by kind (repeatable)."),
      ] = None,
  ) -> None:
      """Dump or stream a run's trace as NDJSON."""
      _require_api_key()
      params: dict[str, Any] = {}
      if follow:
          params["follow"] = "true"
      if since:
          params["since"] = since
      if kind:
          params["kind"] = kind  # httpx emits repeatable key for list values

      with _client() as client:
          try:
              with client.stream(
                  "GET",
                  f"/api/v1/runs/{run_id}/trace",
                  params=params,
                  timeout=None,  # follow mode stays open indefinitely
              ) as response:
                  if response.status_code >= 400:
                      # Read the full body for a proper Problem Details message.
                      response.read()
                      _handle_response(response)
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
                              typer.echo(raw)  # forward non-JSON lines as-is
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
  ```
- Import `render_trace_line` from `app.cli_output`.

### 3. Modify `tests/test_cli_stubs.py`
- Remove `["runs", "trace", ...]` from `_STUB_INVOCATIONS`.  The list may now be empty; keep it + the `TestStubsExit2` class but parameterized over an empty list becomes zero tests.  Alternative: delete the class entirely; decide during implementation — empty-list regression guard is slightly better.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/cli_output.py` | Modify | Add `render_trace_line`. |
| `src/app/cli.py` | Modify | Real `runs_trace` body. |
| `tests/test_cli_stubs.py` | Modify | Remove `runs trace` from stub list. |

## Edge Cases & Risks
- `httpx.Client.stream(...)` with `timeout=None` disables the read timeout — correct for `--follow` mode.  Without `--follow` the default 30 s timeout in `_client()` may kick in for a very slow server; acceptable v1 trade-off.
- Ctrl-C handling: `KeyboardInterrupt` may propagate from `iter_lines()` or from the `with` exit.  Catch inside the stream loop; re-raise as `SystemExit(0)`.
- `iter_lines()` strips the trailing `\n`.  The CLI re-adds one via `typer.echo`, so NDJSON output in `--json` mode is still valid NDJSON.
- `response.read()` on a streaming response may be rejected by httpx if streaming is in progress; in our 4xx branch we haven't started consuming yet, so `read()` works to fetch the full body for the Problem Details message.
- Connection-reset mid-stream (`httpx.ReadError`): treat as clean end, exit 0.  Test coverage lives in T-085.

## Acceptance Verification
- [ ] Default (no `--json`) emits one human-formatted line per record.
- [ ] `--json` forwards raw NDJSON to stdout byte-for-byte.
- [ ] Outbound URL carries `?follow=true`, `?since=...`, `?kind=...&kind=...` correctly.
- [ ] 404 → exit 1, "run not found" on stderr.
- [ ] Missing API key → exit 2.
- [ ] Ctrl-C in `--follow` → exit 0 silently.
- [ ] Adapter-thin check still passes.
