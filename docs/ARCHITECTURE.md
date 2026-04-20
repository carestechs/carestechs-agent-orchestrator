# Architecture

## System Summary

`carestechs-agent-orchestrator` is a headless Python service that turns the ia-framework's feature lifecycle into an **agent-driven loop** on top of `carestechs-flow-engine`. It owns the loop end-to-end: it reads an agent definition (flow + policy + memory), drives the flow engine over HTTP, receives progress events back via webhooks, consults an LLM policy to decide what step to run next, and persists a trace of every decision. The flow engine stays agent-agnostic; agent semantics live cleanly in this layer.

**Stack profile:** `python-ai-agent-service-docker-compose` (see the shared architecture repo). See that profile for every ADR this project inherits — they are not repeated here. This document covers only decisions that are specific to this orchestrator.

---

## Project-Specific Architectural Decisions

### AD-1 — Orchestrator drives; the flow engine is passive

| | |
|---|---|
| **Decision** | The orchestrator is the sole driver of every run. It calls `carestechs-flow-engine` over HTTP to execute individual nodes/subflows and receives engine-side events through webhook callbacks. The engine never makes control-flow decisions on the orchestrator's behalf; its role is to execute deterministic steps on request. |
| **Rationale** | Keeps the agent loop (policy, memory, decision logging) entirely inside this repo, so the engine's core stays stable and agent-agnostic — satisfying the "composition over extension" principle from the stakeholder definition. Also makes the "remove the LLM → still a valid pipeline" invariant enforceable: the control loop is something we own and can degrade. |
| **Constraints** | The orchestrator MUST NOT depend on engine-side branching logic for agent decisions. Any engine-internal automation or hard rule (the documented exceptions) MUST be treated as opaque side-effects that the orchestrator reconciles via webhook events — never as a policy substitute. |

### AD-2 — HTTP client + webhook receiver transport

| | |
|---|---|
| **Decision** | The orchestrator integrates with the flow engine through two HTTP surfaces: (a) an outbound async HTTP client that invokes engine endpoints to start/advance flows and nodes, and (b) an inbound FastAPI webhook endpoint that the engine posts events to (node started, node finished, node failed, flow terminated). The policy loop is advanced by webhook events, not by polling. |
| **Rationale** | Matches the engine's shape (HTTP API with webhook event hooks) without requiring either side to hold long connections. Webhooks give us real-time progress without polling overhead and compose naturally with FastAPI. Polling was rejected as a latency and cost regression; long-lived bidi streams were rejected as premature for v1. |
| **Constraints** | Webhook requests MUST be authenticated via a shared secret / HMAC signature. Webhook handlers MUST be idempotent — the engine may retry. The orchestrator MUST NOT block on the outbound HTTP call past a configurable timeout; long-running engine work is expected to complete via webhook, not synchronous response. Every inbound event MUST be recorded in the run trace before any policy action is taken on it. |

### AD-3 — An Agent is `Flow + Policy + Memory`

| | |
|---|---|
| **Decision** | The Agent primitive is a bundle of three parts: a **flow** (reference to a deterministic flow in the engine, or a composition of nodes), a **policy** (an LLM-backed decision function with the contract `(state, available_nodes) → next_node`, implemented via provider-native tool calling per `adrs/ai/policy-via-tool-calling.md`), and a **memory** (scoped state accessible to the policy across steps of a single run). Removing the policy MUST degrade the agent to a deterministic pipeline that still runs. |
| **Rationale** | This is the composition boundary the whole project hinges on (Philosophy #2 in the stakeholder definition). Encoding it as three explicit slots — rather than, say, a subclassable Agent class — keeps the contract small, testable, and expressible as data (YAML/JSON) later. |
| **Constraints** | Policy code MUST NOT call the flow engine directly; only the runtime loop does. Memory MUST NOT be a hidden global — it is passed into the policy as part of the state and written back explicitly. The runtime MUST be able to execute an agent with a stub "pick first available node" policy and have it complete successfully — this is the composition-integrity test. |

### AD-4 — Per-run memory scope only (v1)

| | |
|---|---|
| **Decision** | In v1, the Agent's memory is scoped to a single run. A new run starts with an empty memory; memory is discarded (beyond the immutable trace) when the run terminates. Per-agent-definition memory that persists across runs is a v2 concern. |
| **Rationale** | Matches the scope lock: v1 proves the composition boundary and delivers a single feature end-to-end. Cross-run memory introduces retention policy, conflict resolution, and privacy questions that would distract from the v1 goal. |
| **Constraints** | The memory interface MUST be designed so that swapping in a longer-lived store later is a substitution, not a rewrite — i.e. the policy contract never assumes "fresh at start of run." NEVER leak memory between concurrent runs. |

### AD-5 — Durable run state, JSONL-first then database

| | |
|---|---|
| **Decision** | Runs, steps, policy decisions, and webhook events are persisted to **append-only JSONL files per run** in v1, and migrated to a **relational database** (PostgreSQL via SQLAlchemy async, as the profile dictates) as soon as it is practical — likely within the first post-v1 iteration. No in-memory-only mode is supported. |
| **Rationale** | JSONL gives us zero-setup durability, crash-resumability, and human-readable traces immediately — matching the "Observability is non-negotiable" principle without requiring a database schema on day one. Moving to Postgres soon gives us queryable history, multi-run aggregates, and fits the profile's conventions. Starting with Postgres was considered but rejected for v1 to keep the first end-to-end run trivial to stand up. |
| **Constraints** | The trace layer MUST be abstracted behind a protocol so that swapping JSONL → Postgres is a composition-root change only, not a service-code change. Trace writes MUST be append-only and MUST include the event kind, timestamp, run id, step id, and full input/output for policy calls. NEVER overwrite or truncate run history. |

### AD-6 — Eat our own dog food

| | |
|---|---|
| **Decision** | The orchestrator's first real consumer is its own delivery loop. Before being offered to external consumers, the orchestrator MUST ship at least one of its own features end-to-end — from feature brief in `docs/work-items/` through task generation, planning, implementation, review, and closure — using itself. |
| **Rationale** | Direct restatement of Product Philosophy #5 in the stakeholder definition, hoisted here because it has direct architectural consequences: the orchestrator's file-system outputs (task lists, plans, work-item status updates) MUST be producible by the orchestrator itself, which means the agent's tool surface includes "write to this repo's docs/" actions. |
| **Constraints** | The orchestrator MUST be runnable against its own working directory with no extra privileges. Any capability the human uses manually to drive the framework (read a brief, write a task list, update a work-item status) MUST be expressible as a tool in the agent's action space. |

---

## Technology Stack

All entries come from the `python-ai-agent-service-docker-compose` profile. Project-specific notes in parentheses.

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Runtime** | Python 3.12+ | Chosen for LLM SDK maturity and YAML/data-as-code ergonomics. |
| **Web framework** | FastAPI + Uvicorn | Exposes webhook receivers and (optionally) a control-plane API. Async-native. |
| **CLI** | Typer or Click | Primary user entry point in v1 (`run-agent`, `inspect-run`, etc.). |
| **Data** | PostgreSQL 16+ (post-JSONL) | Run/step/trace storage once AD-5 migration lands. No pgvector in v1 — no RAG in scope. |
| **ORM / migrations** | SQLAlchemy 2.0 async + Alembic | Per the profile. |
| **LLM access** | Provider-agnostic abstraction (per `adrs/ai/llm-abstraction-python.md`) | Anthropic is the initial concrete provider. Policy calls MUST use native tool calling. |
| **HTTP client** | `httpx` (async) | Outbound calls to `carestechs-flow-engine`. |
| **Config** | `pydantic-settings` | Env-var driven, validated at startup. |
| **Packaging** | Docker multi-stage + Docker Compose | Per the profile. |
| **Frontend** | **None** | Out of scope per stakeholder definition (no UI for authoring agents in v1). |

---

## Component Architecture

```
                    ┌─────────────────────┐
                    │   Human / CI / cron │
                    └──────────┬──────────┘
                               │ CLI invocation or HTTP POST
                               ▼
 ┌─────────────────────────────────────────────────────────┐
 │                   Orchestrator service                  │
 │                                                         │
 │   CLI / HTTP entry ──▶ Agent runtime (the loop)         │
 │                           │                             │
 │          ┌────────────────┼────────────────────────┐    │
 │          ▼                ▼                        ▼    │
 │      Policy          Memory (per-run)        Trace store│
 │  (LLM, tool-        (passed in/out of        (JSONL →   │
 │   calling via       the policy each step)     Postgres) │
 │   provider-                                             │
 │   agnostic abs.)                                        │
 │          │                                              │
 │          │ "pick next node" decisions                   │
 │          ▼                                              │
 │   Flow-engine client (httpx) ──▶ carestechs-flow-engine │
 │                                                         │
 │   Webhook receiver (FastAPI) ◀── carestechs-flow-engine │
 │          │                                              │
 │          └── events feed back into the runtime loop     │
 │                                                         │
 └─────────────────────────────────────────────────────────┘
                               │
                               ▼
                     LLM provider (Anthropic, etc.)
```

### Component Descriptions

**CLI / HTTP entry adapters**
- **Purpose:** Accept intake (a feature brief path, a run id, a `--resume` flag) and translate it into a service call.
- **Responsibilities:** Argument parsing, authN for HTTP routes, delegation to the agent runtime. No business logic.
- **Key Dependencies:** Agent runtime service.

**Agent runtime (the loop)**
- **Purpose:** Owns the `decide → execute → observe → repeat` loop for a single run.
- **Responsibilities:** Load agent definition; initialize state and memory; call the policy to pick a next step; invoke the flow-engine client to execute the step; receive webhook events; update state; emit traces; evaluate stop conditions.
- **Key Dependencies:** Policy, memory, flow-engine client, trace store.

**Policy**
- **Purpose:** LLM-backed decision function with contract `(state, available_nodes) → next_node`.
- **Responsibilities:** Assemble the decision prompt; expose available nodes as tools; call the LLM via the provider-agnostic abstraction; validate that exactly one tool call was returned; emit the decision to the trace.
- **Key Dependencies:** LLM abstraction, tool definitions, trace store.

**Memory (per-run)**
- **Purpose:** Scoped state the policy can read from and write to across steps of a single run.
- **Responsibilities:** Store and surface accumulated context (e.g., "current task under review", "last review feedback") to the policy. Discarded when the run terminates.
- **Key Dependencies:** None beyond the runtime.

**Flow-engine client**
- **Purpose:** Outbound HTTP client for `carestechs-flow-engine`.
- **Responsibilities:** Start/advance flows and nodes; translate orchestrator-side step identifiers to engine-side node invocations; surface engine errors as typed runtime errors.
- **Key Dependencies:** `httpx`, engine base URL + auth from config.

**Webhook receiver**
- **Purpose:** Inbound HTTP endpoint that the engine posts events to.
- **Responsibilities:** Authenticate (shared secret / HMAC), validate with Pydantic, record the event in the trace, and deliver it to the runtime loop for the correct run id. Idempotent — the engine may retry.
- **Key Dependencies:** FastAPI router, trace store, runtime event dispatcher.

**Trace store**
- **Purpose:** Append-only, inspectable record of every run.
- **Responsibilities:** Persist runs, steps, policy calls (inputs/outputs), webhook events, and timings. JSONL-per-run is the live v1 implementation (`JsonlTraceStore`, one `asyncio.Lock` per run, file mode `0600`, typed DTO replay via a `kind` discriminator); Postgres v2 remains a future migration (AD-5).
- **Key Dependencies:** Filesystem in v1, SQLAlchemy/Postgres in v2.

### Runtime Loop Components (FEAT-002)

- **`runtime.run_loop`** — the AD-3 seam. One keyword-only entry point that takes `run_id, agent, policy, engine, trace, supervisor, session_factory, cancel_event`. Each iteration opens its own `AsyncSession` from the factory so a crash in iteration *N* never corrupts iteration *N+1*'s transaction. Every terminal path routes through `_terminate` for a single commit of `status + stop_reason + final_state + ended_at`.
- **`RunSupervisor`** — in-process registry of `asyncio.Task` + wake-up `asyncio.Event` per active run. Webhooks call `supervisor.wake(run_id)`; the loop awaits `supervisor.await_wake(run_id)` between dispatches. `cancel(run_id)` cancels the task; `shutdown(grace)` drains all tasks. **Single-worker constraint**: the supervisor is process-local, so running uvicorn with `--workers > 1` is unsupported in v1 (documented in `CLAUDE.md`).
- **`JsonlTraceStore`** — AD-5 v1 trace store; interchangeable with a future `PostgresTraceStore` via the `TraceStore` protocol.
- **`reconciliation.next_step_state`** — pure monotonic state-machine helper: `(current_status, event_type) → (new_status, changed)`. A webhook that would roll a step backward returns `changed=False` and the caller skips the update.
- **Zombie reconciliation** — lifespan startup hook (`app.lifespan.reconcile_zombie_runs`) flips any `running` rows left over from a prior process to `failed/error` with `final_state.zombie_reason = "process restart"`. Idempotent.
- **`AnthropicLLMProvider`** (FEAT-003) — real LLM policy behind the `LLMProvider` protocol. Translates `ToolDefinition` → Anthropic tool schema, parses `tool_use` blocks back into `ToolCall`s, maps SDK errors to `ProviderError` / `PolicyError`, retries transient failures (5xx / 429 / connection / timeout) with capped exponential backoff + jitter. API key is scoped to the SDK's auth header; `raw_response` is a whitelisted subset so no headers or metadata leak into traces.
- **Trace streaming** (FEAT-004) — `GET /api/v1/runs/{id}/trace` returns the run's JSONL trace as `application/x-ndjson`, optionally tailing live writes (`?follow=true`) and filtering by `?kind=` / `?since=`. The reader lives in `JsonlTraceStore.tail_run_stream` (opens read-only, polls every 200 ms for new lines, never contends on the writer's lock); the service wraps the iterator with terminal-state close detection via a background reader task + queue so cancellation never corrupts the aiofiles handle.
- **Lifecycle agent** (FEAT-005) — first concrete agent at `agents/lifecycle-agent@0.1.0.yaml`. Drives the ia-framework's 8-stage loop (intake → closure). Every stage maps to a **local tool** registered in `modules/ai/tools/lifecycle/registry.py`; the runtime executes handlers in-process against a typed `LifecycleMemory` instead of dispatching to the flow engine. `engine_run_id` on a lifecycle step is `NULL` by design.
- **Pause / resume contract** (FEAT-005) — the `wait_for_implementation` tool returns a `PauseForSignal` sentinel instead of a fresh memory. The runtime persists the step as `in_progress` with `engine_run_id=NULL`, then awaits `RunSupervisor.await_signal(run_id, name, task_id)`. `POST /api/v1/runs/{id}/signals` persists a `RunSignal` row and calls `supervisor.deliver_signal(...)` — same persist-first ordering as the webhook pipeline. Signal channels are keyed on `(run_id, name, task_id)` with preload semantics; purged on run termination.
- **Correction-attempt bound** (FEAT-005) — `stop_conditions.correction_budget_exceeded` reads `memory.correction_attempts` against `Settings.lifecycle_max_corrections` (default `2`). Exceeding the bound fires `StopReason.ERROR` with `final_state.reason="correction_budget_exceeded"`, `final_state.task_id`, `final_state.attempts`. The bound sits inside the existing `error` priority bucket.

---

## Data Flow

1. A user (or cron, or CI) invokes the CLI with a feature brief path, or POSTs to the run-intake endpoint.
2. The entry adapter calls the agent runtime, passing the agent definition and the intake payload.
3. The runtime initializes a new run (id, empty memory, fresh trace) and enters the loop.
4. On each iteration: the runtime calls the policy with current state and the list of currently-available next-nodes (exposed as tools).
5. The policy returns a tool call naming the chosen next node plus its arguments. The runtime records the decision in the trace.
6. The runtime calls the flow-engine client, which posts to `carestechs-flow-engine` to execute the chosen node.
7. The engine executes the node asynchronously. When it finishes (or errors), it POSTs a webhook event back to the orchestrator.
8. The webhook receiver authenticates, validates, records the event, and hands control back to the runtime loop for that run.
9. The runtime updates state + memory with the event's outcome and evaluates stop conditions (budget, explicit "done" node, policy-driven termination).
10. If not stopped, return to step 4. If stopped, close the run and emit a final trace entry.

---

## Integration Points

| Service | Purpose | Auth Method | Failure Strategy |
|---------|---------|-------------|------------------|
| `carestechs-flow-engine` | Deterministic execution of nodes/subflows | API key or shared secret (TBD based on engine's auth) | Typed runtime error surfaced in the trace; run either pauses for human unblock or terminates per policy decision |
| `carestechs-flow-engine` (webhooks in) | Progress/terminal events per step | HMAC signature over the request body + shared secret | Idempotent handlers; engine retries are safe; malformed/unsigned events are rejected and logged |
| LLM provider (Anthropic by default) | Policy decisions via tool calling | API key from env | Typed runtime error; retry with backoff; policy failure terminates the run with a trace entry rather than fabricating a decision |

---

## Security Architecture

- **Authentication (human entry):** For v1, CLI invocation runs with the user's local credentials; the HTTP intake endpoint is optional and, when enabled, uses the stack's standard bearer-token auth (`adrs/api/jwt-bearer-auth.md`) or an API key — defer the concrete choice to when a non-local caller actually exists.
- **Authentication (service-to-service):** Flow engine ↔ orchestrator webhooks use a **shared secret with HMAC request signing**. The orchestrator rejects any webhook request whose signature does not match.
- **Authorization:** v1 is single-tenant (one repo, one operator). No row-level authorization needed. This assumption MUST be revisited before any multi-tenant deployment.
- **Data protection:** LLM API keys, engine shared secrets, and webhook signing keys come from environment variables, never from images or source control. Run traces contain policy inputs/outputs, which MAY include snippets of repository content — treat the trace directory as sensitive (same posture as the repo itself).
- **API security:** All inbound HTTP routes validate payloads via Pydantic. Webhook handlers are idempotent and rate-limit by run id. No CORS concerns in v1 (no browser client).

---

## AI Task Generation Notes

> These notes help AI assistants generate technically correct tasks.

- **Respect component boundaries.** Don't mix responsibilities across components — in particular, never let the policy call the flow-engine client directly, and never put business logic inside tool definitions.
- **Honor AD-3 (Flow + Policy + Memory).** Tasks that blur those three slots MUST flag the trade-off explicitly. Any change that requires the engine to know about policies violates composition and should be rejected or redesigned.
- **Honor AD-1 (passive engine).** Do not propose modifications to `carestechs-flow-engine` to make agent behavior easier. The orchestrator adapts to the engine, not the other way around.
- **Follow the defined data flow.** New capabilities slot in at well-defined seams (new tool, new policy strategy, new stop condition, new trace kind) rather than by reshaping the loop.
- **Use only listed technologies** unless proposing a migration (which needs its own task). In particular, do NOT introduce pgvector, Celery, or a frontend before an in-scope feature requires it.
- **Honor the security architecture.** Every new endpoint needs auth consideration; webhook endpoints specifically MUST validate HMAC signatures.

---

## Changelog

- 2026-04-19 — FEAT-006 rc2-phase-1 — Engine-backed state for the deterministic flow. The flow engine is now the shared source of truth for work-item + task state across tools (AD-1 realized in earnest for the first time). Orchestrator registers two workflows (`work_item_workflow`, `task_workflow`) on startup, mirrors every transition to the engine via `FlowEngineLifecycleClient`, and consumes `item.transitioned` webhooks to fire W2/W5 derivations (`lifecycle/reactor.py`). Local status columns remain in phase 1 as a read-through cache; phase 2 will drop them once auxiliary-row writes (Approval/TaskAssignment/TaskPlan/TaskImplementation) also flow through the reactor via a correlation-context table. Rejection transitions (T3, T8, T11) are asymmetric — they don't produce engine transitions since status doesn't change; only the `Approval` row records the event.
- 2026-04-18 — FEAT-005 — Added Lifecycle agent, local-tool registry, pause/resume contract, and correction-attempt bound to Runtime Loop Components. `RunSignal` is the first entity persisted on the operator side; lifecycle steps have `engine_run_id=NULL` by design.
- 2026-04-18 — FEAT-004 — Documented the trace-streaming reader under Runtime Loop Components. `GET /runs/{id}/trace` is the first NDJSON-streaming endpoint; the reader/writer are isolated via read-only file handles (no shared lock).
- 2026-04-18 — FEAT-003 — Added `AnthropicLLMProvider` to the Runtime Loop Components section; documented the composition-root provider-swap pattern. No contract changes.
- 2026-04-18 — FEAT-002 — Added "Runtime Loop Components" section describing `runtime.run_loop`, `RunSupervisor`, `JsonlTraceStore`, reconciliation helper, and the lifespan zombie sweep. Noted AD-5's JSONL implementation is live; Postgres v2 migration remains future work. Documented the single-uvicorn-worker constraint imposed by the process-local supervisor.
- 2026-04-15 — Initial version. Adopted the `python-ai-agent-service-docker-compose` stack profile. Documented project-specific decisions AD-1 through AD-6. Added component map, data flow, integration points, and security posture for v1.
