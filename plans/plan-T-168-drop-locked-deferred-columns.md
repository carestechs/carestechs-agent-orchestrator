# Implementation Plan: T-168 — Drop `locked_from` + `deferred_from` columns

## Task Reference
- **Task ID:** T-168
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-8. Engine transition history owns prior state. Local columns are redundant under engine-as-authority.

## Overview
Destructive migration removes both columns. Signal adapters that set them (`lock_work_item`, `defer_task`) stop writing; unlock path reads prior state from the engine's transition history instead. Pre-flight check fails the migration loudly if any current row would lose data.

## Implementation Steps

### Step 1: Pre-flight check script
**File:** `src/app/migrations/versions/YYYY_MM_DD_drop_locked_deferred_preflight.py`
**Action:** Create

Before the destructive change, add a tiny migration whose `upgrade()` inspects the live data:

```python
def upgrade() -> None:
    conn = op.get_bind()
    locked = conn.execute(text(
        "SELECT id, external_ref FROM work_items WHERE status = 'locked'"
    )).all()
    deferred = conn.execute(text(
        "SELECT id, external_ref FROM tasks WHERE status = 'deferred'"
    )).all()
    if locked or deferred:
        raise RuntimeError(
            "FEAT-008 pre-flight: refusing to drop locked_from/deferred_from — "
            f"{len(locked)} locked work item(s) and {len(deferred)} deferred task(s) "
            "would lose prior-state data. Resolve before upgrading:\n"
            + "\n".join(f"  locked work_item {r.external_ref}" for r in locked)
            + "\n".join(f"  deferred task {r.external_ref}" for r in deferred)
        )


def downgrade() -> None:
    pass  # pre-flight has no schema effect to reverse
```

This is a migration that does *no* schema change — it just validates. Split from the actual drop so operators can confirm-and-proceed, or skip the check with explicit override in an emergency.

**Alternative (simpler):** include the pre-flight logic inside the destructive migration's `upgrade()` before `op.drop_column`. No separate file. Choose this — less moving parts, same guarantee.

Revised plan: single migration, pre-flight at the top, drop at the bottom.

### Step 2: Destructive migration
**File:** `src/app/migrations/versions/YYYY_MM_DD_drop_locked_from_deferred_from.py`
**Action:** Create

```python
"""drop locked_from from work_items + deferred_from from tasks (FEAT-008)"""
from alembic import op
from sqlalchemy import text

revision = "..."
down_revision = "..."  # after the T-165 pending_aux_writes migration


def upgrade() -> None:
    conn = op.get_bind()
    locked = conn.execute(text(
        "SELECT id, external_ref FROM work_items WHERE status = 'locked'"
    )).all()
    deferred = conn.execute(text(
        "SELECT id, external_ref FROM tasks WHERE status = 'deferred'"
    )).all()
    if locked or deferred:
        msg = (
            "FEAT-008: refusing to drop locked_from/deferred_from — "
            f"{len(locked)} locked work item(s), {len(deferred)} deferred task(s). "
            "Resolve these before upgrading."
        )
        raise RuntimeError(msg)
    op.drop_column("work_items", "locked_from")
    op.drop_column("tasks", "deferred_from")


def downgrade() -> None:
    op.add_column("work_items", sa.Column("locked_from", sa.String(32), nullable=True))
    op.add_column("tasks", sa.Column("deferred_from", sa.String(32), nullable=True))
```

Downgrade restores the columns as nullable — data is not recovered.

### Step 3: Remove model attributes
**File:** `src/app/modules/ai/models.py`
**Action:** Modify

Delete `locked_from` from `WorkItem` and `deferred_from` from `Task`. Search for any remaining references in the codebase and remove them.

### Step 4: Rewrite unlock logic
**File:** `src/app/modules/ai/lifecycle/work_items.py`
**Action:** Modify

Today: `unlock_work_item` reads `work_item.locked_from` and transitions back to that state.

New path: query the engine's transition history for the last non-`locked` state. Add a helper on `FlowEngineLifecycleClient`:

```python
async def get_previous_state(
    self, item_id: uuid.UUID, excluding: str
) -> str | None:
    """Return the most recent state before the current one, excluding *excluding*.

    Used by unlock to find "what state was I in before I was locked."
    """
    history = await self.get_transition_history(item_id)
    for entry in reversed(history):
        if entry.to_state != excluding:
            return entry.to_state
    return None
```

Engine-absent fallback: no history to read, no unlock possible. Either hardcode a default state (`in_progress`) or refuse the unlock with a clear error. Choose: refuse — silent default is worse than a visible failure.

### Step 5: Rewrite resume-from-defer (if we support it)
**File:** `src/app/modules/ai/lifecycle/tasks.py`
**Action:** Modify

Today's `defer_task` writes `deferred_from`. There's no "resume from defer" signal in FEAT-006 — deferred is terminal. So this column is dead weight even under current logic. Deleting it is pure cleanup. Confirm no callers reference it, then remove.

### Step 6: Update models tests
**File:** `tests/modules/ai/test_models.py`
**Action:** Modify

Remove both columns from the expected column sets.

### Step 7: Unit test — unlock via engine history
**File:** `tests/modules/ai/lifecycle/test_work_items_unlock.py`
**Action:** Modify or Create

Happy path: lock a work item → engine history includes the pre-lock state → unlock restores. Use a stubbed engine client that returns a canned history.

Engine-absent path: unlock raises a clear error.

### Step 8: Integration test sanity
**File:** `tests/integration/test_feat006_e2e.py`
**Action:** Modify

The e2e test locks + unlocks a work item mid-flow. Under the new path, the unlock reads the engine history. Verify the test still passes; if the engine stub doesn't record history by default, extend the stub.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/migrations/versions/YYYY_MM_DD_drop_locked_from_deferred_from.py` | Create | Migration with pre-flight. |
| `src/app/modules/ai/models.py` | Modify | Remove `locked_from`, `deferred_from`. |
| `src/app/modules/ai/lifecycle/work_items.py` | Modify | Unlock reads engine history. |
| `src/app/modules/ai/lifecycle/tasks.py` | Modify | Remove defer `deferred_from` writes. |
| `src/app/modules/ai/lifecycle/engine_client.py` | Modify | Add `get_previous_state` helper. |
| `tests/modules/ai/test_models.py` | Modify | Drop column expectations. |
| `tests/modules/ai/lifecycle/test_work_items_unlock.py` | Modify or Create | Unlock via engine history. |
| `tests/integration/test_feat006_e2e.py` | Modify | Stub engine history in the fixture. |

## Edge Cases & Risks
- **Pre-flight failure in production.** If the migration hits locked/deferred rows, ops has to manually resolve before upgrading. That's correct — silent data loss is worse — but operators need clear guidance. Add a section to `README.md` under "Upgrading from v0.6.x to v0.7.x" describing the resolution steps.
- **Engine-absent unlock is now impossible.** Documented. If operators need dev-mode unlock support, add a CLI command that accepts the target state explicitly (`orchestrator unlock <id> --restore-to=in_progress`). Defer to a follow-on task — not in T-168.
- **Downgrade restores schema but not data.** A downgrade after upgrade means the columns are back but NULL everywhere. Any rollback procedure needs to account for that. Document in the migration file's docstring.
- **Migration ordering.** This runs after T-165's outbox migration. Standard chaining.

## Acceptance Verification
- [ ] Migration drops both columns on clean upgrade.
- [ ] Migration fails loudly when locked/deferred rows exist.
- [ ] Downgrade restores columns (empty) cleanly.
- [ ] `test_models.py` expectations updated.
- [ ] Lock/unlock unit tests pass under the new engine-history-based unlock.
- [ ] FEAT-006 e2e test still exercises the lock/unlock path and passes.
- [ ] README documents the upgrade path.
- [ ] `uv run pyright`, `ruff`, full suite green.
