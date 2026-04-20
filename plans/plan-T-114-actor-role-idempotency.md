# Implementation Plan: T-114 — `X-Actor-Role` dependency + signal idempotency helper

## Task Reference
- **Task ID:** T-114
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** None

## Overview
Two cross-cutting helpers used by every FEAT-006 signal endpoint: a FastAPI dependency for `X-Actor-Role` validation, and an idempotency check backed by a new `lifecycle_signals` table.

## Steps

### 1. Modify `src/app/modules/ai/dependencies.py`
- Add:
  ```python
  def require_actor_role(*allowed: ActorRole) -> Callable[..., ActorRole]:
      async def dep(x_actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None) -> ActorRole:
          if x_actor_role is None:
              raise ValidationError(code="actor-role-missing", detail="X-Actor-Role header is required")
          try:
              role = ActorRole(x_actor_role)
          except ValueError:
              raise ValidationError(code="actor-role-invalid", detail=f"Unknown role: {x_actor_role}")
          if role not in allowed:
              raise AuthError(code="actor-role-forbidden", detail=f"Role {role} not allowed for this endpoint", http_status=403)
          return role
      return dep
  ```
- Ensure `AuthError` maps to `403` in the global handler.

### 2. Modify `src/app/modules/ai/models.py`
- New model `LifecycleSignal(Base)`:
  ```python
  class LifecycleSignal(Base):
      __tablename__ = "lifecycle_signals"
      key: Mapped[str] = mapped_column(primary_key=True)
      entity_id: Mapped[UUID] = mapped_column(nullable=False)
      signal_name: Mapped[str] = mapped_column(nullable=False)
      recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
  ```

### 3. Create `src/app/migrations/versions/<ts>_add_lifecycle_signals.py`
- Autogenerate + rename.

### 4. Create `src/app/modules/ai/lifecycle/idempotency.py`
- Functions:
  - `def compute_signal_key(entity_id: UUID, signal_name: str, payload: Mapping[str, Any]) -> str` — `sha256(json.dumps([str(entity_id), signal_name, payload], sort_keys=True, separators=(',',':')).encode()).hexdigest()`.
  - `async def check_and_record(session: AsyncSession, *, key: str, entity_id: UUID, signal_name: str) -> tuple[bool, datetime]` — `INSERT ... ON CONFLICT DO NOTHING RETURNING recorded_at`; returns `(is_new, recorded_at)`.

### 5. Create `tests/modules/ai/test_dependencies.py` (or extend)
- Tests:
  - Missing header → `400`.
  - Unknown role value → `400`.
  - Role not in allowed set → `403`.
  - Allowed role returns the enum.

### 6. Create `tests/modules/ai/lifecycle/test_idempotency.py`
- Hash stability: same `(entity_id, name, payload)` → same key; different payload → different key; order-independence (dict key order doesn't matter).
- `check_and_record`: first call returns `(True, ts)`; second returns `(False, same ts)`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/dependencies.py` | Modify | `require_actor_role` dep. |
| `src/app/modules/ai/models.py` | Modify | New `LifecycleSignal`. |
| `src/app/modules/ai/lifecycle/idempotency.py` | Create | Hash + check-and-record. |
| `src/app/migrations/versions/<ts>_add_lifecycle_signals.py` | Create | Migration. |
| `tests/modules/ai/test_dependencies.py` | Create/Modify | Actor-role tests. |
| `tests/modules/ai/lifecycle/test_idempotency.py` | Create | Hash/dedupe tests. |

## Edge Cases & Risks
- **JSON canonicalization** — `json.dumps(..., sort_keys=True, separators=(',',':'), default=str)`; be consistent about handling UUID/datetime via `default=str` to avoid non-determinism.
- **Unbounded growth** — flag in FEAT-006's risks section (T-127's docs sweep will note it). Retention is out of scope.
- **Middleware temptation** — don't push idempotency into FastAPI middleware (body is consumed at route handler). Keep it inside handlers.

## Acceptance Verification
- [ ] `require_actor_role` dependency behaves per spec on all four cases.
- [ ] `LifecycleSignal` table migrates cleanly.
- [ ] `compute_signal_key` deterministic across runs.
- [ ] `check_and_record` returns `(True, ts)` then `(False, same ts)`.
- [ ] `uv run pyright`, `ruff`, tests green.
