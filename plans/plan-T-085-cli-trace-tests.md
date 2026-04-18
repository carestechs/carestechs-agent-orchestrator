# Implementation Plan: T-085 — CLI tests for `orchestrator runs trace`

## Task Reference
- **Task ID:** T-085
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-081

## Overview
respx-mocked CLI tests covering AC-6 + AC-7: the command issues the right URL with the right query params, renders the NDJSON body, handles `--json`/filters/auth-missing/404, and exits with the correct codes.

## Steps

### 1. Extend `tests/test_cli_runs.py`
Add a new test class `TestRunsTrace` at the bottom of the file.  Reuse `_BASE = "http://cli-test.local"`, `_AUTH`, and `_RUN_ID` constants already defined.

Test cases:

1. **`test_trace_renders_human_lines_by_default`**
   - Body = three NDJSON lines covering step / policy_call / webhook_event kinds.
   - `respx.get(f"{_BASE}/api/v1/runs/{_RUN_ID}/trace").mock(return_value=httpx.Response(200, content=body_bytes, headers={"content-type": "application/x-ndjson"}))`.
   - `result = runner.invoke(main, ["runs", "trace", _RUN_ID], env=_AUTH)`.
   - `assert result.exit_code == 0`.
   - Output should contain `"step #1"`, `"policy →"`, `"webhook"` substrings.

2. **`test_trace_json_flag_forwards_raw_lines_verbatim`**
   - Same mock as #1.
   - `result = runner.invoke(main, ["--json", "runs", "trace", _RUN_ID], env=_AUTH)`.
   - `assert result.exit_code == 0`.
   - Normalize both sides to lists of stripped lines; the output's NDJSON lines must equal the input bytes' NDJSON lines (modulo trailing newline the CLI appends via `typer.echo`).

3. **`test_trace_follow_sets_query_param`**
   - Mock a 200 empty body.
   - `runner.invoke(main, ["runs", "trace", _RUN_ID, "--follow"], env=_AUTH)`.
   - `req = route.calls.last.request`.
   - `assert req.url.params["follow"] == "true"`.

4. **`test_trace_kind_filter_repeatable`**
   - Mock same.
   - `runner.invoke(main, ["runs", "trace", _RUN_ID, "--kind", "step", "--kind", "policy_call"], env=_AUTH)`.
   - `assert req.url.params.get_list("kind") == ["step", "policy_call"]`.

5. **`test_trace_since_flag`**
   - `runner.invoke(main, ["runs", "trace", _RUN_ID, "--since", "2026-04-17T12:00:00Z"], env=_AUTH)`.
   - `assert req.url.params["since"] == "2026-04-17T12:00:00Z"`.

6. **`test_trace_missing_api_key_exits_2`**
   - `result = runner.invoke(main, ["--api-key", "", "runs", "trace", _RUN_ID], env={"ORCHESTRATOR_API_BASE": _BASE})`.
   - `assert result.exit_code == 2`.
   - Assert "api" in output (lowercase).

7. **`test_trace_404_exits_1_with_problem_details_message`**
   - Mock returns 404 with a Problem Details body containing `detail: "run not found: ..."`.
   - `result = runner.invoke(main, ["runs", "trace", _RUN_ID], env=_AUTH)`.
   - `assert result.exit_code == 1`.
   - Assert `"run not found"` in output (or stderr — check where `_handle_response` emits).

### Quality
- Each test should complete in < 200 ms (respx is in-process, the CLI uses a sync httpx client).
- `runner.invoke(...)` catches SystemExit and converts to `exit_code`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/test_cli_runs.py` | Modify | Add `TestRunsTrace` class with 7 cases. |

## Edge Cases & Risks
- `respx` mocking of a streaming response: `httpx.Response(200, content=body_bytes, headers={...})` works — the CLI's `iter_lines()` still produces lines from the in-memory body.  The behavior matches a real server that completes the body quickly.
- `--json` equality check: the CLI calls `typer.echo(raw)` per line.  `typer.echo` adds a `\n`.  The server's NDJSON also has a trailing `\n`.  Be careful in the byte-level assertion — normalize to `splitlines()` on both sides.
- `--follow` test #3 uses an empty body, so the CLI iterates zero lines and exits 0.  That's fine; we're only asserting the outbound query param.

## Acceptance Verification
- [ ] 7 tests, all green.
- [ ] Each test's outbound URL asserted where relevant.
- [ ] `--json` byte-forward check passes.
- [ ] Runtime under 2 s for the whole class.
