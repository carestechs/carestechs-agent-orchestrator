# Implementation Plan: T-165 — Outbox table (`pending_aux_writes`) + migration

## Task Reference
- **Task ID:** T-165
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-10 backstop. Webhook-loss recovery requires a durable record of intent captured *before* the signal returns 202. The outbox is the safety net under T-167's move of aux writes to the reactor.

## Overview
New `pending_aux_writes` table keyed on `correlation_id`. Signal adapters (in T-167) enqueue rows here before returning 202; the reactor deletes them on correlation-matched webhook arrival; the `reconcile-aux` CLI (T-170) drains orphans.

## Implementation Steps

### Step 1: Model
**File:** `src/app/modules/ai/models.py`
**Action:** Modify

Add alongside the existing `PendingSignalContext`:

```python
class PendingAuxWrite(Base):
    """Outbox for aux-row intent — materialized by the reactor or by
    ``reconcile-aux`` when the engine webhook is lost.

    One row per signal that the signal adapter committed.  The reactor
    looks up by ``correlation_id`` on webhook arrival, materializes the
    target aux row, and deletes the outbox row.  Unresolved rows are
    the recovery surface for the reconciliation CLI.
    """

    __tablename__ = "pending_aux_writes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    signal_name: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Only set once the reactor or reconciliation lands the aux row.  Kept
    # briefly for debugging, then GC'd.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_pending_aux_writes_unresolved",
            "entity_id",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_pending_aux_writes_enqueued_at", "enqueued_at"),
    )
```

Design calls:
- **Unique on `correlation_id`** — enforces "one outbox row per signal firing." Duplicate enqueues (signal retry) hit the constraint; caller treats as idempotent success.
- **Partial index on unresolved rows** — the hot query is "show me unresolved outbox items" during reconciliation. Partial index keeps it cheap even as resolved rows accumulate.
- **Keep resolved rows** — don't delete on materialization. Retain briefly (N days) so we can forensic-trace "did this signal actually process?" A GC task or cron can prune; out of FEAT-008 scope.

Actually, the brief says "deletes the pending row" (`_materialize_aux` in T-167). Simpler: **delete on resolve**, don't retain. That matches the brief and keeps the table small. The `resolved_at` column is redundant under that model — drop it.

Revised schema:

```python
class PendingAuxWrite(Base):
    __tablename__ = "pending_aux_writes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    signal_name: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_pending_aux_writes_entity_id", "entity_id"),
        Index("ix_pending_aux_writes_enqueued_at", "enqueued_at"),
    )
```

### Step 2: Migration
**File:** `src/app/migrations/versions/YYYY_MM_DD_add_pending_aux_writes.py`
**Action:** Create

`uv run alembic revision --autogenerate -m "add pending_aux_writes (FEAT-008)"`. Verify the autogenerate produces:
- `op.create_table` with the columns above
- `op.create_index` for the two indexes
- Unique constraint on `correlation_id`

Hand-edit to match. Confirm downgrade cleanly drops.

### Step 3: Update column-expectation tests
**File:** `tests/modules/ai/test_models.py`
**Action:** Modify

If `test_models.py` asserts on the full set of tables (common pattern), add `pending_aux_writes` with its expected column set.

### Step 4: Smoke test
**File:** `tests/modules/ai/test_models.py`
**Action:** Modify

Add a round-trip test:
- Insert a `PendingAuxWrite` row.
- Query by `correlation_id` — round-trips.
- Attempt to insert a second row with the same `correlation_id` — `IntegrityError`.

No service-layer logic yet — that's T-167. This task just proves the schema.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | Add `PendingAuxWrite`. |
| `src/app/migrations/versions/YYYY_MM_DD_add_pending_aux_writes.py` | Create | Migration. |
| `tests/modules/ai/test_models.py` | Modify | Column assertions + round-trip + unique constraint. |

## Edge Cases & Risks
- **JSONB payload size.** Each signal's full payload is stored. Plan payloads (markdown blob) could be large — no issue at expected volume, but worth a comment: if we start storing multi-KB payloads, swap to a TEXT column + compression or prune payload to only the fields the reactor needs.
- **`correlation_id` generation.** Already flows through `PendingSignalContext` in FEAT-006 rc2. The outbox uses the same id — no new generation needed. Signal adapters in T-167 write both rows with the same `correlation_id`.
- **Migration order.** Runs after the FEAT-007 `github_check_id` migration; `down_revision` points at that. Autogenerate handles it.

## Acceptance Verification
- [ ] `PendingAuxWrite` model exists with the documented schema.
- [ ] Migration reversible (`alembic upgrade head && alembic downgrade -1` clean).
- [ ] Unique constraint on `correlation_id` enforced.
- [ ] `test_models.py` column expectations pass.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/test_models.py` green.
