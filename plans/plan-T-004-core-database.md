# Implementation Plan: T-004 — Core database module

## Task Reference
- **Task ID:** T-004
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Required by data-layer tasks (T-009..T-011) and the health check (T-014). Centralizes session lifecycle per `adrs/python/sqlalchemy-async.md`.

## Overview
Create the async SQLAlchemy engine, `async_sessionmaker`, `DeclarativeBase` subclass, and a FastAPI `get_db_session` dependency with correct commit/rollback/close semantics. Single source of truth for DB access across the app.

## Implementation Steps

### Step 1: Create engine and sessionmaker
**File:** `src/app/core/database.py`
**Action:** Modify

- Import `create_async_engine`, `async_sessionmaker`, `AsyncSession`, `AsyncAttrs` from `sqlalchemy.ext.asyncio`; `DeclarativeBase` from `sqlalchemy.orm`.
- Factory `make_engine(settings: Settings) -> AsyncEngine`: `create_async_engine(str(settings.database_url), pool_pre_ping=True, echo=False)`. Don't build a module-level engine — build via factory so tests and Alembic can use different URLs.
- Module-level `_engine: AsyncEngine | None = None` + `get_engine() -> AsyncEngine` that lazily builds via `get_settings()` on first call. This keeps imports cheap.
- `sessionmaker = async_sessionmaker(bind=get_engine, expire_on_commit=False, class_=AsyncSession)` — bind via a callable so settings aren't read at import time. If `async_sessionmaker` requires an eager engine, create a thin `make_sessionmaker(engine)` factory instead.
- `class Base(AsyncAttrs, DeclarativeBase): pass` — single metadata for Alembic.

### Step 2: Implement `get_db_session`
**File:** `src/app/core/database.py`
**Action:** Modify

```python
async def get_db_session() -> AsyncIterator[AsyncSession]:
    session = sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

This is the canonical FastAPI async DB dependency pattern. Route handlers type-annotate with `Annotated[AsyncSession, Depends(get_db_session)]`.

### Step 3: Export
**File:** `src/app/core/database.py`
**Action:** Modify

Ensure `Base`, `get_db_session`, `get_engine`, `sessionmaker` (or `make_sessionmaker`) are module-level names — T-009 and T-011 import from here.

### Step 4: Unit test
**File:** `tests/core/test_database.py`
**Action:** Create

- Test opens a session via `get_db_session`, executes `await session.execute(text("SELECT 1"))`, asserts the scalar is `1`. Uses the session-scoped Postgres fixture from T-024. Since T-024 lands later, initially mark this test `@pytest.mark.xfail(reason="awaits T-024 conftest fixture", strict=False)` or guard with `pytest.importorskip`. Remove the skip when T-024 merges.
- Test that an exception inside the generator triggers rollback: use a context manager wrapper that raises and assert no partial state is committed (easiest via a transient table created earlier).

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/core/database.py` | Modify | Engine, sessionmaker, `Base`, `get_db_session` |
| `tests/core/test_database.py` | Create | SELECT 1 + rollback semantics |

## Edge Cases & Risks

- **Engine built at import time is a common footgun.** If any module does `engine = make_engine(get_settings())` at import, CLI `--help` requires a DB URL. Gate engine creation behind `get_engine()` and document: never call at module scope.
- **`expire_on_commit=False`.** Needed because FastAPI routes often return ORM objects after the session commits in the finally block — otherwise attribute access raises `DetachedInstanceError`. Our DTO boundary (Pydantic) largely makes this moot, but leave it off for safety.
- **`pool_pre_ping=True`** causes a `SELECT 1` on each checkout. Slight overhead, big reliability win for dev/Docker restarts. Keep it.
- **Alembic integration** (T-011) needs `Base.metadata` to include every model. T-009 must `import app.modules.ai.models` in `env.py` (or via a `src/app/modules/__init__.py` barrel — avoid auto-discovery magic).

## Acceptance Verification

- [ ] **Engine uses asyncpg + pool_pre_ping:** inspect `get_engine().pool.__class__.__name__` (QueuePool) and `get_engine().dialect.driver == "asyncpg"`.
- [ ] **`get_db_session` lifecycle:** happy-path commits (verified by committed row visible in a second session); exception triggers rollback (verified by row absent).
- [ ] **`Base` importable:** `from app.core.database import Base; assert issubclass(Base, DeclarativeBase)`.
- [ ] **SELECT 1 smoke:** `test_database.py::test_select_1` passes against the fixture DB (after T-024).
