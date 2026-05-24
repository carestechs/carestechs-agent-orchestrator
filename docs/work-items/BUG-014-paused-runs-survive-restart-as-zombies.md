# Bug Report: BUG-014 — Paused runs survive process restart as zombies; work items strand

> **Purpose**: `reconcile_zombie_runs` only sweeps `status=running` rows. Runs parked on a human-executor checkpoint (`status=paused`, IMP-002) are invisible to the sweep — a process restart leaves them stuck forever with no supervisor to deliver the awaited signal. The associated work items remain in their pre-crash status (`open` or `in_progress`) with no active run to drive them forward.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-014 |
| **Summary** | When the orchestrator process stops while a run is `paused` (waiting for an operator signal at a `mode=human` dispatch), the zombie-run reconciler at startup does not catch the row. The run stays `paused` indefinitely; its in-flight `Dispatch` row is cancelled by the dispatch reconciler, but nothing transitions the run itself to a terminal state. The work item remains stranded in whatever lifecycle status it held at crash time. |
| **Severity** | Medium. Only affects the `lifecycle-agent@0.4.0-manual` variant (and any future agent with human checkpoints). The autonomous `@0.3.0` agent never enters `paused`. Impact is operational — stranded rows require manual DB intervention to clear. |
| **Status** | Resolved |
| **Reported By** | Operator observation (2026-05-24) |
| **Date Reported** | 2026-05-24 |
| **Date First Observed** | Since IMP-002 shipped (run status flip to `paused` on human dispatch). Never surfaced because process restarts during manual-mode runs were not tested. |
| **Related** | IMP-002 (introduced `paused` status flip), FEAT-015 (manual variant with human checkpoints), FEAT-009 / T-221 (dispatch reconciler), T-045 (zombie-run reconciler) |

---

## 2. Reproduction

1. Start a manual-mode run:
   ```bash
   uv run orchestrator run lifecycle-agent@0.4.0-manual \
     --work-item docs/work-items/FEAT-042.md --follow
   ```
2. Wait until the run reaches a human checkpoint (e.g. `confirm_assignment`). The run status flips to `paused`; a `Dispatch` row with `mode=human, state=dispatched` is written.
3. Kill the orchestrator process (`Ctrl-C` or `kill`).
4. Restart the orchestrator: `uv run uvicorn app.main:app --reload`.
5. Query run status: `GET /api/v1/runs/{id}`.

**Expected:** Run is in `failed` with `final_state.zombie_reason = "process restart"`, matching the behavior for `running` runs.

**Actual:** Run is still `paused`. The `Dispatch` row was cancelled by the dispatch reconciler (detail `"orchestrator_restart"`), but the run itself was never touched. No supervisor exists for this run in the new process — the signal endpoint would create a `RunSignal` row but `deliver_dispatch` has no target future. The run is permanently stuck.

---

## 3. Root Cause

### 3.1 Zombie sweep filters on `RUNNING` only

`src/app/lifespan.py:43`:
```python
zombies = await session.scalars(
    select(Run).where(Run.status == RunStatus.RUNNING)
)
```

The `PAUSED` status was introduced by IMP-002 after the zombie reconciler was written (T-045). The reconciler was never updated to include it.

### 3.2 Dispatch reconciler settles the dispatch but not the run

`src/app/modules/ai/executors/reconcile.py:89-118` (`reconcile_orphan_dispatches`) runs at startup with `skip_run_alive=False`, so it *does* find and cancel the human dispatch. But it only writes to the `Dispatch` row — it has no mechanism to transition the owning `Run` to a terminal state. The run stays `paused` with a cancelled dispatch that nobody will ever deliver.

### 3.3 The signal endpoint can't help

If an operator posts a signal to the paused run after restart, `service._deliver_to_human_dispatch` looks up the `Dispatch` row — which is now `cancelled`. The signal is persisted as a `RunSignal` but the dispatch future doesn't exist in the new supervisor. The run remains stuck.

---

## 4. Proposed Fix

### 4.1 Include `PAUSED` in the zombie sweep

Update `reconcile_zombie_runs` to query for both `RUNNING` and `PAUSED`:

```python
orphan_statuses = [RunStatus.RUNNING, RunStatus.PAUSED]
zombies = await session.scalars(
    select(Run).where(Run.status.in_(orphan_statuses))
)
```

And the corresponding bulk update:

```python
await session.execute(
    update(Run)
    .where(Run.status.in_(orphan_statuses))
    .values(
        status=RunStatus.FAILED,
        stop_reason=StopReason.ERROR,
        ended_at=now,
    )
)
```

This is the minimal fix. A paused run whose process died is just as orphaned as a running one — the supervisor that holds its dispatch future is gone.

### 4.2 Update the dispatch reconciler's `skip_run_alive` filter

`reconcile.py:172` skips dispatches whose owning run is `RUNNING`:

```python
if skip_run_alive and run_status == RunStatus.RUNNING:
```

For the CLI path (`skip_run_alive=True`), `PAUSED` runs should also be considered alive (the operator may still deliver the signal). No change needed here after 4.1 — once paused runs are zombie-swept at startup, the CLI reconciler will never encounter a paused-but-orphaned dispatch. But worth adding a comment documenting that `PAUSED` is handled by the zombie sweep, not the dispatch reconciler.

### 4.3 Update tests

- Add a test case in the zombie reconciler tests that creates a `paused` run, calls `reconcile_zombie_runs`, and asserts it transitions to `failed/error` with `zombie_reason`.
- Add an integration-level test that starts a manual-mode run, simulates a process restart (call `reconcile_zombie_runs` + `reconcile_orphan_dispatches`), and asserts both the run and dispatch reach terminal states.

---

## 5. Out of Scope

- Automatic re-queue of stranded work items. The fix marks the run as failed; the work item stays in its current lifecycle status. An operator can start a new run for the same work item. Automatic retry is a separate feature.
- Graceful shutdown behavior. `supervisor.shutdown(grace=5.0)` already drains in-flight runs on clean shutdown. This bug is about ungraceful process death only.
- Whether `paused` runs should be resumable after restart rather than zombie-swept. Resumability would require persisting the dispatch future's state and re-hydrating it — a significantly larger feature. The conservative approach (fail + let the operator re-run) is correct for now.

---

## 6. Verification (when fix lands)

- `reconcile_zombie_runs` transitions both `running` and `paused` rows to `failed/error`.
- Existing zombie-sweep tests for `running` rows remain green unchanged.
- New test: a `paused` run with an in-flight human dispatch is swept to `failed` on restart; the dispatch is cancelled; querying the run returns `failed` with `zombie_reason`.
- Manual verification: start a `@0.4.0-manual` run, kill the process at a checkpoint, restart, confirm the run shows `failed` in the API.
