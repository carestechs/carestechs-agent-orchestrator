# Implementation Plan: T-046 — CLI wiring (real HTTP calls)

## Task Reference
- **Task ID:** T-046
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-040, T-041, T-042, T-043, T-044

## Overview
Replace `_not_implemented(...)` bodies for every command except `runs trace` (FEAT-004). CLI becomes a client of the HTTP service per CLAUDE.md anti-pattern ("not a DB back door"). Adds `run --wait` with exit-code table.

## Steps

### 1. Create `src/app/cli_output.py` (if not already present)
- `def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str`: minimal fixed-width table; no external deps.
- `def render_run_summary(data: dict) -> str`: human-formatted single-run block.
- `def render_json(payload: Any) -> str`: `json.dumps(payload, indent=2, default=str)`.

### 2. Modify `src/app/cli.py` — shared helpers
- Add `_client()` factory: returns `httpx.Client` with base URL = `_state.api_base`, `Authorization: Bearer {_state.api_key}` if set, timeout = 30 s.
- Add `_require_api_key()`: if `_state.api_key` is empty → `typer.echo(...)` + exit 2.
- Add `_handle_response(resp)`: on 2xx return `resp.json()`; else extract Problem Details `detail` + `title`, print, exit with status-appropriate code (401 → 2, 404 → 1, 4xx → 1, 5xx → 3).

### 3. Modify `src/app/cli.py` — command bodies

#### `run`
- `_require_api_key()`.
- Build `intake` dict from `--intake KEY=VAL` list (split on first `=`).
- If `--intake-file`, `yaml.safe_load` it and merge.
- POST `/api/v1/runs` with `{agentRef, intake, budget: {maxSteps, maxTokens}}`.
- Print `runId` in human format, or the envelope in `--json` mode.
- If `--wait`: poll `GET /api/v1/runs/{id}` every 500 ms until `status` terminal. Then print final summary. Exit codes: `completed` → 0, `failed` → 1, `cancelled` → 2, timeout or unknown → 3.

#### `runs ls`
- `_require_api_key()`, GET `/api/v1/runs` with filters, render table.

#### `runs show`
- GET `/api/v1/runs/{id}`, render `render_run_summary`.

#### `runs cancel`
- POST `/api/v1/runs/{id}/cancel` with `{reason}`, render updated summary.

#### `runs steps`
- GET `/api/v1/runs/{id}/steps`, render table.

#### `runs policy`
- GET `/api/v1/runs/{id}/policy-calls`, render table.

#### `runs trace`
- Unchanged — still prints `not implemented yet` with exit 2 (FEAT-004).

#### `agents ls`
- GET `/api/v1/agents`, render table.

#### `agents show`
- GET `/api/v1/agents` (no detail endpoint in v1), filter by ref in CLI, pretty-print.

### 4. Create new tests
- `tests/test_cli_runs.py`: use `respx` + `CliRunner` to mock HTTP responses; assert correct commands issue correct URLs, bodies, and headers. Assert exit codes.
- `tests/test_cli_agents.py`: same pattern for `agents ls/show`.
- `tests/test_cli_run_wait.py`: mock the polling sequence `pending → running → completed` and assert exit 0; verify `failed` → exit 1; `cancelled` → exit 2.

### 5. Modify `tests/test_cli_stubs.py`
- Remove stub invocations that are now implemented (`run`, `runs ls/show/cancel/steps/policy`, `agents ls/show`).
- Keep only `runs trace` in the stub list.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/cli_output.py` | Create/Modify | Table + JSON renderers. |
| `src/app/cli.py` | Modify | Replace all stubs except `runs trace`. |
| `tests/test_cli_runs.py`, `tests/test_cli_agents.py`, `tests/test_cli_run_wait.py` | Create | HTTP-mocked CLI tests. |
| `tests/test_cli_stubs.py` | Modify | Shrink stub list to `runs trace`. |

## Edge Cases & Risks
- CLI uses sync `httpx.Client` (Typer commands are sync-first). No need for `asyncio.run`.
- Missing API key: current global option defaults to `""`. `_require_api_key` surfaces a clear error before any network call.
- `--wait` timeout: add `--wait-timeout` option, default 300 s. Exit 3 on timeout.
- Thin-adapter AST check (`tests/test_adapters_are_thin.py`) must still pass — CLI uses httpx, not sqlalchemy/anthropic. Already allowed.

## Acceptance Verification
- [ ] Every command issues exactly one HTTP call (absent `--wait`).
- [ ] Missing API key → exit 2 with clear message.
- [ ] Non-2xx surfaces Problem Details `detail`.
- [ ] `--json` outputs the full envelope.
- [ ] `--wait` exit-code table asserted in tests.
- [ ] `test_adapters_are_thin.py` still passes.
