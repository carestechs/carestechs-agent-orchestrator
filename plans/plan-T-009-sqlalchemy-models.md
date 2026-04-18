# Implementation Plan: T-009 — SQLAlchemy models for all five entities

## Task Reference
- **Task ID:** T-009
- **Type:** Database
- **Workflow:** standard
- **Complexity:** L
- **Rationale:** Addresses AC-5 (migration round-trips) and the entity half of AC-6. All subsequent runtime work reads/writes these tables.

## Overview
Implement `Run`, `Step`, `PolicyCall`, `WebhookEvent`, `RunMemory` in `modules/ai/models.py` exactly matching `docs/data-model.md`. Uses UUIDv7 via `uuid6`, `timestamptz` timestamps, text+CHECK for enums, JSONB for variable-shape fields. Shared string enums live in a new `enums.py` so both models and schemas (T-010) import from one source.

## Implementation Steps

### Step 1: Create shared enums module
**File:** `src/app/modules/ai/enums.py`
**Action:** Create

Define four `StrEnum` classes matching `docs/data-model.md` → Enums:
- `RunStatus`: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled`.
- `StepStatus`: `pending`, `dispatched`, `in_progress`, `completed`, `failed`.
- `StopReason`: `done_node`, `policy_terminated`, `budget_exceeded`, `error`, `cancelled`.
- `WebhookEventType`: `node_started`, `node_finished`, `node_failed`, `flow_terminated`.

Use `enum.StrEnum` (Python 3.11+). Values are snake_case strings matching the data model exactly.

### Step 2: UUIDv7 helper
**File:** `src/app/modules/ai/models.py`
**Action:** Modify

Add a `generate_uuid7() -> uuid.UUID` helper using `uuid6.uuid7()`. Use as the default for all PK columns via `default=generate_uuid7`.

### Step 3: Implement all five models
**File:** `src/app/modules/ai/models.py`
**Action:** Modify

All models inherit from `app.core.database.Base`. Column conventions:
- PKs: `Mapped[uuid.UUID]` with `mapped_column(primary_key=True, default=generate_uuid7)`.
- `timestamptz` → `DateTime(timezone=True)`.
- `created_at` uses `server_default=func.now()`.
- `updated_at` uses `server_default=func.now(), onupdate=func.now()`.
- Enums stored as `Text` with `CheckConstraint` listing allowed values.
- JSONB fields use `JSONB` from `sqlalchemy.dialects.postgresql`.
- Relationships are declared for type-checking but with `lazy="raise"` to prevent N+1 in async.

**Run:**
- Table `runs`. All fields per data model.
- Indexes: `(status, started_at DESC)`, `(agent_ref)`.
- `status` CHECK constraint on `RunStatus` values.
- `stop_reason` CHECK constraint on `StopReason` values (nullable).

**Step:**
- Table `steps`. All fields per data model.
- `UniqueConstraint("run_id", "step_number")`.
- Index on `engine_run_id`.
- `status` CHECK on `StepStatus` values.

**PolicyCall:**
- Table `policy_calls`. All fields per data model.
- `UniqueConstraint("step_id")`.
- Index on `(run_id, created_at)`.

**WebhookEvent:**
- Table `webhook_events`. All fields per data model.
- `UniqueConstraint("dedupe_key")`.
- Index on `(run_id, received_at)`.

**RunMemory:**
- Table `run_memory`. PK is `run_id` (also FK to Run).
- `data` JSONB default `{}`.

### Step 4: Tests
**File:** `tests/modules/ai/test_models.py`
**Action:** Create

- Assert every documented field exists on the correct model class.
- Assert PK column names and types.
- Assert CHECK constraints contain the right enum values.
- Assert unique constraints exist where documented.
- Assert table names match convention (snake_case, plural / `run_memory`).

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/enums.py` | Create | `RunStatus`, `StepStatus`, `StopReason`, `WebhookEventType` StrEnums |
| `src/app/modules/ai/models.py` | Modify | All five ORM models with indexes, constraints, JSONB fields |
| `tests/modules/ai/test_models.py` | Create | Field-presence + constraint assertions |

## Edge Cases & Risks

- **`uuid6` import.** Verify `uuid6.uuid7()` returns a `uuid.UUID` compatible with SQLAlchemy's `Uuid` type. If not, cast explicitly.
- **`JSONB` import path.** Must use `sqlalchemy.dialects.postgresql.JSONB`, not the generic `JSON` type, for proper Postgres support.
- **CHECK constraint on nullable column.** `stop_reason` is nullable — the CHECK must allow `NULL` (Postgres CHECK constraints pass on NULL by default, so no extra clause needed).
- **`server_default=func.now()` vs `default=func.now()`.** Use `server_default` for `created_at`/`received_at` (DB handles it). Use `onupdate` for `updated_at` (SQLAlchemy emits it on flush).
- **Relationship `lazy="raise"`.** Prevents accidental lazy loads in async context. All relationship access must be explicit via `selectinload` / `joinedload`.

## Acceptance Verification

- [ ] Each model's fields, nullability, defaults, and types match `docs/data-model.md` exactly.
- [ ] Enum fields use text + CHECK with the allowed values from `docs/data-model.md`.
- [ ] Indexes and unique constraints match the data model.
- [ ] JSONB fields use `sqlalchemy.dialects.postgresql.JSONB`.
- [ ] Model classes carry a docstring noting append-only semantics where applicable.
- [ ] `uv run pyright` and `uv run ruff check .` pass.
