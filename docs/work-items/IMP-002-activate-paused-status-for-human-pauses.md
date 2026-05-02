# Improvement Proposal: IMP-002 — Activate `RunStatus.PAUSED` for human-mode dispatches

> **Purpose**: Make `Run.status` honestly reflect when the orchestrator is blocked on an external party versus when it's actively driving. `RunStatus.PAUSED` exists in the enum (`enums.py:13`) but is never written; today every wait — engine webhook, remote callback, multi-day human handoff — looks identical from the run's status field. This conflation hides real signal from observability and forces every consumer to re-derive "is this run idle?" from `Dispatch` rows.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-002 |
| **Name** | Activate `RunStatus.PAUSED` for human-mode dispatches |
| **Type** | Maintainability · Observability |
| **Status** | Proposed |
| **Priority** | High |
| **Proposed By** | Live `lifecycle-agent@0.3.0` run, operator diagnosis (`request_implementation` parked invisibly until 600s timeout) |
| **Date Created** | 2026-05-02 |

---

## 2. Target Area

**Component / Module:** `modules/ai` — runtime loop + run-state surface.

**Affected Files / Directories:**
- `src/app/modules/ai/runtime_deterministic.py` (the dispatch-await leg)
- `src/app/modules/ai/enums.py` (no change — `PAUSED` already defined)
- `src/app/modules/ai/service.py` (cancel path — already tolerant; verify)
- `src/app/modules/ai/schemas.py` (RunDto — already exposes status; no shape change)
- `tests/modules/ai/test_runtime_human_pause.py` (new)

---

## 3. Current State

### How It Works Today

The deterministic runtime (`runtime_deterministic.py:556`) flips `Run.status = RUNNING` once at the top of the loop. Every dispatch — engine, remote, human — then runs `executor.dispatch()` and (when non-terminal) `await supervisor.await_dispatch(dispatch_id)`. The run stays at `running` for the entire wait, regardless of whether that wait is a 200 ms engine webhook or a multi-day human handoff. `RunStatus.PAUSED` is defined in the enum but no writer ever sets it.

### Problems

1. **Run-level status conflates two structurally different waits.** "Loop is making progress and an internal callback is imminent" and "loop is parked indefinitely waiting on a person" are the same value (`running`). Operationally these are not the same thing — the first should alert if it lingers; the second is normal up to days.
2. **No observable signal that work is owed by an external party.** External pollers, future Slack/email effectors, and operator dashboards have no first-class way to ask "is this run waiting on a human?" without joining `Dispatch` rows by mode and state.
3. **`PAUSED` is dead enum-letter.** Defined for exactly this case, never used. Either activate it or drop it; today's silence is the worst of both.
4. **Knock-on: `dispatch_timeout_seconds` (default 600 s) kills legitimate human pauses.** The status flag isn't the fix for this, but the conflation is *why* the timeout policy can't differentiate. A status of `paused` makes a mode-aware timeout policy a small follow-on instead of a guess.

### Evidence

- Live `lifecycle-agent@0.3.0` run on 2026-05-02: `request_implementation` parked at `HumanExecutor.dispatch()` for 600 s with `Run.status='running'` the entire time, then synthesized a timeout failure (`runtime_deterministic.py:340`). No external observer could tell the run was waiting for an operator action.
- `enums.py:13` defines `RUNNING` adjacent to `PAUSED`; grep confirms `PAUSED` has zero writers across the codebase.

---

## 4. Desired State

### Target Implementation

When the runtime parks on a `mode=human` dispatch, flip `Run.status` to `PAUSED` *before* awaiting the supervisor future, and back to `RUNNING` after the future resolves (success, failure, or timeout). The flip lives in the runtime — not in `HumanExecutor` — so the executor stays a passive descriptor, matching every other executor in the registry.

### Benefits

1. **Honest run-state machine.** `running` means "loop is driving"; `paused` means "loop is blocked on an external party"; the terminal triple stays the terminal triple.
2. **Observability without joins.** A run waiting on a human is now a single-column query (`status='paused'`); pollers, dashboards, and future effectors can build on that without dispatch-row inspection.
3. **Activates dead enum-letter** with no schema migration (`Run.status` is already `String` per the model; values are validated at the application layer via `RunStatus`).
4. **Sets up mode-aware timeout policy as a small follow-on** rather than a guess. (Out of scope for this IMP — see §6.)

---

## 5. Trigger and Motivation

**Trigger:** Live operator session for `lifecycle-agent@0.3.0` on 2026-05-02. After the Composite/Sequence wake-race split-tx fix (PR #69) cleared `generate_plan` and `approve_plan`, the run advanced into `request_implementation` (a `HumanExecutor` pause) and timed out invisibly at 600 s. No status flag indicated to the operator that work was owed back.

**Impact if deferred:** Every human-pause node (`request_implementation` today; review-gate handoffs in any future agent) will keep timing out on the default dispatch deadline. We'd paper over it by raising the timeout, which makes the "stuck run" detection problem worse. The status-flag fix is a precondition for thinking honestly about pause-aware timeouts.

**Dependencies on this improvement:**
- Mode-aware `dispatch_timeout_seconds` policy (separate work item — likely a follow-on IMP).
- Outbound notification effector for human pauses (separate; the status flag is the read side, not the push side).

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `Run` (data-model) | `status` may now legally hold `paused` mid-run; no schema change | `docs/data-model.md` — Run entity |
| `runtime_deterministic.py` | Adds a status-flip wrapper around the human-mode `await_dispatch` leg | `CLAUDE.md` — Runtime Loop |
| `RunDto` (api-spec) | Already exposes `status`; documentation note that `paused` is now a live value | `docs/api-spec.md` — runs response |

No new endpoint, no new column, no migration. `_TERMINAL_STATUSES` is unchanged (`paused` is non-terminal — that's the point).

---

## 7. Risk Assessment

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A consumer filters "active runs" by `status='running'` and silently drops paused runs | Medium | Medium | Audit: `service.list_runs`, trace stream filter, any UI/CLI list path. The terminal-set check in `service.py` is `_TERMINAL_STATUSES`, which is correct already. |
| Cancel path mis-handles `paused` | Low | High | `service.cancel_run` checks `status in _TERMINAL_STATUSES` (paused is not terminal → cancellable). Verified by reading `service.py:241`. Add explicit cancel-while-paused test. |
| Trace stream `?follow=true` closes prematurely on `paused` | Low | Medium | Stream closes on terminal-only; `paused` is non-terminal. Verified by reading the trace store impl. Add explicit test that follow-mode keeps streaming through a paused interval. |
| Race: future resolves between the PAUSED flip and the await registration | Low | Low | Flip happens *before* `await_dispatch` in the same coroutine; the supervisor future hasn't been awaited yet so resolution is queued, not lost. The flip-back happens in `finally`, covering all exit paths (resolution, timeout, exception). |
| `Run.status` flicker if many human dispatches in a row | Very Low | Low | Each human dispatch flips paused→running on resume → next iteration may flip again. Honest — the loop *is* running between dispatches. Acceptable. |

### Rollback Strategy

Single-file revert of `runtime_deterministic.py`. No data migration to undo. Any rows that ended up with `status='paused'` and a terminated dispatch are normalized by the existing run-resume guard at the top of the loop (it forces RUNNING on entry).

---

## 8. Constraints

- The flip must live in the runtime, **not** in `HumanExecutor`. Executors are passive descriptors; the runtime owns run-status transitions. (CLAUDE.md: "Service layer owns logic.")
- No new column, no schema migration. `status` is already free-text validated at the app layer.
- Must work under restart: if the orchestrator dies while a run is `paused`, the next cold-start should leave it paused (not auto-promote to running) — the supervisor restart path already handles dispatch-timeout reconciliation; the status flip should not break that contract.
- Must not import anything that breaks `tests/test_runtime_deterministic_is_pure.py` (no `core.llm`, no executor handler modules).

---

## 9. Success Criteria

- A live `lifecycle-agent@0.3.0` run that reaches `request_implementation` shows `Run.status='paused'` for the duration of the wait, observable via `GET /api/v1/runs/{id}`.
- After `POST /api/v1/runs/{id}/signals` with `name=implementation-complete`, `Run.status` returns to `running` and the run advances to `submit_implementation`.
- A unit test exercises the full flip cycle (`running → paused → running → completed`) without hitting a real engine.
- Cancellation of a paused run terminates it correctly (`status='cancelled'`, no orphaned dispatch).
- `dispatch_timeout_seconds` still fires if a human dispatch genuinely exceeds its bound — paused does not mean "exempt from timeout" in this IMP. (Mode-aware timeout is a follow-on.)

---

## 10. Current Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| `HumanExecutor.dispatch` | Unit | Returns dispatched envelope; no run-status assertion. |
| Runtime dispatch-await leg | Integration | `tests/integration/test_runtime_intake_threads_task_id.py` covers local-mode flow; no human-pause coverage. |
| Run-status transitions | Indirect | `test_run_status_transitions` covers terminal mappings (`StopReason → RunStatus`); no in-flight transitions. |
| Cancel path | Unit | `test_cancel_run` covers cancel-while-running; no cancel-while-paused. |

Gap: no test today asserts what `Run.status` is *during* a non-terminal wait. This IMP introduces that assertion.

---

## 11. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | Live `lifecycle-agent@0.3.0` run, operator session, 2026-05-02 |
| **Stakeholder Alignment** | AD-2 (durable, observable runs) — operator must be able to tell at a glance whether a run is making progress or owed external action |
| **Architecture Reference** | `docs/ARCHITECTURE.md` — Runtime loop; `CLAUDE.md` — Runtime Loop section |
| **Related Work Items** | FEAT-002 (runtime loop), FEAT-009 (executor seam), FEAT-010 (engine executor) |
| **Blocked Features** | Mode-aware dispatch timeout (future IMP); outbound human-pause notifications (future FEAT) |
