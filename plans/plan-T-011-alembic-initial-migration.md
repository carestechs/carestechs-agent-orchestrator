# Implementation Plan: T-011 — Alembic init + initial migration

## Task Reference
- **Task ID:** T-011
- **Type:** Database
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Addresses AC-1 (`alembic upgrade head` succeeds), AC-5 (round-trip), and the AC-8 Docker image migration path.

## Overview
Initialize Alembic with the async env template, point `target_metadata` at `Base.metadata`, and generate the initial migration creating all five tables. The migration must round-trip cleanly (`upgrade head` → `downgrade base` → `upgrade head`).

## Implementation Steps

### Step 1: Create `alembic.ini` at repo root
**File:** `alembic.ini`
**Action:** Create

Minimal config pointing at `src/app/migrations` as the script location. `sqlalchemy.url` is a placeholder — the real URL comes from `Settings` in `env.py`.

### Step 2: Create async `env.py`
**File:** `src/app/migrations/env.py`
**Action:** Create

Use the canonical SQLAlchemy async Alembic pattern:
- Import `Base.metadata` from `app.core.database` (this triggers model registration).
- Import `app.modules.ai.models` to ensure all models are loaded before autogenerate.
- Import `Settings` via `app.config.get_settings()` for the database URL.
- Use `run_async_migrations()` with `connectable = create_async_engine(url)`.
- `run_sync` inside the connection for `context.run_migrations()`.

### Step 3: Create `script.py.mako`
**File:** `src/app/migrations/script.py.mako`
**Action:** Create

Standard Alembic revision template.

### Step 4: Create versions directory
**File:** `src/app/migrations/versions/`
**Action:** Create directory

### Step 5: Generate initial migration
**Action:** Run `alembic revision --autogenerate -m "initial schema"`

This produces the initial migration file. Manually verify it includes all five tables, CHECK constraints, indexes, and unique constraints.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `alembic.ini` | Create | Alembic configuration |
| `src/app/migrations/env.py` | Create | Async env with Settings integration |
| `src/app/migrations/script.py.mako` | Create | Revision template |
| `src/app/migrations/versions/*.py` | Create | Initial schema migration |

## Edge Cases & Risks

- **Async engine in env.py.** Must use `run_sync` inside the async connection — common gotcha.
- **Model imports.** `env.py` must import the models module so `Base.metadata` is populated before autogenerate runs.
- **CHECK constraints.** `autogenerate` may not detect text CHECK constraints. May need hand-authored additions.
- **`server_default` for JSONB.** The `'{}'` default on `run_memory.data` needs quoting in the migration.

## Acceptance Verification

- [ ] `alembic.ini` at repo root.
- [ ] `env.py` uses async engine from settings.
- [ ] `upgrade head` + `downgrade base` + `upgrade head` all succeed.
- [ ] `autogenerate` against a clean database after `upgrade head` produces no diff.
