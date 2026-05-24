# Bug Report: BUG-015 — Deterministic runtime never sets Step status to `dispatched`

> **Purpose**: The deterministic runtime (`runtime_deterministic.py`) marks the `Dispatch` row as `dispatched` when an executor is invoked, but leaves the corresponding `Step` row at `pending`. The Step jumps directly from `pending` to `completed`/`failed` when the terminal envelope arrives. This makes the `StepStatus.DISPATCHED` value dead code in the deterministic path and breaks any consumer (UI, trace viewer, reconciler) that relies on step status to determine whether a step is actively waiting for a result.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-015 |
| **Summary** | When a deterministic-policy agent dispatches to any executor (local, remote, human, engine), the `Dispatch` row transitions `pending → dispatched`, but the `Step` row stays `pending` until `_commit_terminal` runs. The LLM-policy runtime (`runtime.py:398`) correctly calls `_mark_step_dispatched`, but the deterministic runtime never does. For local executors the gap is invisible (dispatch and terminal happen in the same iteration), but for human, engine, and remote executors the Step stays `pending` for the entire wait — seconds to hours for human checkpoints. |
| **Severity** | Medium. Incorrect observability data; no data loss or runtime failure. Frontends that check step status to render "active/waiting" states must work around the missing `dispatched` status with negation checks (`!= completed && != failed`) instead of positive checks (`== dispatched`). |
| **Status** | Resolved |
| **Reported By** | Operator observation while building frontend for manual-mode checkpoints (2026-05-24) |
| **Date Reported** | 2026-05-24 |
| **Date First Observed** | Since FEAT-009 shipped (deterministic runtime). Never surfaced because tests assert on terminal step states, not intermediate ones. |
| **Related** | FEAT-009 (deterministic runtime), IMP-002 (paused status on human dispatch), FEAT-010 (engine executor), BUG-014 (paused zombie sweep — same discovery session) |

---

## 2. Reproduction

1. Start a manual-mode run with a human checkpoint:
   ```bash
   uv run orchestrator run lifecycle-agent@0.4.0-manual \
     --work-item docs/work-items/FEAT-042.md --follow
   ```
2. Wait until the run reaches a human checkpoint (e.g. `confirm_assignment`).
3. Query the run's steps: `GET /api/v1/runs/{id}/trace?kind=step`.
4. Find the step for the current node.

**Expected:** `step.status == "dispatched"` (matching the `Dispatch` row's state).

**Actual:** `step.status == "pending"`. The `Dispatch` row is correctly `dispatched`.

---

## 3. Root Cause

### 3.1 Dispatch row updated, Step row not

`runtime_deterministic.py:289-293`:
```python
async with session_factory() as session:
    dispatch_row = await session.get(Dispatch, dispatch_id)
    assert dispatch_row is not None
    dispatch_row.mark_dispatched(at=datetime.now(UTC))
    await session.commit()
```

Only the `Dispatch` row is updated. The `Step` row (created at line 250 with `status=PENDING`) is not touched.

### 3.2 The LLM-policy runtime does this correctly

`runtime.py:388-400` defines `_mark_step_dispatched` which sets `step.status = StepStatus.DISPATCHED` and `step.dispatched_at`. This is called at `runtime.py:265` when the engine run starts. The deterministic runtime has no equivalent call.

### 3.3 `_commit_terminal` skips `dispatched` entirely

`runtime_deterministic.py:508-513`:
```python
def _step_status_from(envelope: DispatchEnvelope) -> str:
    if envelope.state == DispatchState.COMPLETED:
        return StepStatus.COMPLETED.value
    if envelope.state == DispatchState.FAILED:
        return StepStatus.FAILED.value
    return StepStatus.IN_PROGRESS.value
```

When the terminal envelope arrives, the Step goes from `pending` straight to `completed` — `dispatched` is never set at any point in the lifecycle.

### 3.4 The `dispatched_at` timestamp is also wrong

`_commit_terminal` (line 550) sets `step_row.dispatched_at = envelope.started_at`. This is the *executor start time*, not the *dispatch time*. For human executors the two are the same (immediate return), but the semantics are muddled. The `dispatched_at` field should be set when the step is actually dispatched, not when the terminal result arrives.

---

## 4. Proposed Fix

Update the `Dispatch.mark_dispatched` block in `runtime_deterministic.py` (lines 289-293) to also update the Step:

```python
async with session_factory() as session:
    dispatch_row = await session.get(Dispatch, dispatch_id)
    assert dispatch_row is not None
    now = datetime.now(UTC)
    dispatch_row.mark_dispatched(at=now)
    step_row = await session.get(Step, step_id)
    if step_row is not None:
        step_row.status = StepStatus.DISPATCHED
        step_row.dispatched_at = now
    await session.commit()
```

This mirrors the LLM-policy runtime's `_mark_step_dispatched` behavior. The `_commit_terminal` path already overwrites `step_row.status` and `step_row.dispatched_at` when the result arrives, so there's no conflict — the final state is unchanged; only the intermediate state is corrected.

---

## 5. Out of Scope

- Reconciler changes. `reconciliation.py` already defines the monotonic order `PENDING(0) < DISPATCHED(1) < IN_PROGRESS(2) < COMPLETED/FAILED(3)`. The fix makes the deterministic runtime produce `DISPATCHED` steps that the reconciler already handles.
- Frontend changes. The frontend workaround (`!= completed && != failed`) will still work after the fix, but can be simplified to `== dispatched` if desired.
- Adding a `StepStatus.PAUSED` value to mirror `RunStatus.PAUSED`. The step is dispatched (waiting for a result); `paused` is a run-level concept. Not needed.

---

## 6. Verification (when fix lands)

- New test: seed a dispatch for a human executor, assert `Step.status == DISPATCHED` and `Step.dispatched_at` is set before the signal arrives.
- Existing tests remain green (terminal step states are unchanged).
- The trace endpoint (`GET /api/v1/runs/{id}/trace?kind=step`) shows `dispatched` for in-flight human/engine/remote steps.
