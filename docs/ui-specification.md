# UI Specification

## Overview

**v1 has no graphical user interface.** The stakeholder definition locks this: no UI for authoring agents, no hosted control plane, CLI and config-as-data only. The operator-facing surface is therefore a **CLI** (the primary entry point) and the **HTTP control plane** (see `api-spec.md`). This document specifies the CLI — the v1 equivalent of what a UI spec would cover for a GUI application.

A future GUI is possible (inspecting runs, browsing traces, authoring agent YAML interactively) but is explicitly out of scope until the primitives prove out. When/if it lands, this file will be extended with a proper screen inventory. The shape of that file (Design System, Screen Inventory, etc.) is preserved below as `N/A (v1)` placeholders so the v1→v2 diff is obvious.

### Key UI Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interaction paradigm | CLI (terminal commands + stdin/stdout) | Matches the persona (power user, config-as-data, terminal-native workflows — see `personas/primary-user.md`) |
| CLI framework | **Typer** | First-class Pydantic interop, auto-generated help, matches stack profile |
| Output format | Human-readable by default; `--json` flag for machine-readable NDJSON | Supports both interactive use and scripting/CI |
| Exit codes | `0` success; `1` run completed with a non-success terminal status (e.g., `failed`); `2` runtime/infrastructure error | Lets CI/scripts distinguish "ran fine, outcome wasn't success" from "couldn't run" |
| Long-running commands | Non-blocking by default (return run id + exit); `--follow` streams events live | Mirrors the API's 202-Accepted contract (AD-2); still ergonomic for interactive use |
| Config discovery | `pyproject.toml` `[tool.orchestrator]` section + env vars; no dotfiles of our own | One fewer file type for operators to manage |

### Design System / Component Library / Responsive / Screens

**N/A (v1).** No GUI surface. These sections exist in the template and will be filled when a GUI is actually in scope.

---

## CLI Surface

### Invocation

```bash
# Installed as a script on PATH
orchestrator <command> [options]

# Equivalent module invocation during development
uv run python -m app.cli <command> [options]
```

The `orchestrator` command is declared in `pyproject.toml` via `[project.scripts]` pointing at `app.cli:main`. The CLI is a thin adapter over the `ai` module's services — per the stack profile's "two entry points, one core" convention, every command MUST delegate to the same service functions the HTTP routes call.

### Global Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--api-base` | url | `http://localhost:8000` | Orchestrator HTTP base. If unset and the command needs the API, the CLI starts the service in-process |
| `--api-key` | string | `$ORCHESTRATOR_API_KEY` | Bearer token for the control plane |
| `--json` | flag | off | Emit NDJSON instead of human-formatted output |
| `--quiet / -q` | flag | off | Suppress progress chatter; keep only terminal outcome |
| `--verbose / -v` | flag | off | Show trace-level detail (policy prompts, tool args) |
| `--help` | flag | — | Standard Typer help |

### Command Inventory

| Command | Primary Action | Calls |
|---------|----------------|-------|
| `orchestrator run` | Start an agent run; optionally follow it | `POST /api/v1/runs` (+ stream trace if `--follow`) |
| `orchestrator runs ls` | List recent runs | `GET /api/v1/runs` |
| `orchestrator runs show <id>` | Show a run summary | `GET /api/v1/runs/{id}` |
| `orchestrator runs cancel <id>` | Cancel a running run | `POST /api/v1/runs/{id}/cancel` |
| `orchestrator runs trace <id>` | Stream or dump a run's trace | `GET /api/v1/runs/{id}/trace` |
| `orchestrator runs steps <id>` | List steps for a run | `GET /api/v1/runs/{id}/steps` |
| `orchestrator runs policy <id>` | List policy decisions for a run | `GET /api/v1/runs/{id}/policy-calls` |
| `orchestrator agents ls` | List discoverable agent definitions | `GET /api/v1/agents` |
| `orchestrator agents show <ref>` | Print an agent definition + its intake schema | `GET /api/v1/agents` (filtered) |
| `orchestrator tasks mark-implemented <task> --run-id <id>` | Deliver an `implementation-complete` signal (FEAT-005) | `POST /api/v1/runs/{id}/signals` |
| `orchestrator serve` | Start the FastAPI service (webhooks + control plane) in the foreground | — |
| `orchestrator doctor` | Diagnose local setup: config, LLM provider reachability, engine reachability, webhook signing key presence | `GET /health` + local checks |
| `orchestrator reconcile-aux [--since 24h] [--dry-run]` | Drain orphan `pending_aux_writes` rows by querying engine state (FEAT-008/T-170) | direct DB + flow-engine |

### Command Specifications

#### `orchestrator run`

> *Start an agent run. Optionally follow its trace until terminal.*

```
orchestrator run <agent-ref> [--intake KEY=VAL ...] [--intake-file PATH]
                             [--budget-steps N] [--budget-tokens N]
                             [--follow] [--wait]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `<agent-ref>` | positional | required | Stable agent reference, e.g. `lifecycle-agent@0.3.0` |
| `--intake KEY=VAL` | repeatable | — | Inline intake fields (e.g., `--intake featureBriefPath=docs/work-items/FEAT-042.md`) |
| `--intake-file` | path | — | YAML/JSON file providing the full intake payload |
| `--budget-steps` | int | agent default | Maximum steps before `budget_exceeded` |
| `--budget-tokens` | int | agent default | Maximum total tokens before `budget_exceeded` |
| `--follow` | flag | off | Reserved alias for ``runs trace --follow``; today use ``orchestrator runs trace <id> --follow`` directly |
| `--wait` | flag | off | Block until run terminates by polling; quiet |
| `--wait-timeout` | int | 300 | Maximum seconds `--wait` will block before exiting `3` |

**Behavior:**
- Without `--follow` / `--wait`: prints the `run_id` + initial status to stdout and returns. The run continues asynchronously on the service.
- With `--wait`: polls `GET /runs/{id}` every 500 ms until terminal; exits with the derived code.
- Streaming the live trace for a run: use ``orchestrator runs trace <id> --follow`` (see below).
- With `--json`: the full envelope is printed as pretty JSON to stdout.

**Exit codes** (`--wait` path):

| Terminal status | Exit code |
|-----------------|-----------|
| `completed` | 0 |
| `failed` (covers `budget_exceeded` + `error`) | 1 |
| `cancelled` | 2 |
| timeout or unknown status | 3 |
| missing API key, bad request, 4xx | 1 |
| 5xx | 3 |
| unauthenticated (401) | 2 |

**Example:**
```
$ orchestrator run lifecycle-agent@0.3.0 \
    --intake featureBriefPath=docs/work-items/FEAT-042.md \
    --follow
started run 01J8ZX... (lifecycle-agent@0.3.0)
[step 1] policy → generate_tasks({...})
[step 1] engine dispatched (engine_run=a9f2...)
[step 1] node_finished ✓ (took 18s)
...
run completed ✓  stopped_reason=done_node  duration=7m14s
```

---

#### `orchestrator runs ls`

> *List recent runs.*

```
orchestrator runs ls [--status STATUS] [--agent REF] [--limit N]
```

Defaults to the 20 most recent. `--json` emits one run-summary object per line. Human output is a compact table: `id`, `agent`, `status`, `started`, `duration`.

---

#### `orchestrator runs show <id>`

> *Show a run summary (one screen of info).*

Human output groups: **Run header** (id, agent, status, stop reason, timings), **Latest step** (number, node, status), **Counts** (steps, policy calls, webhooks). `--json` returns the full `RunSummaryDto`.

---

#### `orchestrator runs cancel <id>`

> *Cancel a running run. Idempotent.*

Prints the new status and exits `0`. Cancelling a terminal run is a no-op (prints current status) with exit `0`.

---

#### `orchestrator runs trace <id>`

> *Dump or stream a run's trace as NDJSON.*

```
orchestrator runs trace <id> [--follow] [--since TIMESTAMP] [--kind KIND ...]
```

| Option | Type | Description |
|--------|------|-------------|
| `--follow` | flag | Stream live until the run terminates; Ctrl-C exits `0` |
| `--since` | ISO-8601 timestamp | Only entries at or after this time |
| `--kind` | repeatable | Filter by `step` / `policy_call` / `webhook_event` |

Default output is one line per trace entry with a short human summary
(`step #N nodeName status`, `policy → selectedTool  tokens=x/y`,
`webhook eventType engine_run=…`).  `--json` forwards the server's
NDJSON byte-for-byte — suitable for ``jq`` pipelines.

**Exit codes:** `0` on normal close or SIGINT, `1` on run not found,
`2` on missing API key, `3` on server 5xx.

---

#### `orchestrator runs steps <id>` / `orchestrator runs policy <id>`

Human-readable tables backed by the corresponding list endpoints. `--json` for raw NDJSON.

---

#### `orchestrator agents ls` / `agents show <ref>`

`ls` prints a table of discovered agents (ref, path, node count). `show` prints the agent's metadata plus its intake schema as YAML.

---

#### `orchestrator tasks mark-implemented <task-id>`

> *FEAT-005 — deliver an `implementation-complete` signal to a lifecycle run paused at `wait_for_implementation`.*

```
orchestrator tasks mark-implemented T-001 --run-id <id> [--commit-sha SHA] [--notes TEXT]
```

| Option | Type | Description |
|--------|------|-------------|
| `<task-id>` | positional | The task the operator just implemented (e.g. `T-001`). |
| `--run-id` | UUID | The run currently awaiting this signal. Required. |
| `--commit-sha` | string | Commit SHA the operator just landed (forwarded under `payload.commit_sha`). |
| `--notes` | string | Freeform operator notes (forwarded under `payload.notes`). |

Posts a `202 Accepted` request to `/api/v1/runs/{id}/signals` with `name=implementation-complete`. The agent advances to `review` on receipt.

**Exit codes:** `0` on accept (including idempotent "already received"); `1` on 404 (run or task not found); `2` on 409 (run already terminal); `3` on 401 / 5xx / connection error.

---

#### `orchestrator serve`

> *Run the FastAPI service in the foreground (webhooks + control plane).*

```
orchestrator serve [--host HOST] [--port PORT] [--reload]
```

Thin wrapper over `uvicorn app.main:app`. The main reason this is a CLI subcommand (instead of pointing operators at `uvicorn` directly) is so the `doctor` and `run` commands can bootstrap the service the same way.

---

#### `orchestrator doctor`

> *Check that local setup is viable.*

Performs, in order: config loads; `ORCHESTRATOR_API_KEY` set; `ENGINE_WEBHOOK_SECRET` set; `LLM_PROVIDER` + `ANTHROPIC_API_KEY` (or equivalent) set; LLM reachable (one cheap tokens=1 call); flow engine reachable (`GET {engine}/health`); at least one agent definition discoverable on disk.

Human output is a checklist with ✓/✗ per check. `--json` emits a structured report. Non-zero exit if any check fails.

---

## Output Conventions

| Concern | Rule |
|---------|------|
| Default format | Human-readable, monochrome-safe (no color required to be correct; color is additive) |
| Machine format | NDJSON via `--json` (one object per line, never a single multi-line array) |
| Errors | Single line: `error: <message>` to stderr, plus an RFC 7807 problem-details JSON object to stderr when `--json` is set |
| Timestamps | ISO 8601 with timezone offset in both human and JSON output |
| IDs | Full UUIDs in JSON; may be abbreviated in human output (first 10 chars) for readability |
| Secrets | NEVER echo API keys, webhook secrets, or full policy prompts at default verbosity. `--verbose` is required for full policy prompts and MUST redact headers marked secret |

## Configuration Sources

In precedence order (highest wins):
1. Command-line options (`--api-base`, `--api-key`, etc.)
2. Environment variables (`ORCHESTRATOR_*`, `ANTHROPIC_API_KEY`, etc.)
3. `pyproject.toml` `[tool.orchestrator]` table in the current repo
4. Built-in defaults

The CLI never writes configuration. All persistence is operator-controlled (env files, `pyproject.toml`, shell). No "first-run wizard" in v1.

## AI Task Generation Notes

- **No GUI tasks in v1.** If a task implies a screen, stop and flag it — it's either out of scope or belongs in an extension to this spec.
- **CLI commands are thin adapters.** Each subcommand MUST delegate to a service function in the `ai` module — the same one the HTTP route calls. NEVER put business logic in the CLI module.
- **Respect exit-code semantics.** `0`/`1`/`2` have specific meanings (above); a task that collapses them (e.g., always `0`) breaks CI consumers.
- **Streaming vs one-shot.** `run`, `runs trace` with `--follow`, etc. are streaming commands backed by the NDJSON trace endpoint. Keep them responsive: flush per line, never buffer a whole run in memory.
- **Output parity.** Anything a human command shows, the `--json` form MUST also expose (plus likely more). Never make `--json` a subset.
- **Signing boundary.** The CLI is a client of the HTTP service, not a back door. NEVER give the CLI a path that bypasses auth or writes directly to the database — it would break the "one core" invariant.

## Changelog

- 2026-04-24 — FEAT-008/T-170 — `orchestrator reconcile-aux [--since Nh|Nd|Nm] [--dry-run]`: new admin CLI that drains orphan `pending_aux_writes` rows by querying engine state for each pending signal's target item. Materializes when the engine confirms the transition landed; preserves the pending row when the engine says it didn't (operator triage). Idempotent. Requires flow-engine configured. Exit codes: `0` clean, `2` errors during reconcile or engine not configured.
- 2026-04-18 — FEAT-005 — `orchestrator tasks mark-implemented T-XXX --run-id <id>`: new CLI command for delivering operator-injected implementation-complete signals. Exit codes `0/1/2/3` align with the project's exit-code semantics (409 → `2` is distinct from other 4xx → `1`).
- 2026-04-18 — FEAT-004 — `orchestrator runs trace` is now real: `--follow`, `--since`, `--kind` (repeatable), `--json`.  Exit-code table + NDJSON output format documented.  The `run --follow` alias remains reserved for a later convenience.
- 2026-04-18 — FEAT-002 — `orchestrator run --wait` + `--wait-timeout` documented with exit-code table; `runs ls/show/cancel/steps/policy` and `agents ls/show` now make real HTTP calls (stubs retired). `runs trace` + `run --follow` remain deferred to FEAT-004 (trace streaming).
- 2026-04-15 — Initial version. Redefined scope: v1 has no GUI; this document specifies the CLI surface (global options, command inventory, 10 commands, output conventions, config precedence). GUI sections preserved as `N/A (v1)` placeholders for future extension.
