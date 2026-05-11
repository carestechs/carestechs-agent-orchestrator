# FEAT-013 — Postgres trace store (AD-5 v2 migration)

> **Source:** `docs/work-items/FEAT-013-postgres-trace-store.md`
> **Status:** Not Started
> **Target version:** v0.8.0

FEAT-013 closes out AD-5's deferred v2 migration: the `TraceStore` protocol's production backend becomes Postgres while `JsonlTraceStore` stays as an opt-in local-dev backend. The runtime loop, FastAPI dependency tree, CLI, and the FEAT-004 NDJSON streaming endpoint do not change — this is a composition-root swap behind the seam. Two new tables (`effector_calls`, `executor_calls`) cover the only kinds not already in SQL; the four kinds that *are* (`step` / `policy_call` / `webhook_event` / `run_signal`) become read-only joins in the new store, with the protocol's `record_*` methods becoming no-op writes to avoid double-write drift. Live tail is polling-first with `LISTEN`/`NOTIFY` as a gated stretch goal.

The numbering picks up at **T-271** (IMP-003 ended at T-270).

---

## Foundation

### T-271: Design doc — trace-tail mechanism + double-write decision

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Write `docs/design/feat-013-trace-tail-mechanism.md`. Three load-bearing decisions to pin in writing before any code lands:

1. **Tail mechanism.** Spike `LISTEN`/`NOTIFY` against the existing asyncpg-backed SQLAlchemy async setup. Decide one of: (a) `LISTEN`/`NOTIFY` with channel `trace_run_<uuid>`, NOTIFY fired post-commit by the writer; (b) polling-only at 200 ms cadence (same as JSONL today). The brief Section 13 explicitly permits (b) as an acceptable outcome — if the spike adds more than ~1 day of integration work or complicates the test harness, choose (b). Document the spike outcome with a concrete reproducer either way.
2. **No double-write policy.** Pin in writing: `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` are *no-op writes* in `PostgresTraceStore`. The reader reconstructs those kinds by joining `steps` / `policy_calls` / `webhook_events` / `run_signals`. Explain why a second write path here re-creates the FEAT-008-shaped drift problem.
3. **`open_run_stream` query shape.** Pin the canonical query: `UNION ALL` over the six sources filtered on `run_id`, ordered by `created_at`. Decide pagination chunk size (default 1000) and how `since` / `kinds` push down to each branch (predicate per branch, not post-filter in Python).

**Rationale:**
AC-3 (byte-identical NDJSON parity), AC-4 (≤ 1 s follow latency), AC-10 (no drift in record sites). Without the tail decision, T-274 is a guessing exercise; without the no-double-write rule pinned, reviewers re-litigate it on every PR; without the query shape, the parity test in T-279 has no spec to enforce.

**Acceptance Criteria:**
- [ ] Tail mechanism decision is **single-valued** with rationale and a concrete reproducer (or spike log).
- [ ] No-double-write rule is stated as a hard invariant with the FEAT-008 cross-link explaining the drift it prevents.
- [ ] Query shape for `open_run_stream` is spelled out: source list, ordering, filter pushdown, pagination chunk size.
- [ ] Cross-linked from `CLAUDE.md` Patterns ("Trace writes go through a protocol") and `docs/ARCHITECTURE.md` AD-5.
- [ ] AC-11 implication noted: trace history written under one backend remains readable under itself but is **not** migrated implicitly across a runtime swap.

**Files to Modify/Create:**
- `docs/design/feat-013-trace-tail-mechanism.md` — new.

---

### T-272: Schema — `effector_calls` + `executor_calls` tables (forward-only migration)

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-271

**Description:**
Add two SQLAlchemy models in `src/app/modules/ai/models.py` and a forward-only Alembic migration. Both tables are append-only:

- `effector_calls`: `id BIGSERIAL PRIMARY KEY`, `entity_id UUID NOT NULL`, `transition_key TEXT NOT NULL`, `payload JSONB NOT NULL`, `outcome TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Indexes: `(entity_id, created_at)`, `(transition_key)`.
- `executor_calls`: `id BIGSERIAL PRIMARY KEY`, `run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE`, `node_name TEXT NOT NULL`, `dispatch_id UUID NULL`, `payload JSONB NOT NULL`, `outcome TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Indexes: `(run_id, created_at)`, `(node_name)`.

No `updated_at` — trace rows are immutable by contract. The migration must be forward-only; downgrade is `DROP TABLE` and documented as destructive.

**Rationale:**
AC-2, AC-7. These two kinds are the only trace kinds not already modeled in SQL today; landing them first means `PostgresTraceStore` (T-273) has a place to write before the swap.

**Acceptance Criteria:**
- [ ] Two SQLAlchemy models registered against `Base`; type-checked under strict pyright.
- [ ] Alembic migration applies cleanly on a fresh database and on a database already at the prior head.
- [ ] Indexes match the spec; `(entity_id, created_at)` and `(run_id, created_at)` are composite to support reader-side filtering and ordering in one index walk.
- [ ] Downgrade docstring states "destructive — production runs do not roll back."
- [ ] Forward-only is enforced by the migration content (downgrade body raises `NotImplementedError` or `op.execute("...")` drops tables with a comment).

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — add `EffectorCall`, `ExecutorCall`.
- `src/app/migrations/versions/2026_05_XX_feat_013_trace_tables.py` — new Alembic revision.

**Technical Notes:**
Per CLAUDE.md: snake_case plural table names; snake_case columns; descriptive revision slug. Use `BIGSERIAL` not `SERIAL` — trace volume per run is unbounded.

---

## Backend — store implementation

### T-273: `PostgresTraceStore` — writes, reads, and read-only joins

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-272

**Description:**
Introduce `src/app/modules/ai/trace_postgres.py` — `PostgresTraceStore` implementing the full `TraceStore` protocol surface defined in `trace.py`:

- `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` — **no-op** (documented inline; the row is already persisted by the runtime / signal adapter). Method signatures preserved for protocol compatibility.
- `record_effector_call(dto)` / `record_executor_call(dto)` — insert + commit a row in a short-lived session opened from the injected `async_sessionmaker`. Never holds a session across calls; never reuses one.
- `read_effector_calls(entity_id)` — `SELECT ... WHERE entity_id = ? ORDER BY created_at` → list of `EffectorCallDto`.
- `open_run_stream(run_id)` — one-shot drain via the `UNION ALL` over six sources pinned in T-271's design doc; respects `since` and `kinds` predicates; pages in 1000-row chunks.

Constructor signature: `PostgresTraceStore(session_factory: async_sessionmaker[AsyncSession])`. No new engine, no new pool — reuses the app's `async_sessionmaker`.

**Rationale:**
AC-3, AC-5, AC-6, AC-10. The store is the seam; everything else in FEAT-013 either feeds it (T-272, T-275) or verifies it (T-279, T-280).

**Acceptance Criteria:**
- [ ] All six `record_*` / `read_*` / `open_run_stream` methods implemented; `record_step` & siblings carry an inline comment naming the writer that owns the row.
- [ ] Unit tests cover each method against a real Postgres fixture (no SQLite, per CLAUDE.md).
- [ ] Reader emits DTOs in `created_at` order across all six sources; ties broken by `id` for determinism.
- [ ] Reader never holds a session across an iterator boundary — each chunk opens its own.
- [ ] `?since=` and `?kinds=` filters push down to SQL `WHERE` clauses on every source, not into Python post-filtering.

**Files to Modify/Create:**
- `src/app/modules/ai/trace_postgres.py` — new.
- `tests/modules/ai/test_trace_postgres.py` — new.

**Technical Notes:**
The `Step` / `PolicyCall` / `WebhookEvent` / `RunSignal` → DTO mapping must produce *byte-identical* NDJSON to the JSONL writer's output for the same logical row. The parity test in T-279 enforces this; if you find yourself reaching for "close enough" JSON, fix the mapper.

---

### T-274: Live-tail — polling reader (LISTEN/NOTIFY gated by T-271 outcome)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-273

**Description:**
Implement `PostgresTraceStore.tail_run_stream(run_id, follow=True, since=None, kinds=None)`. Default implementation: polling at 200 ms cadence using `created_at > last_seen` as the high-water mark; loop exits when the owning `Run.status` becomes terminal. If T-271's design doc picks `LISTEN`/`NOTIFY`, additionally subscribe to `trace_run_<uuid>` and use the notification as a wake signal (with the 200 ms polling loop as a backstop so a dropped notification can't stall the stream beyond one tick).

The tail reader **must not** hold a session across the lifetime of the stream — each poll opens its own short session from `session_factory` (the same discipline `runtime_deterministic.py` uses).

**Rationale:**
AC-4 (≤ 1 s follow latency), AC-5 (filter parity). Pulled out of T-273 because the tail is the only piece with a real performance bar and a stretch optimization gate.

**Acceptance Criteria:**
- [ ] Follow mode delivers a new entry to a connected client within ≤ 1 s of commit; verified by an integration test that drives a slow producer and times client receipt.
- [ ] Polling reader closes the stream cleanly when the owning run reaches a terminal state.
- [ ] If `LISTEN`/`NOTIFY` is enabled, the polling loop runs as backstop; dropping the notification connection mid-stream must not hang the client (verified by a test that kills the LISTEN side and asserts the next poll picks up the missed row within one tick).
- [ ] Channel cleanup (`UNLISTEN`) on stream close is in a `finally`.
- [ ] Empty-result `?since=now` follow stream blocks correctly until first row or terminal state.

**Files to Modify/Create:**
- `src/app/modules/ai/trace_postgres.py` — add `tail_run_stream` + optional NOTIFY emission in `record_effector_call` / `record_executor_call`.
- `tests/modules/ai/test_trace_postgres_tail.py` — new.

**Technical Notes:**
The NOTIFY payload should carry no body — readers re-read from SQL with the high-water mark. Payload-as-truth re-creates a parallel-write problem.

---

### T-275: Backend selector — register `postgres` and flip the default

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-273

**Description:**
- Extend `settings.trace_backend` in `src/app/config.py` from `Literal["noop", "jsonl"]` to `Literal["noop", "jsonl", "postgres"]`. Default flips from `"jsonl"` to `"postgres"` at the production layer; CI test config continues to default to `"noop"`.
- Extend `get_trace_store()` in `src/app/modules/ai/trace.py` with a `"postgres"` branch that constructs `PostgresTraceStore(session_factory=...)`. The cached singleton mechanism is preserved.
- Verify by test that toggling `trace_backend` does not construct a second `AsyncEngine`.

**Rationale:**
AC-1, AC-6. The smallest possible diff at the composition root; isolated so a misbehaving backend is one line away from a revert during the soak window.

**Acceptance Criteria:**
- [ ] `Literal` widens; `mypy`/`pyright` clean.
- [ ] `get_trace_store()` returns a `PostgresTraceStore` instance when `trace_backend == "postgres"`.
- [ ] Production default is `"postgres"`; documented in the settings reference comment.
- [ ] Startup test asserts exactly one `AsyncEngine` is constructed regardless of `trace_backend`.
- [ ] `"jsonl"` and `"noop"` paths continue to pass their existing tests unchanged.

**Files to Modify/Create:**
- `src/app/config.py` — widen `Literal`, flip default.
- `src/app/modules/ai/trace.py` — add `postgres` branch in `get_trace_store`.
- `tests/test_config.py` — assert the new default and validator.
- `tests/test_trace_singleton.py` — assert single-engine invariant.

---

## CLI

### T-276: `orchestrator migrate-traces` — one-shot JSONL → Postgres importer

**Type:** Backend (CLI)
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-273

**Description:**
Add a Typer command `migrate-traces` to `src/app/cli.py`:

```
uv run orchestrator migrate-traces --from=jsonl --since=<ISO-8601> [--dry-run]
```

Scans `settings.trace_dir`, parses each JSONL line through the existing DTO discriminator used by the streaming reader, and inserts the row into the matching Postgres table. **Idempotent on `(run_id, kind, created_at, payload_hash)`** — second runs log "skipped N already-present entries" and exit 0. `--dry-run` reports row counts per kind and writes nothing.

The command does **not** write to `steps` / `policy_calls` / `webhook_events` / `run_signals` — those rows already exist; only `effector_calls` and `executor_calls` need import. For the JSONL lines mapping to the already-persisted kinds, the command must verify a corresponding row exists in SQL and log a divergence count if not (read-only check; never inserts).

**Rationale:**
AC-8. Closes the cutover window for in-flight production traces without forcing a clean cut.

**Acceptance Criteria:**
- [ ] Twice-run produces identical Postgres state; second run logs the skip count.
- [ ] `--dry-run` writes nothing (assertion: no rows inserted, no `NOTIFY` emitted).
- [ ] Divergence check (JSONL line for `step`/`policy_call`/`webhook_event`/`run_signal` whose SQL row is missing) is reported, not papered over.
- [ ] Malformed JSONL lines are logged with line + file and skipped, not crash-the-import.
- [ ] CLI exit code is non-zero only on configuration errors, not on divergence/skip findings.

**Files to Modify/Create:**
- `src/app/cli.py` — new command.
- `src/app/modules/ai/trace_migrate.py` — pure-function importer (called by the CLI).
- `tests/modules/ai/test_trace_migrate.py` — new.

**Technical Notes:**
Don't generalize — this is one-shot tooling and will be deleted in a future cleanup. No "live tail" mode, no continuous-import flag.

---

### T-277: `orchestrator trace-retention-sweep` + `trace_retention_days` setting

**Type:** Backend (CLI)
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-272

**Description:**
- Add `trace_retention_days: int | None = None` to `settings`. `None` (default) disables retention — preserves current behavior.
- Add `trace-retention-sweep` Typer command. When `trace_retention_days` is set, `DELETE FROM <table> WHERE created_at < now() - interval '<N> days'` is issued against `effector_calls` and `executor_calls` only (the four engine-mode tables retain their own ops policies). `--dry-run` reports counts per kind and writes nothing.
- The command is wired by operators to their scheduler (cron / systemd timer / `CronJob`). No in-process scheduler.

**Rationale:**
AC-9. Operators need retention without dragging APScheduler/Celery into v1.

**Acceptance Criteria:**
- [ ] With `trace_retention_days=None`, the command exits with "retention disabled" and writes nothing.
- [ ] With `trace_retention_days=N`, `--dry-run` reports the counts that would be deleted per kind.
- [ ] Without `--dry-run`, rows older than the threshold are deleted; a second sweep on unchanged data is a no-op.
- [ ] Documented in the settings reference comment.

**Files to Modify/Create:**
- `src/app/config.py` — add `trace_retention_days`.
- `src/app/cli.py` — new command.
- `tests/modules/ai/test_trace_retention.py` — new.

---

## Integration & verification

### T-278: Byte-identical NDJSON parity — side-by-side integration test

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-273, T-275

**Description:**
Drive one canonical end-to-end run under `trace_backend="jsonl"`, then drive the same run (via fixture replay or repeated execution with stubbed time/UUIDs) under `trace_backend="postgres"`. `GET /api/v1/runs/{id}/trace` is invoked against both; the streamed bytes are normalized (only `created_at` resolution — TIMESTAMPTZ vs. ISO-string round-trip — is allowed to differ) and compared byte-for-byte. Repeat for the `?follow=true` path.

Also covers the CLI parity check from the brief: `orchestrator runs trace <id>` output is compared the same way.

**Rationale:**
AC-3 ("byte-identical NDJSON" is the FEAT's most load-bearing claim — operators have scripts grepping the existing output). Anything less than a byte-level test will let a regression through.

**Acceptance Criteria:**
- [ ] Test runs against a real Postgres fixture under both backends in one invocation.
- [ ] Diff failure prints the first differing line with a 5-line context window.
- [ ] Test covers all six trace kinds and at least one `?since=` + `?kind=` filter combination.
- [ ] CLI byte-comparison covers the same kinds.
- [ ] Test is marked under the integration tier (`tests/integration/`).

**Files to Modify/Create:**
- `tests/integration/test_trace_backend_parity.py` — new.

---

### T-279: Structural guard tests — single engine, no model leakage, effector invariant cross-backend

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-275

**Description:**
Three structural-invariant tests, following the FEAT-009 / FEAT-010 import-quarantine subprocess pattern in `tests/test_runtime_deterministic_is_pure.py` and `tests/test_engine_executor_is_isolated.py`:

1. **AC-6:** assert exactly one `AsyncEngine` is constructed during app startup regardless of `trace_backend`. Subprocess test that monkey-patches `create_async_engine` to count.
2. **AC-10:** assert no module outside `src/app/modules/ai/trace_postgres.py` and `tests/` imports the `EffectorCall` or `ExecutorCall` SQL models. Subprocess that imports `runtime.py`, `runtime_deterministic.py`, `service.py` and asserts the symbols are not in `sys.modules`-visible references.
3. **AC-7 (cross-backend):** the FEAT-008 / T-172 effector-trace invariant test is parameterized over both `JsonlTraceStore` and `PostgresTraceStore`; every declared transition either produced an `effector_call` row/line or carries a `no_effector` exemption under both.

**Rationale:**
AC-6, AC-7, AC-10. The structural guards are how this FEAT defends itself against drift in the next six months.

**Acceptance Criteria:**
- [ ] All three tests live under `tests/` and pass under CI's default backend (`noop`) plus the integration tier (`postgres`).
- [ ] AC-10 guard fails loudly with a list of offending import sites when violated.
- [ ] Cross-backend invariant uses one shared test body parameterized on the backend fixture — no copy-paste.

**Files to Modify/Create:**
- `tests/test_trace_single_engine.py` — new (AC-6).
- `tests/test_trace_postgres_is_isolated.py` — new (AC-10).
- `tests/integration/test_effector_invariant_cross_backend.py` — modify (parameterize the existing T-172 test).

---

### T-280: Backend-swap resilience + edge cases

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-273, T-275

**Description:**
Cover the remaining edge cases from the brief Section 9 + AC-11:

- A run started under `trace_backend="jsonl"` and resumed under `trace_backend="postgres"` (and the reverse) does not crash — the runtime loop never reads its own past trace, so swap-in-place is safe.
- Concurrent writer + tailing reader on the same run (stress test): no torn reads under polling and under LISTEN/NOTIFY (if enabled).
- Run with no trace rows yet: non-follow returns empty stream + clean EOF; follow blocks until first row or terminal state.
- `?since=` parses TIMESTAMPTZ on the server (no client-side time math).
- Channel-collision test (only if LISTEN/NOTIFY enabled): two concurrent runs do not see each other's notifications.

**Rationale:**
AC-4, AC-11, brief Section 9. Pulled to its own task because each is a small integration test and bundling with T-278 obscured the parity bar.

**Acceptance Criteria:**
- [ ] Five tests, each with a one-line title that names the edge case it covers.
- [ ] All pass under the integration tier.
- [ ] LISTEN/NOTIFY tests are gated behind the T-271 decision flag — if polling-only, those tests are skipped with a recorded reason, not deleted.

**Files to Modify/Create:**
- `tests/integration/test_trace_edge_cases.py` — new.

---

## Documentation

### T-281: Docs sweep — `data-model.md`, `ARCHITECTURE.md` AD-5, `CLAUDE.md` patterns

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-272, T-275

**Description:**
- `docs/data-model.md`: add `EffectorCall` and `ExecutorCall` entities with field tables; changelog entry at the bottom.
- `docs/ARCHITECTURE.md`: update the AD-5 row to "Status: implemented (FEAT-013)" and note that `JsonlTraceStore` remains as opt-in for local dev; changelog entry.
- `CLAUDE.md` Patterns: update the existing "Trace writes go through a protocol" entry to read "`PostgresTraceStore` is the default backend, `JsonlTraceStore` is local-dev, `NoopTraceStore` is the test default. The runtime loop never imports either implementation directly — only the protocol."
- Update `docs/work-items/FEAT-013-postgres-trace-store.md` Status: "Completed" when this task lands.

**Rationale:**
CLAUDE.md "Documentation Maintenance Discipline" — data-model, architecture, and CLAUDE.md must move with the code in the same PR.

**Acceptance Criteria:**
- [ ] All three documents updated.
- [ ] Changelog entries on `data-model.md` and `ARCHITECTURE.md`.
- [ ] FEAT-013 brief status updated.
- [ ] No reference to "AD-5 v1 (JSONL-first)" remains as an active commitment — it's now the implemented baseline.

**Files to Modify/Create:**
- `docs/data-model.md` — modify.
- `docs/ARCHITECTURE.md` — modify.
- `CLAUDE.md` — modify.
- `docs/work-items/FEAT-013-postgres-trace-store.md` — modify.

---

## Summary

**Task count by type:**
- Backend: 5 (T-273, T-274, T-275, T-276, T-277)
- Database: 1 (T-272)
- Testing: 3 (T-278, T-279, T-280)
- Documentation: 2 (T-271, T-281)

**Complexity distribution:** S × 4, M × 5, L × 1 (T-273), XL × 0.

**Critical path:** T-271 → T-272 → T-273 → T-275 → T-278 → T-281 (six tasks; the rest hang off T-273 or T-275 and parallelize).

**Risks / open questions:**
- LISTEN/NOTIFY interplay with our async SQLAlchemy session factory is unverified — T-271's spike resolves it. The brief explicitly permits polling-only as the acceptable fallback.
- The "byte-identical NDJSON" bar (AC-3 / T-278) depends on the JSONL writer's current output being stable across the soak window. If FEAT-004 or a downstream consumer changes the JSONL format mid-flight, T-278's golden master must be rebuilt — flag this in PR descriptions.
- `migrate-traces` (T-276) is one-shot tooling. If we find ourselves adding flags ("watch", "tail", "incremental"), we are reinventing the streaming backend — push back on scope.
