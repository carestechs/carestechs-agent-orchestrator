# Implementation Plan: T-088 — `RunSignal` entity + Alembic migration + DTO

## Task Reference
- **Task ID:** T-088
- **Type:** Database
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** None

## Overview
Add a new append-only table `run_signals` for operator-injected signals (v1 uses only `implementation-complete`). The row is persisted BEFORE the supervisor is woken — mirror of the webhook pipeline's "persist → reconcile → wake" invariant. Idempotency via a UNIQUE `dedupe_key` on `(run_id, name, task_id)`.

## Steps

### 1. Modify `src/app/modules/ai/models.py`
Add the SQLAlchemy model:
```python
class RunSignal(Base):
    __tablename__ = "run_signals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), nullable=False, index=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    __table_args__ = (
        Index("ix_run_signals_run_id_received_at", "run_id", "received_at"),
        UniqueConstraint("dedupe_key", name="uq_run_signals_dedupe_key"),
    )
```

### 2. Modify `src/app/modules/ai/schemas.py`
Add two Pydantic models with camelCase aliases:
```python
class SignalCreateRequest(BaseModel):
    model_config = _CAMEL_CONFIG
    name: Literal["implementation-complete"]
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

class RunSignalDto(BaseModel):
    model_config = _CAMEL_CONFIG
    id: UUID
    run_id: UUID
    name: str
    task_id: str | None
    payload: dict[str, Any]
    received_at: datetime
    dedupe_key: str
```

### 3. Modify `src/app/modules/ai/repository.py`
Add:
```python
async def create_run_signal(session, *, run_id, name, task_id, payload, dedupe_key) -> tuple[RunSignal, bool]:
    """Return (row, created). On conflict returns the existing row with created=False."""
    stmt = (
        pg_insert(RunSignal)
        .values(id=uuid7(), run_id=run_id, name=name, task_id=task_id,
                payload=payload, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(RunSignal)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row, True
    existing = await session.scalar(select(RunSignal).where(RunSignal.dedupe_key == dedupe_key))
    assert existing is not None  # UNIQUE constraint guarantees this
    return existing, False
```

### 4. Create Alembic migration
- Run: `uv run alembic revision --autogenerate -m "add run_signals"`.
- Inspect the generated file under `src/app/migrations/versions/`; rename to `2026_04_18_add_run_signals.py` to match project convention.
- Verify `upgrade()` creates the table + index + unique constraint; `downgrade()` drops them. Back out any unrelated diff.

### 5. Modify `docs/data-model.md`
- Insert a new `### RunSignal` section between `WebhookEvent` and `RunMemory`, matching the other entities' shape (field table + indexes + business rules).
- Do NOT add a changelog entry here — T-106 sweeps all changelogs together.

### 6. Create `tests/modules/ai/test_repository_run_signal.py`
Four cases:
1. Happy insert → returned with `created=True`.
2. Duplicate `dedupe_key` → returns existing row, `created=False`.
3. Unknown `run_id` → `IntegrityError` (FK violation).
4. Query helper returns rows ordered by `received_at`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | `RunSignal` SQLAlchemy model. |
| `src/app/modules/ai/schemas.py` | Modify | `SignalCreateRequest`, `RunSignalDto`. |
| `src/app/modules/ai/repository.py` | Modify | `create_run_signal` with ON CONFLICT. |
| `src/app/migrations/versions/2026_04_18_add_run_signals.py` | Create | Alembic migration. |
| `docs/data-model.md` | Modify | New `RunSignal` entity section. |
| `tests/modules/ai/test_repository_run_signal.py` | Create | 4 repository tests. |

## Edge Cases & Risks
- **Alembic collateral drift**: autogenerate may include unrelated `ALTER TABLE` if models drifted since the last migration. Diff carefully; back out anything unrelated.
- **Migration reversibility**: round-trip `upgrade → downgrade → upgrade` locally on Postgres before merging.
- **UUIDv7 helper**: confirm the project already has `uuid7` (it does per FEAT-001); reuse it.
- **JSONB default on Postgres**: `server_default="{}"` needs the `::jsonb` cast on some configs; use `text("'{}'::jsonb")` if the migration complains.
- **Namespace collision**: "RunSignal" and "RunMemory" are both per-run entities — don't accidentally reuse the `run_memory` table pattern (PK = run_id). Signals have their own UUID PK.

## Acceptance Verification
- [ ] `RunSignal` model with all columns + index + unique constraint.
- [ ] DTOs `SignalCreateRequest` + `RunSignalDto` validate.
- [ ] Repository `create_run_signal` returns `(row, created)`.
- [ ] Migration round-trips clean on local Postgres.
- [ ] `data-model.md` updated with `RunSignal` section (changelog deferred).
- [ ] 4 repository tests pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
