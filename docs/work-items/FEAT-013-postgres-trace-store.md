# Feature Brief: FEAT-013 — Postgres trace store (close the AD-5 v2 migration)

> **Purpose**: Replace the JSONL trace files with a Postgres-backed `TraceStore` implementation, completing the migration AD-5 anticipated. Trace records — `step`, `policy_call`, `webhook_event`, `operator_signal`, `effector_call`, `executor_call` — land in normalized tables, are queryable across runs, and stream to the existing FEAT-004 NDJSON endpoint with the same follow / `since` / `kinds` semantics they have today. JSONL keeps shipping as an opt-in local-dev backend; tests stay on `noop`. The seam designed in AD-5 (the `TraceStore` protocol + `get_trace_store()` factory) means this is a composition-root swap — `runtime.py`, `runtime_deterministic.py`, `service.py`, and the streaming router never change.
>
> **Why now.** Run/step/policy-call/webhook-event metadata already lives in Postgres (`models.py`). The JSONL trace is a *parallel*, denormalized projection of mostly the same data — every record we write to a file is either a duplicate of a row that exists in `steps` / `policy_calls` / `webhook_events` / `run_signals`, or it's one of the two kinds (`effector_call`, `executor_call`) that we just never modeled in SQL. The drift cost is real: cross-run queries require shelling out to `jq`; trace directories need backup separately from the database; the trace-streaming reader has its own polling implementation that has nothing to do with how the rest of the system reads state. AD-5 said "JSONL-first then database, likely within the first post-v1 iteration." v1 has been live for months and we're well past that window.
>
> **Relationship to FEAT-004.** FEAT-004 stood up `GET /api/v1/runs/{id}/trace` with NDJSON streaming + follow mode + filters. FEAT-013 keeps that endpoint surface identical — only the underlying implementation changes. The reader, which today opens a read-only `aiofiles` handle and polls for new lines, becomes a SQL reader (`SELECT ... WHERE run_id = ? AND created_at > ? ORDER BY created_at`) plus a live-tail mechanism (Postgres `LISTEN`/`NOTIFY` if it lands cleanly, polling otherwise). The endpoint's external contract is byte-for-byte unchanged; integration tests are the bar.
>
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-013 |
| **Name** | Postgres trace store (AD-5 v2 migration) |
| **Target Version** | v0.8.0 |
| **Status** | Completed (T-271..T-281 all landed) |
| **Priority** | High |
| **Requested By** | Project owner (post-v1 cleanup; AD-5 deferred work) |
| **Date Created** | 2026-05-11 |

---

## 2. User Story

**As an** orchestrator operator investigating a misbehaving run across the fleet, **I want** every trace record to be queryable with the same SQL tools I already use for run / step / task state — **so that** I can answer "show me every `policy_error` in the last 24 hours across all agents" or "which runs touched node X" without writing a shell pipeline over a directory of JSONL files, and so that trace history is backed up, restored, and replicated as part of the same Postgres dump that already covers everything else.

---

## 3. Goal

After the FEAT lands, `settings.trace_backend = "postgres"` is the default; the JSONL backend ships unchanged for local dev; the NDJSON streaming endpoint is byte-identical to its FEAT-004 behavior; and a deployed orchestrator does not write trace files to disk under any path except `trace_backend = "jsonl"`. Run-deletion, retention, and backup of trace data follow Postgres conventions, not filesystem operations.

---

## 4. Feature Scope

### 4.1 Included

- **Schema additions.** New SQLAlchemy models + Alembic migration for the two trace kinds that aren't already in SQL today:
  - `effector_calls` — keyed on `(entity_id, created_at)`, mirrors the `EffectorCallDto` payload, indexed on `entity_id` and `transition_key`.
  - `executor_calls` — keyed on `(run_id, created_at)`, mirrors the `ExecutorCallDto` payload, indexed on `run_id` and `node_name`.
  Both tables use `BIGSERIAL` `id` + `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` so insertion order matches monotonic time, and `created_at` is what `since=` filters against.
- **`PostgresTraceStore`** under `src/app/modules/ai/trace_postgres.py`, implementing every method of the `TraceStore` protocol:
  - `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` — **no-op writes** under engine-present mode (Step / PolicyCall / WebhookEvent / RunSignal rows are already written by the runtime / signal adapter; the trace store reads them, doesn't double-write). The method signatures are preserved for protocol compatibility; the implementation comments document the read-only intent.
  - `record_effector_call` / `record_executor_call` — write a row to the new tables in the caller's session if one is supplied, else open a short-lived session from the factory.
  - `read_effector_calls(entity_id)` — `SELECT ... WHERE entity_id = ? ORDER BY created_at` mapped back to `EffectorCallDto`.
  - `open_run_stream(run_id)` — `UNION ALL` over `steps`, `policy_calls`, `webhook_events`, `run_signals`, `executor_calls` filtered on `run_id`, ordered by `created_at`, materialized as the existing DTO union.
  - `tail_run_stream(run_id, follow, since, kinds)` — same shape as JSONL: one-shot drain by default, follow mode that polls for new rows (`since = max(created_at) so far`) until the run reaches a terminal state. The reader **must not** hold a session across the lifetime of the stream — each poll opens its own short session from `session_factory`, the same discipline the runtime loop already uses (CLAUDE.md "Each runtime-loop iteration opens its own `AsyncSession`").
- **`LISTEN`/`NOTIFY` live-tail (stretch, gated).** If the asyncpg path supports it cleanly under our SQLAlchemy async setup, the follow-mode reader subscribes to a per-run channel; the writers emit a `NOTIFY` after commit. Falls back to polling at the same 200 ms cadence as the JSONL implementation if `NOTIFY` lookup adds complexity. The decision is made during T-{first task of the LISTEN/NOTIFY investigation} and documented in `docs/design/feat-013-trace-tail-mechanism.md` — polling-only is an acceptable outcome.
- **Backend selector.** `settings.trace_backend` gains a third value, `"postgres"`, and the production default flips to it. `"jsonl"` and `"noop"` continue working unchanged. `get_trace_store()` in `trace.py` dispatches on the new value; the cached singleton mechanism is preserved.
- **Composition-root wiring.** `PostgresTraceStore` is constructed with the same `async_sessionmaker` the rest of the app uses — no new connection pool, no new engine. Verified by a test that asserts only one `AsyncEngine` is constructed during app startup regardless of `trace_backend`.
- **NDJSON streaming endpoint parity (FEAT-004).** `GET /api/v1/runs/{id}/trace` returns the exact same bytes, in the same order, with the same filters honored (`?follow`, `?since`, `?kind=`). Verified by a side-by-side integration test that drives a run under each backend and compares the streamed bodies byte-for-byte (after normalizing line-buffering differences).
- **CLI parity.** `orchestrator runs trace <id> [--follow] [--since ...] [--kind ...]` is byte-identical under both backends. Same test bar.
- **Effector trace coverage check (FEAT-008 / T-172).** The invariant-3 test today enumerates declared transitions and asserts every one produced an `effector_call` trace entry or carries a `no_effector` exemption. The test is rewritten to call `read_effector_calls(entity_id)` against `PostgresTraceStore` for the migrated path, and against `JsonlTraceStore` for the JSONL path. Both implementations must satisfy the same invariant.
- **Migration tooling.** A one-shot `uv run orchestrator migrate-traces --from=jsonl --since=<ISO-8601> [--dry-run]` command that reads the JSONL files under `settings.trace_dir`, parses entries via the same DTO discriminator the reader uses, and inserts them into the new Postgres tables. Idempotent on `(run_id, kind, created_at, payload_hash)` so a re-run after partial failure is safe. Used for one-time migration of in-flight production traces; not a permanent feature.
- **Retention knob.** A new setting `trace_retention_days: int | None = None` that, when set, drives a daily cleanup job (Postgres-side `DELETE` filtered by `created_at < now() - interval`). Default `None` (no retention) preserves current behavior. The job is implemented as a CLI command `orchestrator trace-retention-sweep [--dry-run]` that operators wire to their scheduler of choice — no in-process cron in v1.
- **Documentation.** `ARCHITECTURE.md` AD-5 row gets a "Status: implemented" note + a changelog entry pointing at FEAT-013. `data-model.md` gains `EffectorCall` and `ExecutorCall` entities + a changelog entry. `CLAUDE.md` "Patterns to Follow" gets an updated entry: "Trace writes go through `TraceStore`; `PostgresTraceStore` is the default backend, `JsonlTraceStore` is local-dev, `NoopTraceStore` is the test default. The runtime loop never imports either implementation directly — only the protocol."

### 4.2 Excluded

- **Removing the JSONL backend.** `JsonlTraceStore` keeps shipping as an opt-in `trace_backend = "jsonl"` for local-dev ergonomics (single-process, zero-setup, human-greppable). The case to remove it lands as a separate FEAT after the Postgres backend has soaked.
- **Schema changes to `steps` / `policy_calls` / `webhook_events` / `run_signals`.** These tables exist; their shape is unchanged. FEAT-013 reads from them.
- **Trace search / full-text index.** Pure structured queries (`WHERE run_id`, `WHERE kind`, `WHERE created_at >`) only. A `tsvector` over policy-call payloads, vector embeddings over reasoning blocks, etc. are future work; pgvector remains out of scope per AD-2 / CLAUDE.md.
- **Cross-run analytics endpoints.** No new aggregate routes (`/api/v1/traces/summary`, etc.). The point of this FEAT is that operators can query Postgres directly with whatever tooling they already use; new endpoints land as follow-on FEATs when concrete need surfaces.
- **In-process retention scheduler.** The retention sweep is a CLI command; wiring it to cron / systemd timers / Kubernetes `CronJob` is an ops concern, not v1 scope.
- **Migrating historical JSONL traces older than the migration window.** The `migrate-traces` command is scoped via `--since=` and intended for in-flight runs at cutover. Bulk-importing months of historical trace files is not required and not validated.
- **Removing the per-entity effector trace file shape.** Today the JSONL backend writes effector traces under `<trace_dir>/effectors/<entity_id>.jsonl` (keyed on entity, not run). The Postgres backend keys on `entity_id` too — the *protocol* method signature is unchanged. We are not introducing a new "trace stream per entity" endpoint as part of this FEAT.

---

## 5. Acceptance Criteria

- **AC-1**: `settings.trace_backend = "postgres"` is the production default. A fresh `docker compose up` with no `TRACE_BACKEND` env override writes nothing to `settings.trace_dir`.
- **AC-2**: `effector_calls` and `executor_calls` tables exist via a forward-only Alembic migration; rolling back the migration is a destructive operation and documented as such (production runs do not roll back).
- **AC-3**: For a run executed end-to-end under `trace_backend = "postgres"`, `GET /api/v1/runs/{id}/trace` returns byte-identical NDJSON to the same run replayed under `trace_backend = "jsonl"`, after normalizing only the `created_at` timestamp resolution differences (TIMESTAMPTZ vs. ISO-string round-trip). Verified by integration test.
- **AC-4**: Follow mode (`?follow=true`) streams new trace records to a connected client within ≤ 1 second of the writing commit landing, under both `LISTEN`/`NOTIFY` and polling implementations. Verified by integration test that drives a run and asserts the client sees each entry under the threshold.
- **AC-5**: `?since=<ISO-8601>` and `?kind=` filters return the same set of records under both backends. Empty result sets close the stream the same way (graceful EOF in non-follow, ongoing tail in follow).
- **AC-6**: The runtime loop, FastAPI dependency tree, and CLI runner construct exactly one `AsyncEngine` regardless of `trace_backend` value. Verified by a startup test.
- **AC-7**: The FEAT-008 / T-172 effector-trace invariant test passes against both backends. Every declared transition either produced an `effector_call` row / line or carries a `no_effector` exemption.
- **AC-8**: `orchestrator migrate-traces --from=jsonl --since=<ISO-8601>` is idempotent: running it twice over the same input produces the same database state and emits a "skipped N already-present entries" log on the second run.
- **AC-9**: `orchestrator trace-retention-sweep --dry-run` reports the row counts that would be deleted (per kind, older than `trace_retention_days`). Without `--dry-run`, it actually deletes them, and the next sweep is a no-op.
- **AC-10**: No code outside `src/app/modules/ai/trace_postgres.py` and tests imports the new SQLAlchemy models for `EffectorCall` / `ExecutorCall`. The runtime continues to depend only on the `TraceStore` protocol. Verified by a structural guard test similar in shape to the FEAT-009 import-quarantine test.
- **AC-11**: A run started under `trace_backend = "jsonl"` and resumed under `trace_backend = "postgres"` (or vice versa) does not crash — the runtime loop does not read its own past trace, so swap-in-place is safe. Verified by integration test.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `EffectorCall` (new) | Append-only row per effector dispatch; keyed on `entity_id`, indexed on `transition_key` | `id BIGSERIAL`, `entity_id UUID NOT NULL`, `transition_key TEXT NOT NULL`, `payload JSONB`, `outcome TEXT`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. No `updated_at` — trace rows are immutable. |
| `ExecutorCall` (new) | Append-only row per executor dispatch terminal state; keyed on `run_id`, indexed on `node_name` | `id BIGSERIAL`, `run_id UUID NOT NULL REFERENCES runs(id)`, `node_name TEXT NOT NULL`, `dispatch_id UUID`, `payload JSONB`, `outcome TEXT`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Immutable. |
| `Step` | Existing — read by `PostgresTraceStore.open_run_stream` / `tail_run_stream` | No schema change. `created_at` is the trace-order key. |
| `PolicyCall` | Existing — same | No schema change. |
| `WebhookEvent` | Existing — same | No schema change. |
| `RunSignal` | Existing — same | No schema change. The `record_operator_signal` method becomes a no-op write because the row is already persisted by the signal adapter. |

**New entities required:** `EffectorCall`, `ExecutorCall`. Both need entries in `docs/data-model.md`.

---

## 7. API Impact

No endpoint shape changes. The streaming endpoint's underlying implementation swaps; the wire contract is preserved.

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs/{id}/trace` | GET | Existing | Same NDJSON shape; same filters; backed by `PostgresTraceStore` when `trace_backend = "postgres"`. |

**New endpoints required:** None.

---

## 8. UI Impact

N/A.

---

## 9. Edge Cases

- **Concurrent writer + tailing reader on the same run.** Polling reader uses `created_at > last_seen` so it never reads a row before its commit lands. `LISTEN`/`NOTIFY` (if chosen) fires post-commit. Either way, no torn reads. Verified by a stress test that runs the tail concurrently with a high-volume trace producer.
- **Clock skew between client and server.** `since=` is parsed as TIMESTAMPTZ and compared on the server. No client-side time math.
- **Run ID with no trace rows yet.** Non-follow returns an empty stream and closes; follow blocks on the polling loop / `LISTEN` channel until the first row arrives or the run reaches a terminal state.
- **Trace producer commits a row, then the writer's session rolls back later in the same transaction.** Cannot happen by construction — every `record_*` opens a short-lived session and commits, the same shape the FEAT-008 reactor uses for `WebhookEvent`. Trace writes are never inside a larger business transaction.
- **Migration command run mid-flight.** Each JSONL line is parsed via the existing DTO discriminator; insertion is idempotent on `(run_id, kind, created_at, payload_hash)`. A run that is *still writing JSONL* while migration runs may have its tail picked up on a second invocation; the idempotency key prevents duplicates.
- **`LISTEN`/`NOTIFY` channel collisions across run IDs.** If we adopt LISTEN/NOTIFY, channels are named `trace_run_<uuid>` — UUIDs cannot collide. The producer publishes after commit, the reader subscribes per-stream. Channel cleanup on stream close is a `finally`.
- **Tracestore reader holds a session across a long-running tail.** Forbidden — same discipline as the runtime loop. Each poll iteration opens its own session. The structural test in AC-10 covers this.
- **Backend selector toggled at runtime.** Not supported — `get_trace_store()` caches the singleton at startup. Switching `TRACE_BACKEND` requires a restart, documented in the settings reference.

---

## 10. Constraints

- Forward-only Alembic migration. Downgrade is destructive (`DROP TABLE`) and not part of normal ops.
- No new external dependencies. Postgres + SQLAlchemy async are already in the stack. `LISTEN`/`NOTIFY`, if adopted, uses the asyncpg path we already have.
- The `TraceStore` protocol shape is preserved. Any change to the protocol (e.g., adding a new trace kind) is a separate FEAT.
- `JsonlTraceStore` keeps working unchanged. Its tests continue to pass.
- Single-uvicorn-worker constraint preserved. The trace store is process-local in the same way it is today; `LISTEN`/`NOTIFY` would in principle support multi-worker, but FEAT-013 does not unlock multi-worker — `RunSupervisor` still serializes.
- Test suite must still run with the default `trace_backend = "noop"`. CI does not require Postgres for unit tests (only for integration tests, as today).
- Run trace volume per run is unbounded in principle (long-running agents accumulate many steps + webhook events). The tables must scale by adding pagination at the read layer if needed; default reader pages in 1000-row chunks under the polling implementation to bound memory.

---

## 11. Motivation and Priority Justification

**Motivation:** AD-5 v1 was always temporary. We're now well past "the first post-v1 iteration" and the parallel persistence is producing concrete drag: cross-run queries are CLI-only, trace backup is a separate operational concern, the streaming reader has its own bespoke polling implementation that has nothing to do with how the rest of the system reads state, and every new trace kind (effector_call, executor_call) has had to invent its own file-layout convention. Each delay locks in more of those bespoke shapes.

**Impact if delayed:** Every new trace kind reinforces the per-file convention; every operator script that greps `<trace_dir>` is a future migration cost; trace volume grows linearly and the filesystem layout starts mattering for backup tooling. The longer we wait, the larger the cutover window for `migrate-traces`.

**Dependencies on this feature:** None blocking. This is a closeout of AD-5's v2 commitment. A future "remove `JsonlTraceStore` entirely" FEAT depends on this transitively (you can only remove the JSONL backend once Postgres is the proven default).

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | Orchestrator operator |
| **Stakeholder Scope Item** | Observability is non-negotiable (AD-5 motivation); durable run state with queryable history (AD-5 long-term shape) |
| **Success Metric** | Cross-run trace queries are pure SQL; trace backup is part of the Postgres dump; new trace kinds register as tables, not as file conventions |
| **Related Work Items** | FEAT-002 (runtime loop), FEAT-004 (trace streaming), FEAT-008 (effector registry — emits effector_call traces), FEAT-009 (executor seam — emits executor_call traces) |

---

## 13. Usage Notes for AI Task Generation

1. **Protocol seam first.** No service-code or runtime-loop file changes. If a task touches `runtime.py`, `runtime_deterministic.py`, or `service.py`, that's a signal the seam is being violated — push the work back behind the `TraceStore` protocol.
2. **Schema before backend before swap.** Land the Alembic migration first, then `PostgresTraceStore`, then flip the default. A partial landing (backend exists, default not flipped) is fine — leave it under `TRACE_BACKEND=postgres` opt-in for the soak window.
3. **Byte-identical NDJSON is the bar.** Every test that compares streams between backends compares bytes (after timestamp normalization). "Looks equivalent" is not enough — operators have scripts grepping the existing output.
4. **`LISTEN`/`NOTIFY` is a stretch goal, not a blocker.** If it adds more than ~1 day of integration work or complicates the test setup, fall back to polling at 200 ms. Document the decision in `docs/design/feat-013-trace-tail-mechanism.md`.
5. **No double-write.** `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` are *no-op writes* in `PostgresTraceStore` — those rows are written elsewhere already. Adding a second write path here re-creates a FEAT-008-shaped drift problem.
6. **`migrate-traces` is one-shot tooling.** Don't generalize it. It exists to cover the cutover window for in-flight runs. After the soak, it can be deleted in a future cleanup.
7. **Retention sweep is a CLI command, not in-process.** Operators wire it to their scheduler. Don't introduce APScheduler / Celery / Quartz for this.
8. **The structural guard test (AC-10) is the regression bar for the FEAT-009-style invariant** — the runtime never imports the new SQL models. Follow the existing import-quarantine subprocess pattern.
