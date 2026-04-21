# Implementation Plan: T-170 — `reconcile-aux` CLI + idempotent orphan drain

## Task Reference
- **Task ID:** T-170
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-10. Without reconciliation, a lost webhook is permanent data loss. The outbox is only useful if something drains orphans.

## Overview
New CLI: `uv run orchestrator reconcile-aux [--since=24h] [--dry-run]`. Walks `pending_aux_writes`, queries the engine for current entity state, and materializes any aux rows whose webhook was lost. Idempotent — running twice produces the same final state.

## Implementation Steps

### Step 1: Reconciliation module — pure logic
**File:** `src/app/modules/ai/lifecycle/reconciliation.py`
**Action:** Create

Isolate the logic from the CLI adapter so it can be unit-tested without Typer rigging.

```python
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.lifecycle.reactor import _build_aux_row  # reuse from T-167
from app.modules.ai.models import PendingAuxWrite, Task, WorkItem

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    scanned: int = 0
    materialized: int = 0
    skipped_stale: int = 0        # engine says transition didn't happen
    skipped_unknown: int = 0      # entity not found (engine or local)
    errors: list[str] = field(default_factory=list)


async def reconcile(
    db: AsyncSession,
    engine: FlowEngineLifecycleClient,
    *,
    since: timedelta | None = None,
    dry_run: bool = False,
) -> ReconciliationReport:
    query = select(PendingAuxWrite).order_by(PendingAuxWrite.enqueued_at)
    if since is not None:
        query = query.where(PendingAuxWrite.enqueued_at >= datetime.now(UTC) - since)
    rows = (await db.scalars(query)).all()

    report = ReconciliationReport()
    for pending in rows:
        report.scanned += 1
        try:
            resolved = await _reconcile_one(db, engine, pending, dry_run=dry_run)
        except Exception as exc:
            report.errors.append(f"{pending.correlation_id}: {exc}")
            logger.exception("reconcile row failed")
            continue
        if resolved == "materialized":
            report.materialized += 1
        elif resolved == "stale":
            report.skipped_stale += 1
        elif resolved == "unknown":
            report.skipped_unknown += 1

    if not dry_run:
        await db.commit()
    return report


async def _reconcile_one(
    db: AsyncSession,
    engine: FlowEngineLifecycleClient,
    pending: PendingAuxWrite,
    *,
    dry_run: bool,
) -> Literal["materialized", "stale", "unknown"]:
    # 1. Find the entity locally to get its engine_item_id.
    if pending.entity_type == "task":
        entity = await db.scalar(select(Task).where(Task.id == pending.entity_id))
    else:
        entity = await db.scalar(select(WorkItem).where(WorkItem.id == pending.entity_id))
    if entity is None or entity.engine_item_id is None:
        return "unknown"

    # 2. Ask the engine: has the transition this signal requested actually landed?
    #    We look at the engine's current state + transition history and match
    #    against the pending signal's target state.
    target_state = _target_state_for(pending.signal_name)
    if target_state is None:
        # Signal doesn't map to a state transition (e.g., reject-task is stay-in-place).
        # Materialize unconditionally — the engine had no work to do but our outbox
        # row still needs to resolve.
        if not dry_run:
            await _apply(db, pending)
        return "materialized"

    current = await engine.get_item_state(entity.engine_item_id)
    if current == target_state:
        if not dry_run:
            await _apply(db, pending)
        return "materialized"

    # Engine says the signal never landed server-side — the transition rolled
    # back or failed silently.  Leave the pending row for the operator to
    # investigate; reconciliation should not materialize an aux row for a
    # transition the engine never accepted.
    return "stale"


async def _apply(db: AsyncSession, pending: PendingAuxWrite) -> None:
    aux_row = _build_aux_row(pending)
    if aux_row is not None:
        db.add(aux_row)
    await db.delete(pending)


def _target_state_for(signal_name: str) -> str | None:
    """Which engine state should exist if this signal completed successfully?

    Returns ``None`` for signals that don't advance state (rejections).
    """
    return {
        "submit-implementation": "impl_review",
        "approve-review": "done",
        "submit-plan": "plan_review",
        "approve-plan": "implementing",
        "assign-task": "assignment_review",
        "assign-approve": "planning",
        "approve-task": "assigning",
        "defer-task": "deferred",
        # rejections — no state change:
        "reject-task": None,
        "reject-plan": None,
        "reject-review": None,
    }.get(signal_name)
```

Key design calls:
- **Idempotent by construction.** Materialize → delete pending row. Re-running finds no pending row.
- **Stale rows preserved, not deleted.** If the engine says the transition didn't land, the pending row stays. Operator investigates. Deleting silently loses the breadcrumb.
- **Dry-run commits nothing.** Report reflects *what would happen*.

### Step 2: CLI command
**File:** `src/app/cli.py`
**Action:** Modify

```python
@main.command("reconcile-aux")
def reconcile_aux_cmd(
    since: Annotated[str | None, typer.Option("--since", help="e.g. 24h, 7d, ISO-8601")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Drain orphan pending_aux_writes rows by querying engine state."""
    asyncio.run(_run_reconcile(since=since, dry_run=dry_run))


async def _run_reconcile(*, since: str | None, dry_run: bool) -> None:
    since_td = _parse_since(since) if since else None
    settings = get_settings()
    engine = _build_lifecycle_engine_client(settings)
    sessionmaker = make_sessionmaker(make_engine(settings))
    async with sessionmaker() as db:
        report = await reconcile(db, engine, since=since_td, dry_run=dry_run)
    typer.echo(_format_report(report, dry_run=dry_run))
    if report.errors:
        raise SystemExit(2)


def _parse_since(raw: str) -> timedelta:
    """24h, 7d, 15m, or ISO-8601 datetime (interpreted as "since <datetime>")."""
    ...
```

Human output format:

```
Reconciliation report (dry-run: false)
  Scanned:           12
  Materialized:       3
  Skipped (stale):    1
  Skipped (unknown):  0
  Errors:             0
```

### Step 3: Unit tests
**File:** `tests/modules/ai/lifecycle/test_reconciliation.py`
**Action:** Create

Cases:
- **Pending row + engine confirms state matches.** Materializes aux row, deletes pending row. Report shows 1 materialized.
- **Pending row + engine says wrong state.** Pending row preserved, report shows 1 stale.
- **Pending row for unknown entity.** Skipped, report shows 1 unknown.
- **Rejection signal (target_state=None).** Materializes unconditionally.
- **Dry run.** No DB changes after call; report matches what a real run would do.
- **Idempotency.** Run twice → second run reports 0 scanned (if `since` excludes now-empty window) or 0 materialized (pending rows already drained).
- **Exception handling.** Engine call raises → error captured in `report.errors`, loop continues.

### Step 4: Integration test
**File:** `tests/integration/test_feat008_reconciliation.py`
**Action:** Create

Higher-fidelity test:

- Drive a signal through the service layer with engine stubbed so no webhook fires (simulating webhook loss).
- Assert pending row exists, aux row absent.
- Run the reconciliation CLI (invoke via `typer.testing.CliRunner` or call the service function directly).
- Assert pending row drained, aux row present.

### Step 5: Operator docs
**File:** `README.md`
**Action:** Modify

Under a new "Operations" section (or append to existing):

```markdown
### Reconciling lost webhooks

If the flow engine drops an `item.transitioned` webhook, the orchestrator
will have a ``pending_aux_writes`` row without the corresponding audit
row (Approval, TaskImplementation, etc.).  Drain the backlog with:

    uv run orchestrator reconcile-aux --since 24h

Add ``--dry-run`` to preview.  Safe to run hourly from cron/systemd.
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/reconciliation.py` | Create | Pure logic. |
| `src/app/cli.py` | Modify | `reconcile-aux` command. |
| `tests/modules/ai/lifecycle/test_reconciliation.py` | Create | Unit tests. |
| `tests/integration/test_feat008_reconciliation.py` | Create | Integration. |
| `README.md` | Modify | Operator docs. |

## Edge Cases & Risks
- **Engine returns item not found.** Treat as `unknown`, not `stale` — distinguishes "we lost the signal" from "we lost the item." Report separately.
- **Clock skew.** `--since 24h` uses the local clock. If the orchestrator clock and the engine clock drift, reconciliation might miss or re-process rows at the boundary. Accept the risk — reconciliation is idempotent, so re-processing is harmless; missing is the bigger concern but mitigated by running periodically.
- **Large outbox during outage.** If the engine is down for a week and then recovers, the outbox accumulates. Running reconciliation afterwards may take minutes. Add a `--limit` flag if this proves a real problem; not in v1.
- **Transaction boundary.** Single commit per reconciliation run (or per dry-run pass). If the CLI crashes mid-run, already-materialized rows stay, still-pending rows stay. Safe.
- **Rejection signals.** Reconcile materializes unconditionally because the engine won't have a state to check against. That's correct — the signal's semantics are "record the rejection", which the aux row captures. No staleness risk.

## Acceptance Verification
- [ ] CLI drains pending rows whose engine state matches the expected target.
- [ ] CLI preserves pending rows whose engine state doesn't match (stale).
- [ ] `--dry-run` produces a report without DB changes.
- [ ] Idempotent: running twice converges.
- [ ] Rejection signals handled (unconditional materialize).
- [ ] Unit tests cover all six cases above.
- [ ] Integration test proves end-to-end drain after simulated webhook loss.
- [ ] README documents the command.
- [ ] `uv run pyright`, `ruff`, test suite green.
