# Data Model

## Overview

The orchestrator models a small, tightly-focused domain: **agent runs** and everything needed to reproduce, inspect, and audit one. There is a single owning module (`ai`), because this project *is* an AI agent service. Every entity exists to make one statement true: "given this run id, I can reconstruct exactly what happened, what the policy was asked, what it decided, what the engine did, and why the run ended."

**Agent definitions are not DB entities in v1.** Per the stakeholder scope lock, agent definitions live as YAML/JSON files on disk (code-first authoring), loaded at run start. Only the *references* to them — by stable id/version string — are persisted with each run.

**Storage split (per `ARCHITECTURE.md` AD-5).** v1 persists these entities as **append-only JSONL per run**. A v2 migration projects them into PostgreSQL tables with the same shape. The entity definitions below describe the logical model; they apply to both stores.

### Key Modeling Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary key strategy | UUIDv7 | Time-sortable, no sequential ID leaks; fine for both JSONL `id` fields and PostgreSQL PKs |
| Timestamps | `timestamptz`, UTC | Matches `adrs/database/timestamptz-always.md` in the profile |
| Soft vs hard deletes | **Neither — append-only** | Runs are history; nothing is deleted. Cancellation is a status, not a row removal |
| Mutability | Runs and RunMemory are mutable; everything else is append-only | Policy calls, steps, and webhook events are facts-at-a-time; Run holds the current status and RunMemory holds the live per-run scratchpad |
| Large JSON fields | `JSONB` in Postgres (v2); raw JSON in JSONL (v1) | Policy prompt context, tool args, and engine payloads are variable-shape and inspected far more often than queried |
| Cross-module references | **None** | Single-module project in v1; revisit only if a second module is introduced |

## Module Ownership

| Module | Entities Owned | DbContext (v2) |
|--------|---------------|----------------|
| `ai` | `Run`, `Step`, `PolicyCall`, `WebhookEvent`, `RunMemory`, `RunSignal` | `ai` module's `AsyncSession` (per `adrs/python/sqlalchemy-async.md`) |

## Entity Definitions

### Run

> *Module: `ai` — A single execution of an agent against a specific intake (e.g., a feature brief). The top-level record every other entity points back to.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | Run id (UUIDv7). |
| agent_ref | text | Required | Stable reference to the agent definition used (e.g., `lifecycle-agent@0.3.0`). |
| agent_definition_hash | text | Required | Content hash of the loaded agent YAML/JSON. Pins exact reproducibility. |
| intake | JSONB | Required | The inbound payload that started the run (e.g., `{ "feature_brief_path": "docs/work-items/FEAT-042.md" }`). |
| status | enum `RunStatus` | Required, default `pending` | See enum. |
| stop_reason | enum `StopReason` | Nullable | Populated when terminal. |
| final_state | JSONB | Nullable | Snapshot of orchestrator-side state at run end. |
| started_at | timestamptz | Required | When the runtime loop began. |
| ended_at | timestamptz | Nullable | Populated when terminal. |
| trace_uri | text | Required | Location of the run's JSONL trace (v1) or table partition reference (v2). |
| created_at | timestamptz | Required, Auto | Record creation timestamp. |
| updated_at | timestamptz | Required, Auto | Last modification timestamp. |

**Indexes:**
- BTREE on `status, started_at DESC` — dashboard / "find recent failed runs" queries.
- BTREE on `agent_ref` — "all runs of this agent".

**Business Rules:**
- A `Run` transitions through `RunStatus` values monotonically: `pending → running → (completed|failed|cancelled)`. The three terminal states are disjoint and non-reversible.
- `ended_at` and `stop_reason` are set together or neither. Enforced in the service, asserted in tests.
- `StopReason` → `RunStatus` mapping (set by the runtime loop on termination):
    - `done_node`, `policy_terminated` → `completed`.
    - `budget_exceeded`, `error` → `failed`.
    - `cancelled` → `cancelled`.
- `agent_definition_hash` is computed once at load time and never rewritten — it's the pin.

---

### Step

> *Module: `ai` — One iteration of the runtime loop: the node that was chosen, dispatched to the flow engine, and observed to completion (or failure) via webhook. Append-only.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | Step id (UUIDv7). |
| run_id | UUID | FK → Run.id, Required | Parent run. |
| step_number | int | Required | Monotonic 1-based sequence within the run. |
| node_name | text | Required | Engine-side node name chosen by the policy. |
| node_inputs | JSONB | Required | Arguments passed to the engine for this node (policy tool-call args). |
| engine_run_id | text | Nullable | Correlation id returned by the engine for this dispatch. |
| status | enum `StepStatus` | Required, default `pending` | See enum. |
| node_result | JSONB | Nullable | Final outcome payload from the engine (via webhook) on success. |
| error | JSONB | Nullable | Error payload if the step failed. |
| dispatched_at | timestamptz | Nullable | When the outbound engine call returned (engine accepted dispatch). |
| completed_at | timestamptz | Nullable | When the terminal webhook event for this step was processed. |
| created_at | timestamptz | Required, Auto | Record creation timestamp. |

**Indexes:**
- UNIQUE on `(run_id, step_number)`.
- BTREE on `engine_run_id` — inbound webhook lookup.

**Business Rules:**
- `step_number` is allocated by the runtime at dispatch and MUST be contiguous per run.
- A step's `status` transitions: `pending → dispatched → (in_progress →)? completed | failed`. **Monotonic**: webhook reconciliation rejects any event that would roll the status backward (e.g., a late `node_started` after `node_finished` is a no-op).
- `node_result` is populated only on `completed`; `error` only on `failed`.
- Append-only once terminal: the three terminal states (`completed`, `failed`) are never mutated after the owning webhook commits.

---

### PolicyCall

> *Module: `ai` — One invocation of the policy (LLM) that produced a decision. Append-only. Exactly one `PolicyCall` per `Step` — the one whose tool call selected that step.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | Policy call id (UUIDv7). |
| run_id | UUID | FK → Run.id, Required | Parent run. |
| step_id | UUID | FK → Step.id, Required, Unique | The step this decision produced. |
| prompt_context | JSONB | Required | The state + memory snapshot fed to the model, plus system/user messages. |
| available_tools | JSONB | Required | List of tool definitions exposed on this call (name + description + parameter schema per tool). |
| provider | text | Required | E.g., `anthropic`, `openai`, `stub`. |
| model | text | Required | Concrete model id (e.g., `claude-opus-4-6`). |
| selected_tool | text | Required | Name of the tool the model called. |
| tool_arguments | JSONB | Required | Arguments of the selected tool call. |
| input_tokens | int | Required | Prompt token count reported by the provider. |
| output_tokens | int | Required | Completion token count reported by the provider. |
| latency_ms | int | Required | End-to-end latency of the provider call. |
| raw_response | JSONB | Nullable | Full provider response (for forensic debugging; may be redacted or truncated). |
| created_at | timestamptz | Required, Auto | Record creation timestamp. |

**Indexes:**
- UNIQUE on `step_id`.
- BTREE on `run_id, created_at`.

**Business Rules:**
- `selected_tool` MUST be a member of the `available_tools` list on the same record — this is enforced in the service and asserted in tests. The policy MUST NOT produce a decision outside the declared action space (per `adrs/ai/policy-via-tool-calling.md`).
- Zero or multiple tool calls from the model MUST be recorded as a failed `PolicyCall` with `selected_tool = null` (or a sentinel error record) and MUST NOT advance the run.
- Append-only once inserted: no field is mutated after commit.

---

### WebhookEvent

> *Module: `ai` — An inbound event from the flow engine. Append-only. Every event is persisted before any runtime action is taken on it.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | Event id (UUIDv7). |
| run_id | UUID | FK → Run.id, Required | Parent run (derived from `engine_run_id` correlation). |
| step_id | UUID | FK → Step.id, Nullable | Parent step, when the event is step-scoped. |
| event_type | enum `WebhookEventType` | Required | See enum. |
| engine_run_id | text | Required | Correlation id from the engine. |
| payload | JSONB | Required | Full, validated event body. |
| signature_ok | bool | Required | Whether HMAC validation passed. Events with `false` are persisted and rejected. |
| received_at | timestamptz | Required, Auto | When the HTTP request landed. |
| processed_at | timestamptz | Nullable | When the runtime loop consumed the event. |
| dedupe_key | text | Required, Unique | Deterministic key for idempotent retry handling (e.g., `engine_run_id:event_type:engine_event_id`). |

**Indexes:**
- UNIQUE on `dedupe_key` — idempotency.
- BTREE on `run_id, received_at`.

**Business Rules:**
- Handlers MUST be idempotent: receiving the same `dedupe_key` twice MUST NOT double-advance the run.
- An event with `signature_ok = false` MUST be persisted (for forensics) and rejected with 401; it MUST NOT be delivered to the runtime loop.

---

### RunSignal

> *Module: `ai` — An operator-injected signal for an in-flight run (e.g., "implementation-complete"). Append-only. Idempotent via a UNIQUE `dedupe_key` derived from `(run_id, name, task_id)`. Introduced by FEAT-005 to support the lifecycle agent's pause/resume contract.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | Signal id (UUIDv7). |
| run_id | UUID | FK → Run.id, Required | Parent run. |
| name | text | Required | Signal name (v1: `'implementation-complete'`). |
| task_id | text | Nullable | Target task when the signal is task-scoped; NULL for run-scoped signals. |
| payload | JSONB | Required, default `{}` | Free-form payload (e.g., `commit_sha`, operator notes). |
| received_at | timestamptz | Required, Auto | When the HTTP request landed. |
| dedupe_key | text | Required, Unique | Deterministic key for idempotent retry handling (`sha256("{run_id}:{name}:{task_id or ''}")`). |

**Indexes:**
- UNIQUE on `dedupe_key` — idempotency.
- BTREE on `(run_id, received_at)` — "all signals for this run in order".

**Business Rules:**
- Handlers MUST be idempotent: receiving the same `dedupe_key` twice MUST NOT double-advance the run. The endpoint persists on first call and returns `alreadyReceived=true` on subsequent calls.
- Signals are persisted *before* the supervisor is woken (mirrors the webhook pipeline's persist-first invariant from AD-2).
- A signal whose `name` is not recognized by the runtime is persisted (for forensics) and returned as `202 Accepted`, but the supervisor is not woken.
- Append-only: no field is mutated after insert.

---

### RunMemory

> *Module: `ai` — The agent's per-run scratchpad. One row per run. Mutable. Discarded (but kept in the trace) when the run terminates.*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| run_id | UUID | PK, FK → Run.id | One row per run. |
| data | JSONB | Required, default `{}` | Key-value store scoped to this run. |
| updated_at | timestamptz | Required, Auto | Last write. |

**Business Rules:**
- Memory is per-run only in v1 (per `ARCHITECTURE.md` AD-4). NEVER share memory across runs.
- Writes are made explicitly by the runtime after a step completes; the policy never mutates memory directly — it returns decisions, not side effects.
- On run termination, the final `data` is copied into `Run.final_state` (for post-hoc inspection) but the `RunMemory` row itself may be retained or purged per retention policy.

---

## Relationships

### One-to-Many

| Parent Entity | Child Entity | Foreign Key | Cascade Behavior |
|---------------|-------------|-------------|------------------|
| Run | Step | `step.run_id` | No cascade delete (runs are never deleted; purging uses a retention job) |
| Run | PolicyCall | `policy_call.run_id` | Same |
| Run | WebhookEvent | `webhook_event.run_id` | Same |
| Run | RunSignal | `run_signal.run_id` | Same |
| Step | WebhookEvent | `webhook_event.step_id` (nullable) | Same |

### One-to-One

| Entity A | Entity B | Link | Notes |
|----------|----------|------|-------|
| Step | PolicyCall | `policy_call.step_id` UNIQUE | Every step was produced by exactly one policy decision |
| Run | RunMemory | `run_memory.run_id` PK | Exactly one memory row per run |

### Many-to-Many

None.

### Cross-Module References

None — single-module project in v1.

## Enums

### RunStatus

> *Used by: `Run.status`*

| Value | Description |
|-------|-------------|
| `pending` | Created but runtime loop has not yet started |
| `running` | Active — the loop is advancing |
| `paused` | Waiting for a human unblock (explicit decision point) |
| `completed` | Terminated successfully (policy-driven or explicit done node) |
| `failed` | Terminated due to error (engine error, policy error, budget breach with fail-policy) |
| `cancelled` | Terminated by external request |

### StepStatus

> *Used by: `Step.status`*

| Value | Description |
|-------|-------------|
| `pending` | Allocated, not yet dispatched |
| `dispatched` | Outbound engine call returned success |
| `in_progress` | Engine reported started (intermediate event) |
| `completed` | Terminal success webhook processed |
| `failed` | Terminal failure webhook processed |

### StopReason

> *Used by: `Run.stop_reason`*

| Value | Description |
|-------|-------------|
| `done_node` | An explicit "done" node in the flow was reached |
| `policy_terminated` | Policy chose to stop (e.g., it emitted the `stop` tool) |
| `budget_exceeded` | Token/step/time budget hit |
| `error` | Unrecoverable error (engine, provider, or internal) |
| `cancelled` | External cancellation |

### WebhookEventType

> *Used by: `WebhookEvent.event_type`*

| Value | Description |
|-------|-------------|
| `node_started` | Engine began executing the node |
| `node_finished` | Engine completed the node successfully |
| `node_failed` | Engine failed the node |
| `flow_terminated` | Engine considers the dispatched flow/subflow fully ended |

## Database Conventions

| Convention | Rule | Example |
|------------|------|---------|
| Table naming | snake_case, plural | `runs`, `steps`, `policy_calls`, `webhook_events`, `run_memory` |
| Column naming | snake_case | `created_at`, `engine_run_id` |
| Primary keys | UUIDv7, column named `id` (except `run_memory.run_id` which is both PK and FK) | `id UUID PK` |
| Timestamps | `timestamptz`, UTC, `created_at` on every append-only entity; `updated_at` where mutable | `created_at timestamptz not null default now()` |
| Enums | Stored as text + check constraint (simpler migrations than PG enum types) | `status text not null check (status in ('pending','running',...))` |
| JSON fields | `JSONB`, non-null default `'{}'` where applicable | `data jsonb not null default '{}'::jsonb` |

## AI Task Generation Notes

- **Single module**: Every data-access task targets `src/app/modules/ai/`. If a task implies a second module, flag it and ask — the v1 design does not have one.
- **Append-only where stated**: `Step`, `PolicyCall`, and `WebhookEvent` rows MUST NOT be updated after their terminal fields are set. No "edit last policy call" operations.
- **Field completeness**: Generated Pydantic/SQLAlchemy classes must include all fields defined here. Do NOT add fields that aren't documented — propose them in a doc update first.
- **Idempotency**: The `WebhookEvent.dedupe_key` constraint is load-bearing; any ingestion task MUST insert through a path that honors it.
- **JSONL ↔ Postgres parity**: Any change to an entity's shape MUST update both the v1 JSONL writer and the v2 SQLAlchemy model in the same PR. They are two representations of the same model.
- **No cross-module relationships**: Do not introduce foreign keys to modules that don't exist yet.

## Changelog

- 2026-04-18 — FEAT-005 — Added `RunSignal` entity (operator-injected signals, unique `dedupe_key` derived from `(run_id, name, task_id)`, persist-first-then-wake contract). Noted that lifecycle-agent steps legitimately have `engine_run_id=NULL` (local-tool path — no engine dispatch).
- 2026-04-18 — FEAT-002 — Documented `Run` and `Step` status transitions, the `StopReason` → `RunStatus` mapping, `Step` monotonic reconciliation, and `PolicyCall` append-only invariant. No schema changes.
- 2026-04-15 — Initial version. Defined `Run`, `Step`, `PolicyCall`, `WebhookEvent`, `RunMemory` under the single `ai` module. Documented JSONL-first / Postgres-next storage split and append-only conventions.
