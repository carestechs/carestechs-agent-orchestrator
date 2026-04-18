# Feature Brief: FEAT-002 — Agent Runtime Loop (Stub-Policy End-to-End)

> **Purpose**: Turn the FEAT-001 skeleton into a running agent loop — engine dispatch, webhook reconciliation, per-run memory, JSONL traces, stop conditions, and the control-plane service implementations — all provably working with the deterministic stub policy. Real LLM (FEAT-003) and lifecycle agent (FEAT-004) plug in on top of this.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-002 |
| **Name** | Agent Runtime Loop (Stub-Policy End-to-End) |
| **Target Version** | v0.2.0 |
| **Status** | Not Started |
| **Priority** | Critical |
| **Requested By** | Tech Lead (`ai@techer.com.br`) |
| **Date Created** | 2026-04-17 |

---

## 2. User Story

**As a** solo tech lead driving feature delivery (see `docs/personas/primary-user.md`), **I want to** run `orchestrator run <agent> --intake ...` against a YAML-defined agent and watch it iterate through engine-dispatched nodes driven by a deterministic stub policy — with every step, policy call, and webhook persisted and traceable — **so that** the architecture's composition-integrity claim (AD-3) stops being a promise and becomes a test I can run, and every later feature (real LLM, lifecycle agent, trace streaming) plugs into a proven runtime.

---

## 3. Goal

A run started via `POST /api/v1/runs` or `orchestrator run` returns `202` immediately, then — in the background — loads a YAML agent definition, calls the configured policy through the tool-calling seam, dispatches the chosen node to `carestechs-flow-engine` over HTTP, reconciles the returned webhook into a step update, advances the loop, and terminates under one of the five stop conditions with a final state persisted and a complete JSONL trace on disk. All FEAT-001 stub service functions (except `stream_trace`, reserved for FEAT-004) are real. The `StubLLMProvider`, driven by a scripted tool-call sequence, drives an end-to-end run without any LLM SDK in the process.

---

## 4. Feature Scope

### 4.1 Included

- **Agent definition loader** (`src/app/modules/ai/agents.py`): reads YAML files from `AGENTS_DIR`, validates against a Pydantic `AgentDefinition` schema (ref, version, flow topology, intake schema, available nodes, terminal/"done" node names, optional budget defaults), and computes `agent_definition_hash` (sha256 of canonical YAML). Exposes `load_agent(ref)` and `list_agents()`.
- **Flow-engine `dispatch_node` full implementation** in `engine_client.py`: POSTs `{agent_ref, run_id, node_name, node_inputs, callback_url}` to `{engine_base_url}/nodes/dispatch`, returns the engine's `engine_run_id`, wraps all httpx errors in `EngineError`. Webhook-based completion — no polling.
- **Runtime loop** (`src/app/modules/ai/runtime.py` replaces the FEAT-001 stub): async `run_loop(run_id, db, policy, engine, trace_store)` that (a) loads agent + intake, (b) initializes `RunMemory`, (c) for each iteration: builds the tool list from the agent's available nodes, calls `policy.chat_with_tools`, persists a `PolicyCall`, creates a `Step` with `status=pending`, dispatches to the engine, transitions the step to `dispatched`/`in_progress`/`completed`/`failed` as webhooks arrive, updates `RunMemory` from node results, and evaluates stop conditions. Terminates by writing `stop_reason`, `final_state`, and `ended_at` on the `Run`.
- **Webhook-driven reconciliation** (`src/app/modules/ai/reconciliation.py` or extended service): `ingest_engine_event` updates the matching `Step` (status, `node_result`, `error`, `completed_at`) atomically with event persistence, and signals the waiting run-loop coroutine (via an `asyncio.Event` registry keyed by `run_id`) so the loop wakes and proceeds.
- **Non-blocking `start_run`**: writes the `Run` row with `status=pending`, returns the summary DTO, and launches the loop via `asyncio.create_task` inside the FastAPI lifespan's task supervisor (task supervisor lives in `app.main` and tracks in-flight loops for graceful shutdown + cancel).
- **Stop-condition module** (`src/app/modules/ai/stop_conditions.py`): pure functions returning `StopReason | None` for each rule — `done_node` (agent terminal reached), `budget_exceeded` (max-steps or max-tokens hit), `policy_terminated` (policy selected a dedicated "terminate" tool), `error` (engine or policy error propagated), `cancelled` (run row flipped to `cancelled`).
- **Cancel** (`cancel_run`): sets `status=cancelled`, `stop_reason=cancelled`, `ended_at=now`; the supervisor cancels the task; any in-flight dispatch is left to complete on the engine side but its late webhook is persisted with `signature_ok` respected and the step update is a no-op once the run is terminal.
- **Control-plane service implementations** for `start_run`, `list_runs` (filters: `status`, `agentRef`; pagination), `get_run` (with last-step summary), `cancel_run`, `list_steps` (pagination), `list_policy_calls` (pagination), and `list_agents` (reads `AGENTS_DIR`). `stream_trace` remains `NotImplementedYet` — owned by FEAT-004.
- **JSONL trace store** (`src/app/modules/ai/trace_jsonl.py`, implementing the FEAT-001 `TraceStore` Protocol, AD-5 v1): append-only `.trace/<run_id>.jsonl`; one line per `Step`, `PolicyCall`, `WebhookEvent`. `trace_uri = "file://.trace/<run_id>.jsonl"` written on `Run` at start.
- **Tool definition builder** (`src/app/modules/ai/tools/__init__.py`): converts each agent node into a `ToolDefinition` (name = node name, description from YAML, parameters = node input schema). Also emits a built-in `terminate` tool for `policy_terminated` stop.
- **Logging**: every loop iteration logs with `run_id` + `step_id` bound; all engine and policy errors caught at the boundary and recorded both in the trace and as dedicated `Step.error` / `Run.final_state` entries per CLAUDE.md's "never swallow silently" rule.
- **CLI wiring**: `orchestrator run <agent> --intake k=v --intake-file ... [--budget-steps N] [--budget-tokens N] [--wait]` calls `POST /api/v1/runs` and optionally polls `/runs/{id}` until terminal (`--wait`). `runs ls/show/cancel/steps/policy` and `agents ls/show` become real. `runs trace` stays stubbed (FEAT-004).

### 4.2 Excluded

- **Real LLM provider.** No Anthropic/OpenAI SDK code path. `llm_provider=anthropic` continues to be a wiring placeholder only. FEAT-003.
- **Trace streaming** — `GET /api/v1/runs/{id}/trace` NDJSON stream and `orchestrator runs trace --follow`. FEAT-004.
- **Concrete ia-framework lifecycle agent** (task-gen, plan-gen, implementation, review, corrections, closure nodes). FEAT-004 / FEAT-005.
- **Multi-agent coordination, hot-reload of agent definitions, vector memory, cross-run memory.** Out per stakeholder scope lock (AD-4 per-run memory only).
- **Distributed/background workers** (Celery, Arq, RQ). `asyncio.create_task` inside the FastAPI process is enough for v1. A dedicated worker ships only if and when the FastAPI process proves an inadequate home.
- **Retries, circuit breakers, or adaptive back-off** on engine dispatch failures. Engine errors are terminal for the step in v1 and bubble up to run-level `error` stop.
- **Structured tool-argument validation errors as run-continuations.** If the stub policy emits invalid tool arguments, the run terminates with `stop_reason=error` (T-013's fail-fast policy rule from CLAUDE.md).
- **Any change to `carestechs-flow-engine`.** AD-1. Composition over extension.

---

## 5. Acceptance Criteria

- **AC-1**: A test agent defined as YAML in `tests/fixtures/agents/sample-linear.yaml` with 3 nodes, scripted via `StubLLMProvider` to pick them in order and then `terminate`, completes a run end-to-end against a mocked engine (`respx`) that returns synthetic webhooks via the real `POST /hooks/engine/events` path — and the test passes deterministically with no real LLM and no real engine.
- **AC-2**: `POST /api/v1/runs` with a valid `agentRef` and intake returns `202` within 50 ms (dispatch is background) and the `Run` row exists with `status=pending` immediately after the response.
- **AC-3**: Every terminated run has a non-null `stop_reason` in {`done_node`, `budget_exceeded`, `policy_terminated`, `error`, `cancelled`}, a `final_state` JSONB blob, an `ended_at` timestamp, and a `.trace/<run_id>.jsonl` file containing one line per `Step` + `PolicyCall` + `WebhookEvent` ingested — verified by an integration test that reads the JSONL back.
- **AC-4**: `POST /api/v1/runs/{id}/cancel` on an in-flight run transitions the run to `cancelled` within 500 ms and the supervised loop task is cancelled — asserted by a test that starts a multi-step run, cancels mid-flight, and checks terminal state.
- **AC-5**: `GET /api/v1/runs`, `/runs/{id}`, `/runs/{id}/steps`, `/runs/{id}/policy-calls`, and `/agents` all return real data with the `api-spec.md` envelope shapes (pagination meta included on collection responses) — covered by parameterized integration tests.
- **AC-6**: The AD-3 composition-integrity test is no longer a placeholder: with `StubLLMProvider` scripted deterministically, a canonical test agent completes with `stop_reason=done_node` and the exact same sequence of `Step.node_name`s on every run.
- **AC-7**: Webhook arrival with a valid signature advances a paused run-loop coroutine within 100 ms (measured from the `POST /hooks/engine/events` response to the next `PolicyCall` row). Measured by a timing-assert integration test.
- **AC-8**: Engine dispatch failure (mocked `respx` 502) terminates the owning step with `status=failed` + `error` JSONB populated, and the run with `stop_reason=error`, without crashing the process or leaking the raw `httpx` exception.
- **AC-9**: Dead-code rule: `stream_trace` still raises `NotImplementedYet`; the endpoint still returns `501`. No placeholder "coming soon" stubs or half-wired code paths.
- **AC-10**: `uv run pyright` and `uv run ruff check .` stay clean; the full `uv run pytest` suite stays green on Postgres. No test skipped except the `live`-marked suite.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|-----------------|--------------------|
| `Run` | Now actually created and mutated by `start_run` / runtime / `cancel_run`. | `status` transitions: `pending → running → (completed | failed | cancelled)`. `stop_reason` set exactly once on terminal transition. `final_state` append-only once written. `trace_uri` set at creation. |
| `Step` | Created per loop iteration; updated on webhook arrival. | UNIQUE `(run_id, step_number)` enforced. `status` transitions: `pending → dispatched → in_progress → (completed | failed)`. `engine_run_id` set at dispatch. Terminal fields append-only per AD's append-only rule. |
| `PolicyCall` | Created once per policy invocation before the dispatch. | UNIQUE `step_id`. `selected_tool ∈ available_tools` enforced in service layer; violation → `PolicyError` → run terminates. `tool_arguments` validated against the tool's JSON schema; violation also `PolicyError`. |
| `WebhookEvent` | Persisted by FEAT-001 already; now consumed by the runtime. | FEAT-001 persistence contract unchanged. Runtime reads the latest event for a `step_id` to drive the step transition; idempotent (dedupe_key) per FEAT-001. |
| `RunMemory` | Created at run start, updated per step from node results. | One row per run (`run_id` PK). `data` JSONB deep-merged with node result digests via service-layer helper; no cross-run reads (AD-4). |

**New entities required:** None. `AgentDefinition` is a Pydantic model backed by YAML files on disk — **not** a DB entity.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs` | POST | **Now real** | Returns 202 + run summary; dispatches loop in background |
| `/api/v1/runs` | GET | **Now real** | Filters `status`, `agentRef`; pagination meta |
| `/api/v1/runs/{id}` | GET | **Now real** | Includes `last_step` summary |
| `/api/v1/runs/{id}/cancel` | POST | **Now real** | Transitions run + cancels supervised task |
| `/api/v1/runs/{id}/steps` | GET | **Now real** | Pagination |
| `/api/v1/runs/{id}/policy-calls` | GET | **Now real** | Pagination |
| `/api/v1/runs/{id}/trace` | GET | Still 501 | Deferred to FEAT-004 |
| `/api/v1/agents` | GET | **Now real** | Reads `AGENTS_DIR` YAML |
| `/hooks/engine/events` | POST | Extended | Same contract; now triggers run-loop advancement |

**New endpoints required:** None. All endpoints already declared in `docs/api-spec.md` as of FEAT-001.

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| CLI (`orchestrator`) | Extended | `run`, `runs ls/show/cancel/steps/policy`, `agents ls/show` become real. `runs trace` stays stubbed. |

**New screens required:** None (v1 is CLI-only per stakeholder scope).

---

## 9. Edge Cases

- **Multiple webhooks for the same step** (e.g., `node_started` then `node_finished`) MUST each update the step idempotently in event order; out-of-order arrivals within a small window (seconds) MUST reconcile correctly by respecting the state-machine monotonicity (a later `started` does not roll back from `completed`).
- **Webhook arrives for a `Run` that has already terminated** (late event after cancel/budget): persist the event, update the `Step` if still non-terminal (for forensics), but do NOT restart the loop. Log a `WARNING`.
- **Engine dispatch returns 2xx but no webhook ever arrives** (engine drops the request): budget-based stop MUST still eventually fire (`max_steps` reached via dispatched-but-not-completed counting, or an explicit per-step dispatch timeout — v1 uses per-step timeout from agent YAML, default 5 min, terminating the step as `failed` and the run as `error`).
- **Policy returns zero or >1 tool calls, or a tool not in the available list**: service raises `PolicyError` → `stop_reason=error`, run terminates, no fabricated "best guess" per CLAUDE.md error handling.
- **Stub policy script exhausted before agent terminates**: `ProviderError` → `stop_reason=error`. Tests MUST cover this path.
- **Two concurrent runs of the same agent**: each gets its own `RunMemory`, its own JSONL file, its own supervisor task entry. No shared state. Per-run isolation is load-bearing for AD-4.
- **FastAPI process restart mid-run**: in-flight runs are lost (their supervisor tasks are gone). On restart, any `Run` with `status=running` is marked `failed` + `stop_reason=error` + a terminal trace entry noting the reason — prevents zombie rows. An integration test simulates this by force-creating a `running` row, starting the app, and asserting reconciliation.
- **Agent YAML missing required fields or referencing nodes not in the flow**: `load_agent` raises `ValidationError` → `start_run` returns `400` Problem Details before writing the `Run` row.
- **`AGENTS_DIR` path does not exist**: `list_agents` returns an empty list (not an error). `load_agent(ref)` raises `NotFoundError` → 404.

---

## 10. Constraints

- MUST NOT introduce a real LLM SDK import in runtime/service code paths. `core/llm.py`'s abstraction stays the seam; FEAT-003 swaps providers.
- MUST NOT call the flow engine from policy code (CLAUDE.md anti-pattern). Only the runtime loop dispatches.
- MUST NOT add cross-run state (AD-4). Any caching survives only within a run's lifetime.
- MUST respect the thin-adapter rule (AC-9 from FEAT-001): `router.py` and `cli.py` gain NO business logic. Every new behavior lands in the service layer or a new `modules/ai/*` module.
- MUST NOT modify `carestechs-flow-engine` to simplify agent behavior (AD-1).
- MUST ship the JSONL `TraceStore` (AD-5 v1); Postgres trace store is a later feature's concern.
- MUST keep run-start non-blocking (AD-2): no synchronous wait in `POST /api/v1/runs`.
- MUST update `docs/data-model.md`, `docs/api-spec.md`, and `docs/ARCHITECTURE.md` in the same PR when runtime behavior changes their contracts (doc-first rule from CLAUDE.md).
- The work is stacked PRs in strict dependency order. Each PR leaves `doctor`, `pyright`, `ruff`, and the test suite green. Partial merges that leave the loop half-wired are blocked.

---

## 11. Motivation and Priority Justification

**Motivation:** The stakeholder's third success criterion — "removing the LLM degrades to a deterministic flow that still runs" — is today only a placeholder test. FEAT-002 makes it real. Without this feature the architecture's core claim is unverified and every later feature (real LLM, lifecycle agent, self-hosted delivery) rests on an untested seam. FEAT-002 also satisfies the observability metric ("policy call inputs, outputs, and selected next-node inspectable") because JSONL traces and the `PolicyCall` table become first-class after this feature.

**Impact if delayed:** FEAT-003 (Anthropic provider) and FEAT-004 (lifecycle agent + self-hosted delivery) are both blocked. The `run_loop` stub in `runtime.py` and the seven `NotImplementedYet` service functions remain — every demo continues to be caveat-heavy ("it *will* do X once FEAT-002 ships").

**Dependencies on this feature:** FEAT-003, FEAT-004, FEAT-005 (self-hosted proof), any later observability/UI work. The thin-adapter rule is enforced structurally so additional features plug in without reopening this one.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` |
| **Stakeholder Scope Item** | "Agent primitive", "policy interface", "Subflow-as-node integration", "stop-condition model", "Observability hooks", "serialization schema" — all six are exercised by this feature. The seventh ("end-to-end lifecycle agent") is deliberately deferred to FEAT-004 so FEAT-002 can ship independently. |
| **Success Metric** | Most directly: "Composition integrity" (proven by AC-1 + AC-6) and "Policy traceability" (proven by AC-3). Indirectly unblocks "Self-hosted feature delivery" and "Time-to-task-list from brief" (both require a functioning loop). |
| **Related Work Items** | Predecessor: FEAT-001 (skeleton — complete). Successors: FEAT-003 (Anthropic provider), FEAT-004 (lifecycle agent + concrete nodes), FEAT-005 (self-hosted feature delivery). |
