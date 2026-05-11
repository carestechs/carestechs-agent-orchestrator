# FEAT-013 — Postgres Trace Store: Tail Mechanism, No-Double-Write Rule, and `open_run_stream` Query Shape

**Status:** Accepted · **Date:** 2026-05-11 · **Closes the v2 commitment of:** [AD-5](../ARCHITECTURE.md#ad-5--durable-run-state-jsonl-first-then-database) · **Brief:** [`FEAT-013`](../work-items/FEAT-013-postgres-trace-store.md) · **Preserves the endpoint contract from:** [FEAT-004 (trace streaming)](../work-items/FEAT-004-trace-streaming.md).

> **Scope of this doc.** Three load-bearing decisions only, pinned before any code lands in FEAT-013. Schema, executor wiring, CLI tooling, and retention all have their own tasks (T-272 .. T-281). This doc is the spec the parity test (T-278) and the structural guard tests (T-279) enforce.

## Context

`TraceStore` is the AD-5 seam. v1 ships `JsonlTraceStore` writing append-only `<trace_dir>/<run_id>.jsonl` plus per-entity `<trace_dir>/effectors/<entity_id>.jsonl` and per-run `<trace_dir>/executors/<run_id>.jsonl`. FEAT-004 stood up `GET /api/v1/runs/{id}/trace` as `application/x-ndjson` with `?follow=true`, `?since=`, and repeatable `?kind=`. The reader opens a read-only `aiofiles` handle and polls every ~200 ms for new lines, closing cleanly when the run reaches a terminal state.

FEAT-013 replaces the *backend* of that seam with Postgres while keeping `JsonlTraceStore` shipping as an opt-in `trace_backend = "jsonl"` for local dev. The endpoint contract is preserved byte-for-byte. The runtime loop never imports either implementation — only the protocol.

Three open questions remained before T-272 (schema) and T-273 (store implementation) could start without ambiguity:

1. How does follow-mode wake when a new trace row lands?
2. Which `record_*` methods actually write rows in `PostgresTraceStore`, and which are no-ops?
3. What is the canonical SQL query that backs `open_run_stream` — sources, filter pushdown, ordering, pagination?

This doc answers all three.

---

## Decision 1 — Tail mechanism: polling-only at 200 ms, no `LISTEN`/`NOTIFY`

**Choice:** the follow-mode tail polls Postgres every 200 ms using `created_at > :high_water_mark` against each source branch. **No `LISTEN`/`NOTIFY`.**

The brief's Section 13 / Note 4 explicitly permits this: *"`LISTEN`/`NOTIFY` is a stretch goal, not a blocker. If it adds more than ~1 day of integration work or complicates the test setup, fall back to polling at 200 ms."*

### Rationale (structural, not a debug-it-later deferral)

The `LISTEN` mechanism in Postgres requires the **listening connection itself** to remain open for notifications to arrive — there is no "deliver to the next session that asks." That collides with two load-bearing disciplines already in this codebase:

- **CLAUDE.md, Patterns: "Each runtime-loop iteration opens its own `AsyncSession`."** Sessions are short-lived by construction. Trace tail readers are required to follow the same discipline (T-273 AC-10) — opening a fresh session per poll iteration, never reusing one across calls. A `LISTEN` subscriber that has to hold a connection open across the lifetime of an HTTP follower stream is the opposite shape.
- **AsyncPG `psycopg`-style `LISTEN` over SQLAlchemy 2.0 async** requires either (a) a dedicated raw `asyncpg` connection bypassing the session factory entirely — a parallel persistence surface, which CLAUDE.md's anti-pattern entry explicitly forbids for engine round-trips and which we should not introduce here either, or (b) a long-lived `AsyncSession` held by the streaming endpoint, which violates (1).

Adopting `LISTEN`/`NOTIFY` would mean introducing a dedicated subscriber-connection pool plus a notification-routing layer, *and* keeping the 200 ms polling loop as a backstop anyway (so dropped notifications don't stall the stream). The cost of that is non-trivial; the benefit is recovering at best ~100 ms of mean-case follower latency against an AC-4 bar that already permits 1000 ms.

200 ms polling already meets AC-4 (≤ 1 s follow latency) with a 5× safety margin. The JSONL implementation has shipped with this exact cadence since FEAT-004 with no complaints.

### Spike outcome

Time-box: half-day per the T-271 plan. Resolved by structural analysis rather than a live spike — the collision above is a property of how Postgres LISTEN works, not something the spike would discover. **Outcome recorded:** polling-only, by analysis, no live spike performed. Documenting this so future readers know it was a deliberate choice and not an "I'll spike it later" that fell through.

### What this means for the implementation

- `PostgresTraceStore.record_effector_call` and `record_executor_call` do **not** emit `NOTIFY` after commit. The writer does not need to know about followers.
- `PostgresTraceStore.tail_run_stream(run_id, follow=True, ...)` runs a `while not terminal` loop with `await asyncio.sleep(0.2)` between iterations. Each iteration opens its own session, queries with `created_at > high_water`, advances the high-water, yields rows.
- The loop exits when the owning `Run.status` becomes terminal (re-read per iteration; cheap — same row the supervisor would read).
- Tests gate behind backend, not behind a notification flag. T-280's "channel-collision" edge case is dropped from the test list (recorded as N/A in the cross-backend test parameterization).

### Revisiting later

If a future feature genuinely requires sub-200-ms follower latency (none currently does), the path forward is a dedicated subscriber service, not retro-fitting LISTEN onto the per-request session machinery. That would be its own FEAT.

---

## Decision 2 — `PostgresTraceStore` does **not** double-write the four already-persisted kinds

**Rule (hard invariant):** in `PostgresTraceStore`, the methods

- `record_step(run_id, step)`
- `record_policy_call(run_id, call)`
- `record_webhook_event(run_id, event)`
- `record_operator_signal(run_id, signal)`

are **no-op writes**. The rows are already persisted elsewhere by the writers that own them:

| Kind | Writer (existing, unchanged) | Table |
|------|------------------------------|-------|
| `step` | `runtime.py` / `runtime_deterministic.py` step emission | `steps` |
| `policy_call` | LLM-policy runtime loop, when applicable | `policy_calls` |
| `webhook_event` | The webhook router under `modules/ai/router.py` — "persist before reconcile, before wake" | `webhook_events` |
| `operator_signal` | `POST /api/v1/runs/{id}/signals` adapter | `run_signals` |

`PostgresTraceStore.open_run_stream` *reads* these rows; it does not also re-write them under a different identity. The method signatures are preserved for protocol compatibility (`TraceStore` is one `Protocol`, not four), and each no-op method carries an inline comment naming the owning writer.

### Why this is the hill we're choosing

FEAT-008 closed an analogous drift: aux-row writes were happening *both* inline in `lifecycle/service.py` *and* derived from the engine's webhook by the reactor. The fix was to make the reactor the sole writer; inline writes from signal adapters became forbidden (and a CLAUDE.md anti-pattern entry). The drift cost — two writers diverging under load, correlation matching breaking, "which row is canonical" becoming a recurring bug — is identical in shape to what a double-writing `PostgresTraceStore` would produce.

The CLAUDE.md anti-pattern *"Don't add a parallel persistence surface for engine round-trips"* applies by analogy. Trace rows are not engine round-trips, but the structural failure mode is the same: two writers, one logical record.

### What the read path looks like

`open_run_stream(run_id)` and `tail_run_stream(run_id, ...)` `UNION ALL` over six sources (Decision 3). Four of them are the existing tables; two of them are the new `effector_calls` and `executor_calls`. The reader projects each branch into the matching DTO so the wire output is indistinguishable from what `JsonlTraceStore` would emit for the same logical rows.

### What this rules out

- A future "trace event bus" that re-emits every step as a trace row in a single canonical table. That would be the consolidation move FEAT-013 explicitly defers — Section 4.2 of the brief excludes a single trace table because it would change the shape of `steps` / `policy_calls` / `webhook_events` / `run_signals`. If that consolidation lands later, it is a separate FEAT and supersedes this rule.
- Catch-up writes during `migrate-traces` (T-276). The CLI imports `effector_calls` / `executor_calls` rows from JSONL; for the other four kinds it performs a *read-only divergence check* — verifying a matching SQL row already exists for each JSONL line — and logs a count if not. It never inserts.

---

## Decision 3 — `open_run_stream` query shape

The canonical query for a one-shot drain of run `R`'s trace, after filter pushdown, is a `UNION ALL` over **four** sources — `steps`, `policy_calls`, `webhook_events`, `run_signals`. These are the four kinds the existing protocol surfaces through `open_run_stream` / `tail_run_stream` (the return type is `AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]`).

**Effector calls and executor calls are not in the run-stream union.** They have their own protocol methods (`read_effector_calls(entity_id)`, no run-keyed reader for executor calls — JSONL writes them under `executors/<run_id>.jsonl` but reads happen through a separate path). FEAT-013 preserves that surface; folding them into the per-run stream would change the FEAT-004 endpoint contract, which is explicitly out of scope.

```sql
-- Generated dynamically: each branch is included only if `?kinds=` admits it.
-- Each branch carries a literal `kind` column so the union row is self-describing.

SELECT 'step'            AS kind, s.id  AS source_id, s.created_at, <step_payload_columns…>
  FROM steps s
 WHERE s.run_id = :run_id
   AND s.created_at > :since      -- ALWAYS pushed down; :since defaults to '-infinity'::timestamptz
UNION ALL
SELECT 'policy_call'     AS kind, pc.id,        pc.created_at, <pc_payload_columns…>
  FROM policy_calls pc
 WHERE pc.run_id = :run_id
   AND pc.created_at > :since
UNION ALL
SELECT 'webhook_event'   AS kind, we.id,        we.created_at, <we_payload_columns…>
  FROM webhook_events we
 WHERE we.run_id = :run_id
   AND we.created_at > :since
UNION ALL
SELECT 'operator_signal' AS kind, rs.id,        rs.received_at AS created_at, <rs_payload_columns…>
  FROM run_signals rs
 WHERE rs.run_id = :run_id
   AND rs.received_at > :since
ORDER BY created_at ASC, source_id ASC        -- tie-break on source_id for determinism
LIMIT 1000;                                   -- pagination chunk
```

The DTO projection happens in Python — the SQL returns enough columns per branch (or whole-row SELECTs, since these are stable existing tables and the SQLAlchemy mappers are already wired) to construct each DTO without a follow-up query.

Per-table "creation" timestamp column may differ from `created_at` — `run_signals` uses `received_at`, which is what the JSONL backend's `_record_timestamp` helper reads for that DTO. The reader's filter pushdown uses each table's authoritative timestamp; the union projects it as `created_at` for consistent ordering.

### Filter pushdown rules

- **`?since=<ISO-8601>`** is parsed as `TIMESTAMPTZ` on the server and bound as `:since`. It is pushed into *every* branch's `WHERE`, not applied to the union result. When unset, `:since` binds to `'-infinity'::timestamptz`. Clock-skew handling: the bind is server-side; clients never send already-compared booleans.
- **`?kind=` (repeatable)** selects which `SELECT` branches participate in the union at all. Excluded branches are not selected from — the planner never reads rows the caller will not see. The branch list is fixed at six; the query is built by string concatenation in `trace_postgres.py` from a static template, not at runtime, so the plan caches.
- **`?run_id`** is bound as `:run_id` and is mandatory for the run-stream methods.

### Ordering

`ORDER BY created_at ASC, source_id ASC` on the outer union. The tie-break on `source_id` (the `BIGSERIAL` `id` of the source row, projected as `source_id` in the union) gives deterministic ordering for two rows that commit in the same microsecond. Without it, equal `created_at` rows could swap order between two reads of the same run — the parity test in T-278 would flake.

### Pagination chunk size

1000 rows per fetch. Used for both `open_run_stream` (one-shot drain) and `tail_run_stream`'s backlog drain before entering the polling loop. The number is empirical (matches the chunk size we use in the engine-corr reconciler) and adjustable without changing the contract.

### Why no `effector_calls` / `executor_calls` join into the run stream

These two kinds are *not* part of the per-run stream union. The reasons:

- The `TraceStore` protocol's stream return type does not include `EffectorCallDto` or `ExecutorCallDto` — only the four kinds above. `JsonlTraceStore` enforces this by writing them to separate files (`effectors/<entity_id>.jsonl`, `executors/<run_id>.jsonl`) that the `_tail` reader does not touch. FEAT-013 preserves the protocol contract verbatim.
- The FEAT-004 streaming endpoint's external behavior is therefore "step / policy_call / webhook_event / operator_signal only." Folding new kinds in would be a contract change, out of scope.
- Effector calls remain readable via `read_effector_calls(entity_id)` — Postgres backend implementation: `SELECT * FROM effector_calls WHERE entity_id = :entity_id ORDER BY created_at, id`.
- Executor calls have no run-keyed reader on the protocol today. If a future feature needs one, that's an additive protocol extension and its own FEAT.

---

## Implications for tests (T-278, T-279, T-280)

This doc is the spec the verification tier enforces.

- **T-278 (byte-identical NDJSON parity)** compares `JsonlTraceStore` and `PostgresTraceStore` output for the same run. The query shape above plus the no-double-write rule together fix the row set the Postgres backend produces; the JSONL backend's output is the golden master. The diff must be empty after `created_at` normalization.
- **T-279 AC-6 (single `AsyncEngine`)** is unaffected by Decisions 1 / 2 / 3 — `PostgresTraceStore` uses the injected `async_sessionmaker`, no new engine.
- **T-279 AC-10 (no SQL model leakage)** is reinforced by Decision 2. The runtime loop continues to depend only on the `TraceStore` protocol; `EffectorCall` / `ExecutorCall` SQL models are imported only by `trace_postgres.py` and the migration. Subprocess guard test follows the FEAT-009 import-quarantine pattern.
- **T-279 AC-7 (cross-backend effector invariant)** runs against both backends. The parameterization stays — Decisions 1 / 2 / 3 don't change the effector-call writer; only the location of the bytes changes.
- **T-280 channel-collision case** is dropped (no `LISTEN`/`NOTIFY` means no channel). Recorded here so the test author doesn't go looking.

## AC-11 implication

Trace history written under `trace_backend = "jsonl"` remains readable under `"jsonl"`. Trace history written under `"postgres"` remains readable under `"postgres"`. The runtime loop never reads its own past trace, so **swap-in-place is safe** — a run can start under one backend and resume under the other without crashing.

**Historical visibility across a backend swap requires `orchestrator migrate-traces`** (T-276). A naive backend flip leaves old JSONL files unreadable through the Postgres-backed endpoint until they are imported. This is documented in the settings reference (T-275) and in the `migrate-traces` CLI help.

## What this doc does **not** decide

- **Retention policy** — lives in T-277 (`trace_retention_days` setting + `trace-retention-sweep` CLI).
- **Index-tuning under production load** — lives in a future ops doc. T-272's indexes (`(entity_id, created_at)`, `(transition_key)`, `(run_id, created_at)`, `(node_name)`) are the v1 starting point; revisiting is post-soak.
- **Multi-worker support** — explicitly deferred. The single-uvicorn-worker constraint from FEAT-002 is preserved by FEAT-013; nothing here unlocks multi-worker, and adopting LISTEN/NOTIFY in the future does not by itself unlock it either (`RunSupervisor` still serializes).
- **Removing `JsonlTraceStore`** — out of scope per the brief. The case for removal lands as a separate FEAT after the Postgres backend has soaked.
- **Cross-run analytics endpoints** — out of scope. Operators query Postgres directly with whatever tooling they already have.

## Cross-links

- Brief: [`docs/work-items/FEAT-013-postgres-trace-store.md`](../work-items/FEAT-013-postgres-trace-store.md)
- Task list: `tasks/FEAT-013-tasks.md`
- AD-5: [`docs/ARCHITECTURE.md` § AD-5](../ARCHITECTURE.md#ad-5--durable-run-state-jsonl-first-then-database)
- CLAUDE.md Patterns entry: *Trace writes go through a protocol*
- Drift-pattern precedent: [`feat-008-engine-as-authority.md`](./feat-008-engine-as-authority.md) — the inverted-writer-direction precedent that informs Decision 2.
- FEAT-004 endpoint contract: [`docs/work-items/FEAT-004-trace-streaming.md`](../work-items/FEAT-004-trace-streaming.md) — preserved byte-for-byte.
