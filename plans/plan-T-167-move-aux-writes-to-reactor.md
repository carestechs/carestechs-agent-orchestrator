# Implementation Plan: T-167 — Move aux-row writes to the reactor

## Task Reference
- **Task ID:** T-167
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Rationale:** AC-7. The load-bearing pivot of FEAT-008. Signal adapters stop writing aux rows directly; the reactor materializes them on correlation-matched webhook arrival from the engine.

## Overview
Signal adapters in `lifecycle/service.py` change shape: forward to the engine (already do this), enqueue a `PendingAuxWrite`, commit the idempotency + outbox + transition state, return 202. On engine webhook arrival, the reactor matches the `correlation_id`, materializes the aux row from the outbox payload, deletes the outbox row. Fallback exists for engine-absent mode — adapters still write inline when `lifecycle_engine_client is None`.

## Implementation Steps

### Step 1: Catalog the inline aux writes
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify (survey first)

Before touching code, list every aux row the adapters currently insert:

| Signal | Aux row type | Fields |
|--------|--------------|--------|
| S5 approve-task | `Approval(stage=task, decision=approve)` | task_id, actor, actor_role |
| S6 reject-task | `Approval(stage=task, decision=reject)` | task_id, actor, actor_role, feedback |
| S7 assign-task | `TaskAssignment` | task_id, assignee_type, assignee_id, assigned_by |
| S7b assign-approve | `Approval(stage=assignment, decision=approve)` | task_id, actor, actor_role |
| S8 submit-plan | `TaskPlan` | task_id, plan_path, plan_sha, submitted_by |
| S9 approve-plan | `Approval(stage=plan, decision=approve)` | task_id, actor, actor_role |
| S10 reject-plan | `Approval(stage=plan, decision=reject)` | task_id, actor, actor_role, feedback |
| S11 submit-impl | `TaskImplementation` | task_id, pr_url, commit_sha, summary, submitted_by |
| S12 review-approve | `Approval(stage=review, decision=approve)` | task_id, actor, actor_role |
| S13 review-reject | `Approval(stage=review, decision=reject)` | task_id, actor, actor_role, feedback |

These are the writes that move. Everything else in the adapter (idempotency, correlation, state transition) stays.

### Step 2: Change adapter shape
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Each adapter becomes:

```python
async def submit_implementation_signal(db, task_id, *, pr_url, commit_sha, summary, actor, engine, github):
    # 1. Idempotency check (unchanged)
    key = idempotency.compute_signal_key(...)
    is_new, _ = await idempotency.check_and_record(...)
    if not is_new:
        return await _reload_task(db, task_id), False

    # 2. Correlation (unchanged)
    corr = await _with_correlation(db, signal_name="submit-implementation", payload={...})

    # 3. State transition (unchanged — still mirrors to engine)
    task = await tasks.submit_implementation(
        db, task_id, submitted_by=actor, engine=engine, correlation_id=corr,
    )

    # 4. NEW: enqueue outbox instead of inline insert
    if engine is not None:
        db.add(PendingAuxWrite(
            correlation_id=corr,
            signal_name="submit-implementation",
            entity_type="task",
            entity_id=task_id,
            payload={
                "aux_type": "task_implementation",
                "pr_url": pr_url,
                "commit_sha": commit_sha,
                "summary": summary,
                "submitted_by": actor,
            },
        ))
    else:
        # Engine-absent fallback: pre-FEAT-008 behavior
        db.add(TaskImplementation(
            task_id=task_id, pr_url=pr_url, commit_sha=commit_sha,
            summary=summary, submitted_by=actor,
        ))

    # 5. Commit (unchanged)
    await db.commit()

    # 6. Effector dispatch still happens here (transitional from T-162)
    # FIXME: moves to reactor in T-167 — this is the move.
    # ACTUALLY: stays here for engine-absent mode; moves to reactor for engine-present.
    if engine is None:
        await _fire_effectors_for_transition(...)

    return task, True
```

Key calls:
- **Outbox payload is self-describing.** `aux_type` tells the reactor which row to build. Fields are whatever that aux type needs.
- **Engine-absent fallback** writes inline exactly like pre-FEAT-008. Signal adapter knows when it's in fallback mode by checking `engine is None`.
- **Effector dispatch moves with the aux write.** Engine-present: reactor fires effectors after materializing. Engine-absent: adapter fires effectors after inline write.

### Step 3: Reactor materialization
**File:** `src/app/modules/ai/lifecycle/reactor.py`
**Action:** Modify

Today's reactor handles `item.transitioned` webhooks and fires derivations (W2, W5). Extend it:

```python
async def handle_transition(db, webhook_event):
    correlation_id = webhook_event.correlation_id  # encoded by engine
    if correlation_id is not None:
        await _materialize_aux(db, correlation_id)
        # Also fire effectors now that aux row exists
        await _fire_effectors(db, webhook_event)

    # Existing derivation dispatch (unchanged)
    ...


async def _materialize_aux(db: AsyncSession, correlation_id: uuid.UUID) -> None:
    pending = await db.scalar(
        select(PendingAuxWrite).where(
            PendingAuxWrite.correlation_id == correlation_id
        )
    )
    if pending is None:
        # Either already materialized (idempotent no-op) or never enqueued.
        return

    aux_row = _build_aux_row(pending)  # dispatcher on pending.payload["aux_type"]
    if aux_row is not None:
        db.add(aux_row)
    await db.delete(pending)
    await db.commit()


def _build_aux_row(pending: PendingAuxWrite) -> Base | None:
    aux_type = pending.payload.get("aux_type")
    if aux_type == "task_implementation":
        return TaskImplementation(
            task_id=pending.entity_id,
            pr_url=pending.payload["pr_url"],
            commit_sha=pending.payload["commit_sha"],
            summary=pending.payload["summary"],
            submitted_by=pending.payload["submitted_by"],
        )
    if aux_type == "task_assignment":
        return TaskAssignment(
            task_id=pending.entity_id,
            assignee_type=pending.payload["assignee_type"],
            assignee_id=pending.payload["assignee_id"],
            assigned_by=pending.payload["assigned_by"],
        )
    if aux_type == "approval":
        return Approval(
            task_id=pending.entity_id,
            stage=pending.payload["stage"],
            decision=pending.payload["decision"],
            actor=pending.payload["actor"],
            actor_role=pending.payload["actor_role"],
            feedback=pending.payload.get("feedback"),
        )
    if aux_type == "task_plan":
        return TaskPlan(
            task_id=pending.entity_id,
            plan_path=pending.payload["plan_path"],
            plan_sha=pending.payload["plan_sha"],
            submitted_by=pending.payload["submitted_by"],
        )
    return None
```

Idempotent on duplicate webhook arrival: second arrival finds no `PendingAuxWrite` (already deleted on first materialization), returns silently. Same row materialized twice produces a unique-constraint error; rely on the fact that each aux type has a composite unique constraint that we may need to add (e.g., `(task_id, submitted_at)` on `TaskImplementation`). **Audit the aux tables for uniqueness** — if duplicates are currently possible, a double webhook could produce two rows.

### Step 4: Where does the effector dispatch fire?
**File:** `src/app/modules/ai/lifecycle/reactor.py`
**Action:** Modify

T-162 temporarily fired effectors inside the signal adapter. This task moves it. The reactor, after materializing aux rows, builds an `EffectorContext` from the webhook payload + the now-current entity state and calls `registry.fire_all(ctx)`.

Under engine-absent, the signal adapter still fires effectors (unchanged from T-162). Under engine-present, it's the reactor. The effectors themselves don't know which path they're on.

### Step 5: Unit test — engine stubbed, no aux rows until webhook
**File:** `tests/modules/ai/lifecycle/test_reactor_aux_materialization.py`
**Action:** Create

```python
async def test_signal_adapter_does_not_insert_aux_row_when_engine_present(...):
    # Arrange: engine mock accepts the transition call.
    # Act: call submit_implementation_signal with engine=mock.
    # Assert:
    #   - signal returns (task, True)
    #   - no TaskImplementation row exists
    #   - exactly 1 PendingAuxWrite row exists with matching correlation_id

async def test_reactor_materializes_aux_row_on_matched_webhook(...):
    # Arrange: signal has fired (pending row exists).
    # Act: deliver a synthetic item.transitioned webhook with the
    #      matching correlation_id.
    # Assert:
    #   - TaskImplementation row exists with expected fields
    #   - PendingAuxWrite row is gone

async def test_duplicate_webhook_is_idempotent(...):
    # Deliver the same webhook twice.  Second call is a no-op; no
    # duplicate TaskImplementation row.

async def test_engine_absent_fallback_writes_inline(...):
    # engine=None; signal adapter writes TaskImplementation directly,
    # no PendingAuxWrite row, effectors still fire.
```

### Step 6: Integration regression
**File:** `tests/integration/test_feat006_e2e.py`
**Action:** Modify

With T-166's `await_reactor` already wrapping aux-row assertions, the e2e test should still pass. Verify. If any assertion times out, it's a bug in T-167's reactor path.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/service.py` | Modify | Every signal adapter: enqueue outbox, not inline insert. Engine-absent fallback preserved. |
| `src/app/modules/ai/lifecycle/reactor.py` | Modify | `_materialize_aux` dispatcher + effector fire moved here. |
| `src/app/modules/ai/models.py` | Modify (conditional) | Add uniqueness constraints on aux tables if missing. |
| `tests/modules/ai/lifecycle/test_reactor_aux_materialization.py` | Create | Unit tests for the new path. |
| `tests/integration/test_feat006_e2e.py` | Modify | Verify with `await_reactor`. |

## Edge Cases & Risks
- **Aux-row uniqueness.** Duplicate webhook arrival materializes twice if no unique constraint exists. **Pre-flight audit required** before landing this task. Likely candidates: `TaskImplementation(task_id, commit_sha)` unique; `Approval(task_id, stage, decision, created_at)` unique. Add migrations for missing constraints as part of this task.
- **Webhook arrives before the outbox row is committed.** Race condition: signal adapter's commit includes the outbox write, but the *engine's* transition webhook can theoretically race the orchestrator's commit. In practice, the engine mirror is a HTTP call inside the transaction — commit comes after, webhook comes after commit. Still, write an integration test that exercises the order.
- **Engine mirror call failure.** If the engine call inside `tasks.submit_implementation` raises, the whole signal fails (no 202). Outbox never enqueues. That's correct — no aux row, no webhook expected.
- **Reactor receives a webhook with a correlation_id the orchestrator never enqueued.** Could happen if the engine replays. `_materialize_aux` finds no pending row, returns silently. Log at debug level for forensics.
- **Long-lived pending rows.** If the engine never delivers the webhook, the outbox row accumulates. T-170's reconciliation CLI handles this; `await_reactor` handles the test-level visibility. In production, operators should monitor the outbox row count as a health signal.
- **Commit boundary with reactor-side effectors.** Reactor commits the aux row, then fires effectors. If an effector needs the aux row committed (likely — T-162's github effector reads latest `TaskImplementation`), that ordering is correct. Document this.

## Acceptance Verification
- [ ] Signal adapters no longer insert aux rows directly in engine-present mode.
- [ ] Adapter writes exactly one `PendingAuxWrite` per signal; committed atomically with state transition.
- [ ] Reactor materializes aux rows from the outbox on webhook arrival.
- [ ] Reactor is idempotent on duplicate webhook.
- [ ] Engine-absent fallback path preserved; test proves inline insert still works.
- [ ] FEAT-006 e2e tests pass with `await_reactor` wrappers.
- [ ] Unit tests in `test_reactor_aux_materialization.py` cover the four scenarios above.
- [ ] `uv run pyright`, `ruff`, full suite green.
