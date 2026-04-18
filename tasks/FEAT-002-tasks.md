# Task Breakdown: FEAT-002 — Agent Runtime Loop (Stub-Policy End-to-End)

> **Source:** `docs/work-items/FEAT-002-runtime-loop.md`
> **Generated:** 2026-04-17
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

Tasks are grouped in build order: Foundation → Backend (building blocks → runtime loop → service layer) → Integration → Testing → Polish. Every task's **Workflow** is `standard`; v1 is CLI-only so `mockup-first` never applies. Task IDs continue from FEAT-001's final `T-030`.

---

## Foundation

### T-031: Add YAML dependency and `AgentDefinition` Pydantic schema

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add `pyyaml` to runtime deps. Create `AgentDefinition`, `AgentNode`, and `AgentFlow` Pydantic models in `src/app/modules/ai/agents.py` matching the YAML surface described in the Feature Brief (`ref`, `version`, `nodes[*]`, `flow`, `intake_schema`, `terminal_nodes`, `default_budget`, `description`). Define a `tests/fixtures/agents/sample-linear.yaml` with 3 nodes used across the suite.

**Rationale:**
Every subsequent runtime task reads an `AgentDefinition`. Landing the schema + fixture first removes ambiguity about the YAML contract and unblocks parallel work on the loader, tool-builder, and stop conditions.

**Acceptance Criteria:**
- [ ] `pyyaml>=6` added to `pyproject.toml`; `uv lock` regenerated.
- [ ] `AgentDefinition.model_validate` accepts the fixture YAML; missing required fields raise a Pydantic `ValidationError`.
- [ ] Fixture YAML round-trips: `yaml.safe_load` → `AgentDefinition.model_validate` → `.model_dump(mode="json")` yields a stable dict with sorted keys.
- [ ] Pyright + ruff stay clean.

**Files to Modify/Create:**
- `pyproject.toml` — add `pyyaml` to `dependencies`.
- `src/app/modules/ai/agents.py` — dataclass-style Pydantic models (no loader yet; that's T-032).
- `tests/fixtures/agents/sample-linear.yaml` — 3-node linear flow used by integration tests.
- `tests/modules/ai/test_agent_schema.py` — schema validation happy/missing/extra cases.

**Technical Notes:**
Use `ConfigDict(populate_by_name=True, alias_generator=to_camel)` to match the rest of the DTO layer. `terminal_nodes` is a `set[str]`; rejecting an empty set keeps "done" reachability from being silently undefined.

---

### T-032: Agent loader (`load_agent`, `list_agents`) with definition hashing

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-031

**Description:**
Implement `load_agent(ref: str) -> AgentDefinition` and `list_agents() -> list[AgentDefinition]` in `agents.py`. Walk `Settings.agents_dir`, match `ref` against `{ref}@{version}.yaml` (plus bare `{ref}.yaml` for unversioned). Compute `agent_definition_hash` as `sha256` of the canonical YAML bytes. Handle missing file (`NotFoundError`) and missing `AGENTS_DIR` (return empty list for `list_agents`; `NotFoundError` for `load_agent`).

**Rationale:**
The loader is the single entry point for agent data. Hashing at load time pins reproducibility on the `Run` (per data-model.md rule "`agent_definition_hash` computed once, never rewritten").

**Acceptance Criteria:**
- [ ] `load_agent("sample-linear@1.0")` returns a validated `AgentDefinition` whose `agent_definition_hash` matches a manually-computed sha256 of the canonical YAML.
- [ ] `list_agents()` returns all YAMLs in `AGENTS_DIR` sorted by `(ref, version)`.
- [ ] Non-existent ref → `NotFoundError` with the ref in the message.
- [ ] Invalid YAML → `ValidationError` (surfaces per-field errors through the 400 path).
- [ ] `AGENTS_DIR` not set / dir missing → `list_agents() == []` (graceful).

**Files to Modify/Create:**
- `src/app/modules/ai/agents.py` — add loader functions.
- `tests/modules/ai/test_agents_loader.py` — happy, unknown ref, missing dir, invalid YAML, hash stability.

---

### T-033: Stop-condition module (pure functions)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-031

**Description:**
Create `src/app/modules/ai/stop_conditions.py` with five pure functions — `is_done_node`, `is_budget_exceeded`, `is_policy_terminated`, `is_error`, `is_cancelled` — each returning `StopReason | None`. Also export a single `evaluate(state) -> StopReason | None` that runs them in a documented priority order (cancelled > error > budget > policy_terminated > done_node).

**Rationale:**
Pure, side-effect-free rules are trivial to unit-test and impossible to subtly misorder at runtime. Putting priority in one place prevents the loop from needing to re-evaluate order on every iteration.

**Acceptance Criteria:**
- [ ] Each rule takes a typed `RuntimeState` dataclass (fields: `last_node`, `step_count`, `token_count`, `budget`, `last_policy_error`, `last_engine_error`, `cancel_requested`, `terminal_nodes`) and returns `StopReason | None`.
- [ ] `evaluate` returns the first non-None in the priority order.
- [ ] 100 % branch coverage in unit tests.

**Files to Modify/Create:**
- `src/app/modules/ai/stop_conditions.py`.
- `tests/modules/ai/test_stop_conditions.py` — parameterized over the cartesian product of inputs.

---

### T-034: Tool-definition builder (agent node → `ToolDefinition`)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-031

**Description:**
In `src/app/modules/ai/tools/__init__.py` add `build_tools(agent: AgentDefinition, available_nodes: Iterable[str]) -> list[ToolDefinition]`. Each node becomes one tool: `name = node.name`, `description = node.description`, `parameters = node.input_schema` (already JSON Schema). Append a built-in `terminate` tool (no parameters) so the policy can trigger `policy_terminated` explicitly.

**Rationale:**
"Tool definition doubles as policy action space" (CLAUDE.md pattern). Centralizing the conversion keeps the runtime loop from reimplementing it per iteration and keeps gating (omit a node from `available_nodes` → tool disappears) correct by construction.

**Acceptance Criteria:**
- [ ] `build_tools(fixture, ["node_a", "node_b"])` returns 3 tools: `node_a`, `node_b`, `terminate` (in that order).
- [ ] Gating: a node not in `available_nodes` is absent from the output.
- [ ] Unit tests cover: all nodes available, partial gate, empty gate (only `terminate`).

**Files to Modify/Create:**
- `src/app/modules/ai/tools/__init__.py` — add `build_tools` + `TERMINATE_TOOL_NAME` constant.
- `tests/modules/ai/test_tools_builder.py`.

---

## Backend — Building Blocks

### T-035: JSONL `TraceStore` implementation (AD-5 v1)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-031

**Description:**
Add `src/app/modules/ai/trace_jsonl.py` with `JsonlTraceStore` implementing the FEAT-001 `TraceStore` Protocol. Writes one NDJSON line per call to `record_step`, `record_policy_call`, `record_webhook_event` into `.trace/<run_id>.jsonl` (path configurable via `Settings.trace_dir`, default `.trace`). Uses async file I/O (`aiofiles` or `loop.run_in_executor` with a shared thread) and an `asyncio.Lock` keyed per run id to keep lines atomic. Update the `TraceStore` factory to dispatch on a new `Settings.trace_backend` setting (`"noop"` vs `"jsonl"`, default `"jsonl"`).

**Rationale:**
AD-5 v1 is load-bearing for AC-3 (the JSONL file being readable proves policy traceability) and AC-6 (composition integrity). The Protocol seam is already carved; this task is its first real implementation.

**Acceptance Criteria:**
- [ ] Three write methods append well-formed JSON lines (valid JSON when read one line at a time).
- [ ] Concurrent writes for the *same* run serialize (no interleaved bytes). Concurrent writes for *different* runs are independent.
- [ ] `open_run_stream(run_id)` replays the existing lines and `yield`s as new ones are appended — at least the "read existing" half (tailing for live streams is a FEAT-004 concern; the simpler read-once is enough here).
- [ ] `Settings.trace_dir` + `Settings.trace_backend` added with sensible defaults; unit tests assert the factory selects correctly.
- [ ] Files are created with mode `0600` (ops best practice — traces may contain secrets).

**Files to Modify/Create:**
- `src/app/modules/ai/trace_jsonl.py`.
- `src/app/modules/ai/trace.py` — factory update.
- `src/app/config.py` — `trace_dir` and `trace_backend` fields.
- `src/app/core/dependencies.py` — `get_trace_store` dep if not already present.
- `pyproject.toml` — add `aiofiles` to deps (or document the executor approach).
- `tests/modules/ai/test_trace_jsonl.py`.

---

### T-036: `FlowEngineClient.dispatch_node` full implementation

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-031

**Description:**
Replace the `NotImplementedYet` body with a real `async def dispatch_node(*, run_id, step_id, agent_ref, node_name, node_inputs, callback_url) -> str` that POSTs to `{engine_base_url}/nodes/dispatch` and returns the engine's `engine_run_id`. Wrap transport + HTTP errors via the existing `_request` helper so `EngineError` carries `engine_http_status`, `engine_correlation_id`, and the original body.

**Rationale:**
AC-8 (engine failure → `stop_reason=error`) is only testable once dispatch is real. The error-boundary contract is already established in FEAT-001; this task implements the happy path and wires it into `EngineError` at the seam.

**Acceptance Criteria:**
- [ ] `respx`-mocked 202 returns the `engineRunId` from the payload.
- [ ] `respx`-mocked 500 raises `EngineError` with `engine_http_status=500`.
- [ ] Connection errors raise `EngineError` with `engine_http_status=None`.
- [ ] The payload shape matches the flow-engine API (documented inline with a link to the engine's API spec); request body is JSON, `Content-Type: application/json`, auth header passes the optional `engine_api_key`.
- [ ] Timeout of 10 s for dispatch (configurable via `Settings.engine_dispatch_timeout_seconds`).

**Files to Modify/Create:**
- `src/app/modules/ai/engine_client.py`.
- `src/app/config.py` — `engine_dispatch_timeout_seconds` (default 10).
- `tests/modules/ai/test_engine_client_dispatch.py`.

**Technical Notes:**
Callback URL is built from `Settings.public_base_url + /hooks/engine/events`. Add `public_base_url` to config.

---

### T-037: Run-loop supervisor (async task registry)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-035

**Description:**
Create `src/app/modules/ai/supervisor.py` with `RunSupervisor`: a process-local registry keyed by `run_id` holding the asyncio `Task`, an `asyncio.Event` for wake-up, and a cancellation flag. Public API: `spawn(run_id, coro)`, `wake(run_id)`, `cancel(run_id)`, `shutdown()` (awaits all tasks with a configurable grace timeout). Tasks that raise are logged at `ERROR` with full traceback and removed from the registry; re-raising is avoided to keep the supervisor itself alive.

**Rationale:**
The loop needs somewhere to live after `POST /api/v1/runs` returns. An in-process supervisor — not Celery, not a DB job queue — is the v1 choice (per scope lock). Centralizing it also makes the AC-7 wake-up-on-webhook timing provable and cancellation (AC-4) precise.

**Acceptance Criteria:**
- [ ] `spawn` returns immediately after scheduling; the coro runs in the background.
- [ ] `wake(run_id)` sets the `Event`; a coro awaiting it observes the wake within 10 ms in tests.
- [ ] `cancel(run_id)` raises `CancelledError` inside the target coro within 50 ms and removes the entry on completion.
- [ ] `shutdown()` cancels all outstanding runs and awaits them; returns within 2× the configured grace.
- [ ] A failing coro logs the exception and does NOT crash the supervisor.

**Files to Modify/Create:**
- `src/app/modules/ai/supervisor.py`.
- `src/app/core/dependencies.py` — `get_supervisor` FastAPI dep (singleton).
- `tests/modules/ai/test_supervisor.py`.

---

### T-038: Webhook-driven reconciliation (step update + loop wake)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-037, T-035

**Description:**
Extend `service.ingest_engine_event` (FEAT-001 implementation) so that, after persisting the `WebhookEvent`, it:
1. Looks up the step, transitions its status per event type (`node_started → in_progress`, `node_finished → completed`, `node_failed → failed`, `flow_terminated` → mark step terminal with the included reason), writes `node_result`/`error`, sets `completed_at` when terminal. State-machine monotonic (never downgrades).
2. Calls `supervisor.wake(run_id)` so the loop advances.
3. Records a line in the JSONL trace via `TraceStore.record_webhook_event`.

Split into `ingest_engine_event` (persist) + `reconcile_step_from_event` (state transition) + `notify_loop` (wake) helpers so each can be unit-tested in isolation.

**Rationale:**
AC-7 (webhook → next policy call within 100 ms) is the quantitative version of the architectural claim that webhooks drive the loop, not polling. Splitting the function cleanly keeps the service layer readable as it grows.

**Acceptance Criteria:**
- [ ] `node_finished` for a `dispatched` step transitions to `completed` + fills `node_result` + `completed_at`.
- [ ] An out-of-order `node_started` after `completed` is ignored (no status rollback); logged at `DEBUG`.
- [ ] A `node_failed` event writes `error` and transitions to `failed`.
- [ ] A `flow_terminated` event on the parent run writes `final_state` (best-effort from payload) — run-level terminal reason still comes from stop_conditions.
- [ ] `supervisor.wake(run_id)` is called exactly once per event that materially advances state.
- [ ] Late event for a terminal run: persists, does NOT wake, does NOT mutate the step if step is also terminal.
- [ ] Unit tests with a fake supervisor + trace store cover each branch.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py` — split + extend.
- `src/app/modules/ai/reconciliation.py` — new module for the state-transition helper (thin pure function).
- `tests/modules/ai/test_reconciliation.py`.

---

## Backend — Runtime Loop

### T-039: Runtime loop implementation (replaces FEAT-001 stub)

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-032, T-033, T-034, T-035, T-036, T-037, T-038

**Description:**
Replace `src/app/modules/ai/runtime.py`'s body with the real loop. Signature:
```python
async def run_loop(*, run_id: UUID, agent: AgentDefinition, intake: dict,
                   policy: LLMProvider, engine: FlowEngineClient,
                   trace: TraceStore, supervisor: RunSupervisor,
                   session_factory: Callable[[], AsyncSession]) -> None:
```
Each iteration: open its own `AsyncSession` (loop tasks must not share the request session); read the latest `Run`; evaluate stop-conditions; build tools; call `policy.chat_with_tools`; persist `PolicyCall`; if `TERMINATE` → terminate with `policy_terminated`; else create `Step` (`pending`), `engine.dispatch_node` → `dispatched`, write trace line, await `supervisor.await_wake(run_id)` (with per-step timeout from YAML), then loop. On any raised exception: terminate with `error`, persist `final_state`, cancel outstanding engine work best-effort.

**Rationale:**
This task is the feature. Every earlier task is scaffolding; every later task is integration or verification of the loop. The AD-3 success metric "removing the LLM → deterministic pipeline still runs" becomes a passing test the moment this lands with the stub policy.

**Acceptance Criteria:**
- [ ] Loop iterates exactly once per policy call and terminates on the first stop condition.
- [ ] `PolicyCall` row is persisted BEFORE `Step` dispatch (order matters for traceability: we log *what we decided* before *what we did*).
- [ ] Step timeout from agent YAML enforced via `asyncio.wait_for(supervisor.await_wake(...), timeout=...)`; timeout → step `failed`, run `error`.
- [ ] `RunMemory.data` is deep-merged with each node's result payload via a service helper; no cross-run reads.
- [ ] Cancelled runs surface `CancelledError` and terminate with `stop_reason=cancelled` (the supervisor's injection path works).
- [ ] Every terminal path sets `ended_at`, `stop_reason`, `final_state` in a single session commit, followed by a final trace line.

**Files to Modify/Create:**
- `src/app/modules/ai/runtime.py` — full rewrite.
- `tests/modules/ai/test_runtime_iterations.py` — unit tests using fakes for engine, supervisor, trace store.

**Technical Notes:**
The composition-integrity test (T-054) is the acceptance gate for this task; internal unit tests are valuable but the integration test is the one that proves "removed LLM → deterministic pipeline". Keep the loop body small enough to read in one screen — helper functions in a companion `runtime_helpers.py` are fine.

---

## Backend — Service Layer

### T-040: `start_run` — non-blocking with supervisor spawn

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-039, T-032

**Description:**
Implement `start_run(request, db) -> RunSummaryDto`: validate the `agentRef` exists via the loader, validate the `intake` against the agent's JSON schema, insert a `Run` row (`status=pending`, `agent_definition_hash`, `trace_uri`, `started_at=now`), create the empty `RunMemory`, commit, then `supervisor.spawn(run.id, run_loop(...))`. Returns the summary DTO. MUST NOT await the loop — AD-2.

**Rationale:**
AC-2: 202 within 50 ms. The request handler's envelope wrap already exists; the service function is where the invariant lives.

**Acceptance Criteria:**
- [ ] Returns within 50 ms (integration test with timing assertion).
- [ ] Invalid `agentRef` → `NotFoundError` BEFORE a `Run` row is written.
- [ ] Intake not matching the agent's `intake_schema` → `ValidationError` with per-field `errors` BEFORE a `Run` row is written.
- [ ] Successful path: exactly one `Run` row + one `RunMemory` row exist immediately after return; the supervised task is registered.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py` — replace `start_run` body.
- `tests/modules/ai/test_service_start_run.py`.

---

### T-041: `list_runs` + `get_run` with filters and last-step summary

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-039

**Description:**
Implement `list_runs(db, status=None, agent_ref=None, page=1, page_size=20) -> (list[RunSummaryDto], total)` with indexed filter/sort. Implement `get_run(run_id, db) -> RunDetailDto` with a sub-query that fetches the highest-numbered step to fill `last_step`. Return DTOs, not ORM objects.

**Rationale:**
AC-5. These reads are the observability surface the CLI and later the `/health` dashboard consume.

**Acceptance Criteria:**
- [ ] Filters: `status` exact-match; `agent_ref` exact-match. Combined filters AND-together.
- [ ] Pagination: `(page, pageSize)` honored; `total` accurate; invalid combos (page=0, pageSize>100) rejected at the DTO layer already.
- [ ] `get_run` on an unknown id → `NotFoundError`.
- [ ] `last_step` populated when ≥1 step exists; `null` otherwise.
- [ ] Queries use the `ix_runs_status_started_at` index for dashboard-style reads (visible in `EXPLAIN` — not tested automatically, but implementation matches the data-model.md intent).

**Files to Modify/Create:**
- `src/app/modules/ai/service.py`.
- `src/app/modules/ai/repository.py` — thin query helpers (keeps `service.py` readable).
- `tests/modules/ai/test_service_list_get.py`.

---

### T-042: `cancel_run` — state transition + supervisor cancel

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-040

**Description:**
Implement `cancel_run(run_id, request, db)`: no-op if already terminal (returns current summary), else flip to `status=cancelled`, `stop_reason=cancelled`, `ended_at=now`, append the optional `reason` to `final_state`; commit; then `supervisor.cancel(run_id)`.

**Rationale:**
AC-4 requires ≤500 ms turnaround. The service function's correctness (state transition before supervisor cancel) matters so a concurrent webhook doesn't revive a cancelled run.

**Acceptance Criteria:**
- [ ] Double-cancel is idempotent (second call returns the same terminal summary).
- [ ] Cancelling a `pending`/`running` run terminates the loop task within 500 ms.
- [ ] Late webhook after cancel persists but does NOT transition the step (step is terminal via the run's cancel path).

**Files to Modify/Create:**
- `src/app/modules/ai/service.py`.
- `tests/modules/ai/test_service_cancel.py`.

---

### T-043: `list_steps` + `list_policy_calls` with pagination

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-039

**Description:**
Both read-only paginated services with the same pattern as `list_runs`. Order: `step_number ASC` for steps; `created_at ASC` for policy calls.

**Rationale:**
AC-5. Completes the control-plane read surface.

**Acceptance Criteria:**
- [ ] Unknown `run_id` → `NotFoundError` (no implicit empty list).
- [ ] DTOs round-trip via `api-spec.md` envelope shape.
- [ ] Pagination `meta.totalCount` accurate.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py`.
- `tests/modules/ai/test_service_lists.py`.

---

### T-044: `list_agents` via the loader

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-032

**Description:**
Delegate to `agents.list_agents()` and shape each `AgentDefinition` into `AgentDto` (ref, definition_hash, path, intake_schema, available_nodes).

**Rationale:**
AC-5 includes `/agents`. CLI `agents ls` consumes this.

**Acceptance Criteria:**
- [ ] Empty `AGENTS_DIR` → empty list (not an error).
- [ ] Invalid YAML in one file surfaces as a 500 Problem Details — not a silent skip.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py`.
- `tests/modules/ai/test_service_agents.py`.

---

## Integration

### T-045: App lifespan — supervisor + zombie-run reconciliation

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-037, T-040

**Description:**
Replace the module-level `app = create_app()` pattern with a FastAPI `lifespan` context manager. On startup: instantiate the singleton `RunSupervisor`, bind it to app state, then sweep the DB for `status=running` rows (orphans from a prior process) and transition each to `status=failed`, `stop_reason=error`, writing a final trace line explaining "process restart". On shutdown: `supervisor.shutdown(grace=5s)`.

**Rationale:**
Edge-case from the brief: "FastAPI process restart mid-run" — we MUST NOT leave zombie `running` rows.

**Acceptance Criteria:**
- [ ] App startup recovers N orphan runs in O(N) queries (one scan, one `UPDATE`).
- [ ] Supervisor lives for the app's lifetime and is the same instance across requests (verified by identity check in a dependency).
- [ ] Graceful shutdown cancels in-flight runs; a test triggers it and checks they end up `cancelled` not `failed`.

**Files to Modify/Create:**
- `src/app/main.py`.
- `tests/integration/test_lifespan_zombie_reconciliation.py`.

---

### T-046: CLI wiring — real HTTP calls to the control plane

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-040, T-041, T-042, T-043, T-044

**Description:**
Replace `_not_implemented(...)` bodies in `src/app/cli.py` for `run`, `runs ls/show/cancel/steps/policy`, `agents ls/show` with real httpx calls against the configured `--api-base` with the `--api-key` Bearer token. Respect `--json` (raw envelope) vs human (table / list). `runs trace` stays a stub (FEAT-004).

Add `run --wait`: polls `GET /runs/{id}` every 500 ms until terminal, then prints the summary. Exit code `0` for `completed`, `1` for `failed`/`error`, `2` for `cancelled`.

**Rationale:**
AC-7 from FEAT-001 said every command must be invocable; AC-5 from FEAT-002 says they return real data. This task ties them together through the HTTP boundary (per CLAUDE.md anti-pattern: CLI is a client, not a DB back door).

**Acceptance Criteria:**
- [ ] Each command issues exactly one HTTP call (ignoring `--wait` polling).
- [ ] Missing `--api-key` → exit `2` with a clear message (pre-flight check).
- [ ] Non-2xx responses surface the Problem Details `detail` and exit with a command-appropriate code.
- [ ] `--json` emits the exact response envelope; human format is a sensible table (use `rich`? or stdlib — pick one and note in CLAUDE.md).
- [ ] `test_cli_stubs.py` shrinks accordingly; new `test_cli_run_wait.py` asserts the polling exit-code table.

**Files to Modify/Create:**
- `src/app/cli.py`.
- `src/app/cli_output.py` — if not already present, add small table/json formatter.
- `tests/test_cli_run_wait.py`, `tests/test_cli_runs.py`, `tests/test_cli_agents.py`.

**Technical Notes:**
Stand up `httpx.AsyncClient` per command. Use `asyncio.run` at the Typer boundary. Don't introduce a dependency on `rich` unless you need it; stdlib `textwrap` + `print` covers the v1 human format.

---

### T-047: Doctor — agents-dir check

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-032

**Description:**
Add a check to `src/app/doctor.py`: "agents_dir readable" — reports `✓` if `Settings.agents_dir` exists and `list_agents()` succeeds; `✗` with the exception message otherwise. Not fatal when dir is missing (a new user may not have authored an agent yet); only fatal when the dir is unreadable or YAML parsing fails.

**Rationale:**
`doctor` is the "did you set up right" summary. A missing agents dir is a soft warning; a corrupt YAML is a hard fail.

**Acceptance Criteria:**
- [ ] New check appears in both human + JSON output.
- [ ] Missing dir → `warn` (not `fail`); exit code unchanged.
- [ ] Unreadable / invalid YAML → `fail`; exit code `2`.

**Files to Modify/Create:**
- `src/app/doctor.py`.
- `tests/test_cli_doctor.py` — extend.

---

## Testing

### T-048: Agent-loader unit tests — edge cases

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-032

**Description:**
Cover the missing-dir, missing-file, invalid-yaml, two-versions-of-same-ref, hash-stability, and aliasing cases not exercised by T-032's happy-path tests. Use `tmp_path` fixtures so no real `AGENTS_DIR` is touched.

**Acceptance Criteria:**
- [ ] ≥8 test cases parameterized over edge scenarios.
- [ ] No test relies on state outside its `tmp_path`.

**Files to Modify/Create:**
- `tests/modules/ai/test_agents_loader_edges.py`.

---

### T-049: Stop-condition + tool-builder unit tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-033, T-034

**Description:**
Table-driven tests over the five stop-condition rules and the tool-builder gating logic. Assert priority ordering explicitly.

**Acceptance Criteria:**
- [ ] Priority conflict cases pass (e.g., budget exceeded AND policy terminated → returns `budget_exceeded` first per the documented order).
- [ ] Tool-builder tests cover full gate, empty gate, and unknown-node-in-available-set (ignored, not error).

**Files to Modify/Create:**
- `tests/modules/ai/test_stop_conditions.py` (extends T-033 tests).
- `tests/modules/ai/test_tools_builder.py` (extends T-034).

---

### T-050: Engine-client dispatch tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-036

**Description:**
Parameterized `respx` tests: 202 happy, 400/500/503 status errors, connection error, timeout. Assert the outbound payload shape and headers. Assert the correlation-id parsing from response headers into `EngineError`.

**Acceptance Criteria:**
- [ ] All five outcomes asserted.
- [ ] Payload shape matches the documented flow-engine API.
- [ ] Timeout triggers an `EngineError` with `engine_http_status=None`.

**Files to Modify/Create:**
- `tests/modules/ai/test_engine_client_dispatch.py` (extends T-036).

---

### T-051: Service unit tests (start, list, get, cancel, steps, policy-calls, agents)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-040, T-041, T-042, T-043, T-044

**Description:**
One test file per service function, hitting the real DB fixture and a fake supervisor. Covers happy, filter combinations, pagination boundaries (first page, last page, empty), validation errors, not-found.

**Acceptance Criteria:**
- [ ] Every public service function has ≥3 tests (happy, edge, error).
- [ ] Tests do NOT launch real run loops — the supervisor is swapped for a no-op.

**Files to Modify/Create:**
- `tests/modules/ai/test_service_start_run.py`, `test_service_list_get.py`, `test_service_cancel.py`, `test_service_lists.py`, `test_service_agents.py`.

---

### T-052: JSONL trace store unit tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-035

**Description:**
Unit tests for `JsonlTraceStore`: file creation, mode `0600`, append semantics, line atomicity under concurrent writes (use `asyncio.gather` with 50 writers), `open_run_stream` replay correctness. Uses `tmp_path` for isolation.

**Acceptance Criteria:**
- [ ] 50 concurrent writes to the same run produce 50 valid JSON lines (no truncation, no interleave).
- [ ] File mode is `0600` after first write.
- [ ] Replay returns the written lines in order.

**Files to Modify/Create:**
- `tests/modules/ai/test_trace_jsonl.py` (extends T-035 basic coverage).

---

### T-053: Supervisor + reconciliation unit tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-037, T-038

**Description:**
Unit tests for `RunSupervisor` (spawn, wake, cancel, shutdown, exception handling) and `reconcile_step_from_event` (state-machine monotonicity, per-event-type transitions, out-of-order events ignored).

**Acceptance Criteria:**
- [ ] `wake` timing test: < 10 ms from `set` to awaiter observation.
- [ ] `cancel` timing test: < 50 ms from call to coro exit.
- [ ] Exception in supervised coro logged; supervisor still responsive afterwards.
- [ ] Reconciliation rejects rollback (completed → in_progress) — unit-tested with fixture events.

**Files to Modify/Create:**
- `tests/modules/ai/test_supervisor.py` (extends T-037).
- `tests/modules/ai/test_reconciliation.py` (extends T-038).

---

### T-054: Integration — end-to-end composition integrity (AC-1 + AC-6 headliner)

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-039, T-040, T-045, all building blocks

**Description:**
Use the `sample-linear.yaml` fixture + a scripted `StubLLMProvider` + `respx`-mocked engine that, for each dispatch, POSTs a synthetic webhook back to `/hooks/engine/events`. Start a run, wait for terminal, assert:
- `stop_reason=done_node`.
- Step sequence exactly matches the script.
- `PolicyCall` rows match the script.
- JSONL trace on disk contains the documented line-per-entity.
- `RunMemory.data` reflects the merged node results.

This test IS the AD-3 success metric.

**Acceptance Criteria:**
- [ ] Test completes deterministically within 5 s.
- [ ] On re-run with the same script, `Step.node_name` sequence is byte-identical.
- [ ] JSONL file reads back to the exact set of written events.
- [ ] Removing the script (empty provider) surfaces `ProviderError` → `stop_reason=error` (variant test).

**Files to Modify/Create:**
- `tests/integration/test_run_end_to_end.py`.
- `tests/fixtures/agents/sample-linear.yaml` (already created in T-031).

**Technical Notes:**
Respx fixture needs to POST back to the running ASGI app via its own `AsyncClient` — this is the moment where the webhook HMAC signer fixture earns its keep. Document the pattern so FEAT-003 can reuse it.

---

### T-055: Integration — cancel mid-flight

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-042, T-054

**Description:**
Start a multi-step run with a deliberately slow engine mock (delays 2 s per dispatch), wait until `status=running`, POST `/runs/{id}/cancel`, assert terminal within 500 ms, assert `stop_reason=cancelled`, assert any in-flight engine dispatch's late webhook is handled gracefully.

**Acceptance Criteria:**
- [ ] Timing assertion (<500 ms) holds.
- [ ] Late webhook (arrives post-cancel) is persisted with no step update.
- [ ] No leaked tasks at teardown.

**Files to Modify/Create:**
- `tests/integration/test_run_cancel.py`.

---

### T-056: Integration — engine failure → error stop (AC-8)

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-039, T-036

**Description:**
Respx returns 502 on the first dispatch. Assert run ends `stop_reason=error`, step ends `status=failed` with `error` JSONB populated (contains `engine_http_status`, `original_body`), JSONL trace records the failure.

**Files to Modify/Create:**
- `tests/integration/test_engine_failure.py`.

---

### T-057: Integration — webhook advancement timing (AC-7)

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-038, T-054

**Description:**
Measure `(webhook response wall-time) → (next PolicyCall.created_at)` delta on a 2-step run. Assert ≤100 ms on a warmed test DB.

**Acceptance Criteria:**
- [ ] Assertion holds in CI (may need a small warmup run first to avoid cold-DB variance).

**Files to Modify/Create:**
- `tests/integration/test_webhook_timing.py`.

---

### T-058: Integration — budget exhaustion stop

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-033, T-039

**Description:**
Start a run with `budget.maxSteps=2` against a scripted policy that would take 4 steps; assert `stop_reason=budget_exceeded` after exactly 2 steps.

**Files to Modify/Create:**
- `tests/integration/test_run_budget.py`.

---

### T-059: Integration — zombie-run reconciliation on restart (AC from §9 edge case)

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-045

**Description:**
Manually insert a `Run` row with `status=running` (no supervised task). Create the app (triggers lifespan startup). Assert the row is transitioned to `failed` + `stop_reason=error` and the trace contains the documented reason line.

**Files to Modify/Create:**
- `tests/integration/test_lifespan_zombie_reconciliation.py` (extends T-045).

---

### T-060: Integration — control-plane real-data tests (AC-5)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-041, T-043, T-044, T-054

**Description:**
For each read endpoint (`/runs`, `/runs/{id}`, `/runs/{id}/steps`, `/runs/{id}/policy-calls`, `/agents`): start a run, let it complete, query the endpoint with and without pagination/filters, assert envelope shape + `meta` correctness.

**Files to Modify/Create:**
- `tests/integration/test_control_plane_real.py`.

---

## Polish

### T-061: Documentation updates (data-model / api-spec / ARCHITECTURE + CLAUDE.md)

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-046, T-060

**Description:**
Per the CLAUDE.md doc-update table, add changelog entries to `docs/data-model.md` (new status transitions documented), `docs/api-spec.md` (201-Accepted for `POST /runs`, real response shapes), and `docs/ARCHITECTURE.md` (supervisor, JSONL trace store). Add a new "Supervised tasks" subsection to CLAUDE.md → Patterns. Update `docs/ui-specification.md` for the real `orchestrator run --wait` exit code table.

**Acceptance Criteria:**
- [ ] Every file has a changelog entry dated 2026-04-17 referencing FEAT-002.
- [ ] No doc contradicts the shipped behavior.
- [ ] `CLAUDE.md`'s "Anti-Patterns to Avoid" lists "Don't share an AsyncSession between a request handler and a run-loop task."

**Files to Modify/Create:**
- `docs/data-model.md`, `docs/api-spec.md`, `docs/ARCHITECTURE.md`, `docs/ui-specification.md`, `CLAUDE.md`.

---

### T-062: README extension — first real run walkthrough

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-046, T-054

**Description:**
Extend `README.md` with a 10-line "First Run" snippet: drop `sample-linear.yaml` in `AGENTS_DIR`, `orchestrator run sample-linear@1.0 --intake brief=hi --wait`, then `orchestrator runs show <id>` and `orchestrator runs policy <id>`. Link to the fixture path. Stay under the 150-line README cap.

**Acceptance Criteria:**
- [ ] Commands, run verbatim on a fresh clone after `alembic upgrade head`, produce a completed run.
- [ ] README still ≤150 lines.

**Files to Modify/Create:**
- `README.md`.

---

## Summary

**Total tasks:** 32 (T-031 through T-062)

**By type:**
| Type | Count |
|------|-------|
| Backend | 16 |
| Testing | 13 |
| Documentation | 2 |
| Database | 0 |
| DevOps | 0 |
| Frontend | 0 |

FEAT-001 already shipped the data + DevOps layers. FEAT-002 is entirely runtime logic + tests.

**By complexity:**
| Complexity | Count |
|------------|-------|
| S | 15 |
| M | 13 |
| L | 4 |
| XL | 0 |

**Critical path (longest dependency chain):**
`T-031 → T-032 → T-039 → T-040 → T-045 → T-054 → T-062` (7 tasks — schema → loader → runtime → start_run → lifespan → end-to-end test → README proof).

**Suggested parallelization after T-031:**
- Track A — agent data: `T-032 → T-044 → T-048`.
- Track B — runtime infra: `T-033, T-034, T-035, T-036, T-037, T-038` (all independent of one another after T-031) → `T-039`.
- Track C — service layer: `T-040 → T-041, T-042, T-043` (parallel after T-040).
- Track D — integration: `T-045, T-046, T-047` (after their respective deps).
- Track E — testing: unit tests track their implementation tasks; integration tests (T-054..T-060) wait for the runtime loop.

**Risks / Open Questions:**

1. **Flow-engine API shape for `POST /nodes/dispatch`** is assumed, not specified here. If the real engine's contract differs, T-036 adjusts; document the actual request/response in the docstring when it ships. Mitigation: a respx-based integration contract test (added under `tests/contract/` with `@pytest.mark.live`) can pin it once the engine stabilizes.

2. **In-process supervisor vs dedicated worker.** v1 keeps the loop inside FastAPI. If run durations grow beyond ~minutes, a forked uvicorn worker holding long-running tasks stops being safe (`--workers > 1` duplicates the supervisor). Mitigation: document the single-worker constraint in `docs/ARCHITECTURE.md` (T-061); FEAT-005 or later opens an IMP for a dedicated worker if and when it matters.

3. **JSONL trace file handle lifetime.** Opening/closing per write is simple but slow; keeping a handle open per active run costs FDs. T-035 chooses per-write open as the default — correct and leak-free, slow enough only to matter in FEAT-005's load test. Revisit then.

4. **Zombie-run reconciliation race.** If two app instances start simultaneously (uvicorn reload), both could try to transition the same `running` row. T-045's simple `UPDATE WHERE status='running'` is racy but idempotent; add a `SELECT FOR UPDATE SKIP LOCKED` if we ever see double-failure rows in practice. Flagged here, not solved.

5. **Tool-argument validation.** T-039 terminates the run on invalid tool arguments per CLAUDE.md's "fail fast on policy errors" rule. If that proves too strict for real LLM outputs in FEAT-003, the recovery surface (one retry with a correction prompt) lives in FEAT-003's scope — not FEAT-002.

6. **`asyncio.Event` wake-up loss.** If a webhook arrives *after* the loop sets the event but *before* the loop reads it, a naive `Event` can be missed. T-037 spec requires the Event be explicitly `clear()`ed after observation, or use a `Queue` of `(step_id, event_type)` for correctness. Call out the pattern in the supervisor's docstring.

7. **Test flake from timing assertions (AC-7, AC-4).** 100 ms and 500 ms thresholds are easy to fail on a loaded CI box. Mitigation: use `pytest-timeout` for safety, but assert against generous bounds in CI (`≤500 ms` for AC-7, `≤2 s` for AC-4) and document the stricter local-machine targets in the test docstring.
