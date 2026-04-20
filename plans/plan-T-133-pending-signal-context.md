# Implementation Plan: T-133 — Signal adapters + correlation-based context passing

## Task Reference
- **Task ID:** T-133
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-132

## Overview
Signal adapters now compute a correlation id, stash the signal payload in `pending_signal_context`, call the engine via the rewired transition, and return. The engine's webhook (T-130's reactor) consumes the context row to write `Approval` / `TaskAssignment` / `TaskPlan` / `TaskImplementation` with the original payload data.

## Steps

### 1. Modify `src/app/modules/ai/models.py`
- Add:
  ```python
  class PendingSignalContext(Base):
      __tablename__ = "pending_signal_context"
      correlation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
      signal_name: Mapped[str]
      payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
      created_at: Mapped[datetime] = mapped_column(server_default=func.now())
  ```

### 2. Alembic migration — `add_pending_signal_context`.

### 3. Modify `src/app/modules/ai/lifecycle/service.py`
- Extract a helper:
  ```python
  async def _with_correlation(db, signal_name, payload) -> uuid.UUID:
      corr = uuid.uuid4()
      db.add(PendingSignalContext(
          correlation_id=corr,
          signal_name=signal_name,
          payload=payload,
      ))
      await db.flush()
      return corr
  ```
- Every service adapter calls `_with_correlation(...)` before the engine transition and passes the `correlation_id` through to the engine client.
- Rejections (no engine transition) skip `_with_correlation` — they write their `Approval` row inline.

### 4. Modify `tests/modules/ai/test_router_*.py`
- Mock the engine client; drive signal endpoint; assert:
  - `PendingSignalContext` row exists with correct `signal_name` + `payload`.
  - Engine client was called with the matching `correlation_id`.
- For the reactor-side effects (`Approval` written, etc.) — drive a synthetic engine webhook in the same test, then assert aux rows + that `PendingSignalContext` row is gone.

### 5. Update `src/app/modules/ai/lifecycle/reactor.py` (extends T-130)
- Guard: if no `PendingSignalContext` row for `correlation_id`, log warning + skip aux writes (still do derivations).
- After successful aux writes, `DELETE FROM pending_signal_context WHERE correlation_id = ...`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | New entity. |
| Alembic migration | Create | Table. |
| `src/app/modules/ai/lifecycle/service.py` | Modify | Context helper + adapter updates. |
| `src/app/modules/ai/lifecycle/reactor.py` | Modify | Consume + delete context. |
| `tests/modules/ai/test_router_*.py` | Modify | Two-phase assertions. |

## Edge Cases & Risks
- **Stale rows.** If the engine webhook never arrives (engine outage), rows accumulate. Background cleanup / alarm is a follow-up.
- **Correlation-id leakage.** Correlation ids travel through engine transition metadata — make sure they're not considered secret. (They aren't; they're random UUIDs.)
- **Two adapters using the same correlation id.** Only possible via manual UUID collision; effectively zero. No guard needed.

## Acceptance Verification
- [ ] `PendingSignalContext` table + model + migration.
- [ ] Every signal adapter uses `_with_correlation` (except rejections).
- [ ] Reactor consumes + deletes context row on success.
- [ ] Route tests cover the two-phase flow.
- [ ] `uv run pyright`, `ruff`, tests green.
