# Bug Report: BUG-011 — Signal dedupe key gates the human-dispatch wake

> **Purpose**: The signal endpoint's audit-trail dedupe (idempotency on `(run_id, name, task_id)`) was *also* gating the wake-up of the in-flight human dispatch. Second iterations of the same human-pause node (e.g. `request_implementation` after a correction-loop rejection) re-sent the same signal name+task_id, hit `alreadyReceived=true`, and the new active dispatch never woke. Filed and resolved in the same PR.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-011 |
| **Summary** | `service.deliver_signal_for_run` only fired the wake mechanisms (`supervisor.deliver_signal` + `_deliver_to_human_dispatch`) inside the `if created:` branch. When an iterative flow parks the same human-pause node twice with the same `(name, task_id)`, the second POST returned 202 with `meta.alreadyReceived=true` but never woke the second iteration's active dispatch — the run hung indefinitely. |
| **Severity** | High (blocks every correction-loop / multi-iteration human-pause flow on v0.3.0) |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run on 2026-05-02. Step 12 (`request_implementation`, second iteration of the correction loop after BUG-010 unblocked the review→correct→loop flow) parked indefinitely on a duplicate `implementation-complete` signal. |
| **Date Reported** | 2026-05-02 |
| **Related** | FEAT-005 (introduced the `RunSignal` dedupe contract), FEAT-009/T-217 (added `_deliver_to_human_dispatch`), BUG-010 (fixed the upstream namespace bugs that exposed this) |

---

## 2. Steps to Reproduce

1. Start `lifecycle-agent@0.3.0`. Advance to `request_implementation` (step 8).
2. `POST /api/v1/runs/{id}/signals` with `{"name": "implementation-complete", "taskId": "T-001"}`. → 202, `created=true`, dispatch wakes.
3. Run advances `submit_implementation` → `review_implementation` (verdict=fail) → `correct_implementation` → loops back to `request_implementation` (step 12, second iteration).
4. **`POST /api/v1/runs/{id}/signals` with the same body.** → 202, `meta.alreadyReceived=true`.
5. **Observe:** `Run.status='paused'` indefinitely. `Dispatch.state='dispatched'` for the step-12 row never flips. No further log lines from the runtime. Eventually the dispatch deadline fires.

---

## 3. Root Cause

`src/app/modules/ai/service.py:321-338` (pre-fix):

```python
if created:
    try:
        await trace.record_operator_signal(run_id, dto)
    except Exception:
        ...
    supervisor.deliver_signal(run_id, name, task_id, payload)
    await _deliver_to_human_dispatch(...)
```

The wake mechanisms were inside the `if created:` block.

The `(run_id, name, task_id)` dedupe is correct as an *audit-trail* contract — it ensures the `RunSignal` table records each unique operator action once. But the *Dispatch* awaiting the signal is per-iteration distinct (each park creates a new `Dispatch` row in `dispatched` state). On a re-iteration the audit row already exists → `created=False` → the brand-new dispatch never woke.

`_deliver_to_human_dispatch` already filters by `Dispatch.state == DISPATCHED`, so it only matches an *active* dispatch. The previous iteration's dispatch is `COMPLETED` and gets skipped automatically. Wake on duplicate is safe.

`supervisor.deliver_signal` (the legacy v0.1.0 buffered path) is also safe under duplicate — overwrites the buffer for `(run_id, name, task_id)` and sets the event if a waiter is parked; no-op otherwise.

---

## 4. Fix

Move the wake mechanisms outside the `if created:` block. Trace recording stays inside (audit-trail dedupe is the right behaviour for the trace).

```python
if created:
    await trace.record_operator_signal(run_id, dto)

# Wake mechanisms fire on every signal — duplicates are common in
# iterative flows where the same human-pause node parks multiple
# times.  Both wake paths are idempotent on "no waiter" / "already
# resolved future".
supervisor.deliver_signal(run_id, name, task_id, payload)
await _deliver_to_human_dispatch(...)
```

---

## 5. Verification

- New `tests/modules/ai/test_routes_signals.py::TestDuplicateSignalWakesActiveDispatch::test_duplicate_signal_completes_active_dispatch`. Seeds a run with a Dispatch row in `DISPATCHED` state, sends the first signal (succeeds), resets the dispatch back to `DISPATCHED` to simulate the second iteration, sends the *same* signal again, asserts the response has `alreadyReceived=true` AND the dispatch row flipped to `COMPLETED`.
- Existing `TestHappyPath::test_duplicate_is_idempotent` unchanged and still passes — the audit-row dedupe contract is preserved.
- Full suite: 1164 passed, 12 skipped.

---

## 6. Out of Scope

- **Multiple in-flight human dispatches per run.** `_deliver_to_human_dispatch` already logs a warning and no-ops when more than one `dispatched`-state human dispatch exists for the same run. The lifecycle agent never has two human pauses concurrently in v0.3.0; PR 5's "explicit pairing" comment in the helper is the long-term plan.
- **Reviewer LLM fail-loop on smoke runs without real implementations.** With BUG-011 fixed the run will reach `terminate_correction_budget` after 2 attempts and end as `failed` — proving the budget mechanism trips correctly. Reaching `completed` via `close_work_item` in the smoke needs either (a) a tighter work-item brief that's verifiable from a signal payload alone, or (b) a stub reviewer in the smoke harness that always passes. Operator concern, separate work.
- **`real-run.sh`'s snake_case `task_id`** + empty task_id extractor — operator-side script, fix separately.

---

## Changelog

- 2026-05-02 — Filed and resolved in the same PR. Wake mechanisms decoupled from audit-trail dedupe; trace recording stays inside `if created:`.
