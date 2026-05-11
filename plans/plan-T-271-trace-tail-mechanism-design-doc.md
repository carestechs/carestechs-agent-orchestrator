# Implementation Plan: T-271 — Design doc: trace-tail mechanism + double-write decision

## Task Reference
- **Task ID:** T-271
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-3 (byte-identical NDJSON parity), AC-4 (≤ 1 s follow latency), AC-10 (no drift in record sites). Without the tail decision, T-274 is a guessing exercise; without the no-double-write rule pinned, reviewers re-litigate it on every PR; without the query shape, the parity test in T-279 has no spec to enforce.

## Overview
Pin three decisions before any code lands in FEAT-013: (1) how live-tail follow mode wakes — `LISTEN`/`NOTIFY` vs. polling; (2) the hard rule that `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` are no-op writes in `PostgresTraceStore` (the rows are already written by the runtime / signal adapter — re-writing them re-creates the FEAT-008 drift); (3) the canonical SQL query shape for `open_run_stream`, including filter pushdown and pagination, so the parity test in T-279 has a concrete contract to enforce.

## Implementation Steps

### Step 1: Spike `LISTEN`/`NOTIFY` against the async SQLAlchemy setup
**File:** *no commits — local spike branch only*
**Action:** Investigate

Use the existing `async_sessionmaker` to subscribe to a Postgres channel from inside an `AsyncSession` and observe whether `NOTIFY` events delivered post-commit can be consumed without holding the receiving session open across the wait. Two concrete probes:

1. Open a session, issue `LISTEN trace_run_<uuid>`, drop the session (return the connection to the pool). Issue `NOTIFY trace_run_<uuid>` from a separate session. Does the listener still receive? If no — `LISTEN` requires the listening session to stay open, which collides with the runtime-loop discipline ("each iteration opens its own `AsyncSession`"). That is the disqualifying outcome.
2. If (1) requires a long-lived session, measure whether one dedicated subscriber connection per active tail is acceptable (one extra pooled connection per concurrent follower; v1 has a single uvicorn worker and the supervisor serializes runs, so concurrent followers per run are bounded by client count).

Time-box this to one half-day. If the spike turns into a multi-day rabbit hole, choose polling and record that reason.

**Outcome:** A one-paragraph spike log captured in the design doc under the "Tail mechanism" section, naming the disqualifying or supporting evidence concretely.

### Step 2: Create the design doc skeleton
**File:** `docs/design/feat-013-trace-tail-mechanism.md`
**Action:** Create

Sections:

1. **Status & cross-links.** "Active — supersedes nothing." Link out to FEAT-013 brief, FEAT-004 (the endpoint contract this preserves), FEAT-008 (the drift pattern the no-double-write rule prevents), AD-5 in `ARCHITECTURE.md`.
2. **Decision 1 — Tail mechanism.** Single-valued. Either "polling-only at 200 ms cadence" or "`LISTEN`/`NOTIFY` on channel `trace_run_<uuid>` with the 200 ms polling loop as backstop". The spike log from Step 1 is the rationale block. The fallback rule (brief Section 13: polling-only is acceptable) is quoted verbatim.
3. **Decision 2 — No double-write.** State as a hard invariant: `record_step` / `record_policy_call` / `record_webhook_event` / `record_operator_signal` are no-op writes in `PostgresTraceStore`. The reader joins the existing tables instead. Reference FEAT-008's effector-vs-inline-aux-write drift as the cautionary tale; cite the CLAUDE.md anti-pattern entry "Don't add a parallel persistence surface for engine round-trips."
4. **Decision 3 — `open_run_stream` query shape.** Spell out the canonical SQL:
   - `UNION ALL` over six sources: `steps`, `policy_calls`, `webhook_events`, `run_signals`, `effector_calls` (filtered by linkable `run_id` join — see edge cases), `executor_calls`.
   - Each branch carries a `kind` literal column so the union row is self-describing without a downstream lookup.
   - Filter pushdown: `?since=` becomes `created_at > :since` *inside each branch*, not on the union. `?kinds=` selects which branches to emit at all — branches the filter excludes are not selected from. This avoids the planner reading rows the caller never sees.
   - Ordering: `ORDER BY created_at ASC, id ASC` on the outer union (tie-break by `id` for determinism within the same microsecond).
   - Pagination chunk size: 1000 rows per iteration; the tail reader uses this same chunk size when draining backlog before entering the follow loop.
5. **Implications for tests.** Name the three guard tests T-279 will produce: AC-6 single-engine, AC-10 no model leakage, AC-7 cross-backend effector invariant. The doc is the spec these tests enforce.
6. **Edge case — `effector_calls` and `run_id`.** Effector calls are keyed on `entity_id`, not `run_id`. The `open_run_stream(run_id)` query for the `effector_calls` branch joins on whichever table carries the `entity_id` ↔ `run_id` link (work items / tasks). Specify the join shape here so T-273's implementation has no ambiguity.
7. **AC-11 implication.** Trace history written under one backend remains readable under itself but is **not** migrated implicitly across a runtime swap. The runtime loop does not read its own past trace, so swap-in-place is safe but historical visibility requires the `migrate-traces` CLI (T-276).
8. **Out of scope for this doc.** Retention policy lives in T-277. Index-tuning under load lives in a future ops doc. Multi-worker LISTEN/NOTIFY is explicitly deferred — single-worker is preserved.

### Step 3: Cross-link from `CLAUDE.md`
**File:** `CLAUDE.md`
**Action:** Modify

Update the existing "Trace writes go through a protocol" entry in **Patterns to Follow** to add a forward-link to `docs/design/feat-013-trace-tail-mechanism.md`. Do not rewrite the entry yet — that happens in T-281 when the implementation lands. Single sentence appended: "See `docs/design/feat-013-trace-tail-mechanism.md` for the Postgres backend's query shape and tail-mechanism rationale."

### Step 4: Cross-link from `docs/ARCHITECTURE.md`
**File:** `docs/ARCHITECTURE.md`
**Action:** Modify

In the AD-5 row, append a forward-link: "Active design doc for the v2 migration: `docs/design/feat-013-trace-tail-mechanism.md`." Do **not** flip the AD-5 status to "implemented" here — that's T-281's job after the code lands.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/design/feat-013-trace-tail-mechanism.md` | Create | Three decisions + spike log + cross-links. |
| `CLAUDE.md` | Modify | Forward-link from the trace-protocol pattern entry. |
| `docs/ARCHITECTURE.md` | Modify | Forward-link from the AD-5 row. |

## Edge Cases & Risks
- **Spike inconclusive.** If the `LISTEN`/`NOTIFY` probe is ambiguous after the half-day time-box, pick polling-only. The brief permits it. Record the time-box outcome verbatim — "ambiguous, defaulted to polling" — so future readers know it wasn't a no-investigation choice.
- **`effector_calls` join ambiguity.** Effector traces key on `entity_id` (work item / task UUID), not `run_id`. If the join through the lifecycle tables to recover `run_id` is non-trivial, document the join shape explicitly; do not punt to T-273. T-273 should be code-only, not design-discovery.
- **Doc scope creep.** Resist the temptation to spec retention, indexing, or multi-worker semantics here. Each has its own task or is explicitly excluded from the FEAT.
- **JSONL writer format drift during the soak.** If FEAT-004 or any downstream consumer changes the JSONL format between this doc landing and T-278 running, the parity bar moves. Flag this risk in the doc and in the PR description.

## Acceptance Verification
- [ ] **AC-1 (tail mechanism single-valued):** The "Tail mechanism" section names exactly one of polling-only or LISTEN/NOTIFY+polling-backstop, with the spike log as rationale.
- [ ] **AC-2 (no-double-write rule pinned):** The "No double-write" section states the rule as a hard invariant and cites FEAT-008 as the analogous drift pattern.
- [ ] **AC-3 (query shape spelled out):** The "`open_run_stream` query shape" section names all six sources, the filter pushdown rule, the ordering, the pagination chunk size, and the `effector_calls` join.
- [ ] **AC-4 (cross-links present):** `CLAUDE.md` Patterns entry and `docs/ARCHITECTURE.md` AD-5 row both link forward to the new doc.
- [ ] **AC-5 (AC-11 implication noted):** Section explicitly states that runtime swap is safe but historical visibility requires `migrate-traces`.
