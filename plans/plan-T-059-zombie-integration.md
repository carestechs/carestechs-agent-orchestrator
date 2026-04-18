# Implementation Plan: T-059 — Integration: zombie-run reconciliation (extends T-045)

## Task Reference
- **Task ID:** T-059
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-045

## Overview
Insert a pre-existing `running` row, trigger app startup (lifespan runs), assert the row transitioned to `failed` + trace line written.

## Steps

### 1. Extend `tests/integration/test_lifespan_zombie_reconciliation.py`

Already partially built in T-045. Add:

```python
async def test_multiple_zombies_all_reconciled(...):
    # Insert 3 Runs with status=running at different started_at times.
    # Trigger lifespan startup.
    # Assert all 3 rows now status=failed with stop_reason=error.
    # Assert 3 JSONL files exist with final zombie-reason lines.
    # Assert startup logs contain "reconciled N=3 zombie runs".

async def test_zombie_reconciliation_idempotent(...):
    # Trigger lifespan twice (stop app, create new instance, start).
    # First startup: 1 zombie → reconciled.
    # Second startup: 0 zombies (already failed).
    # Assert no double-write to trace file.

async def test_graceful_shutdown_distinguishes_from_zombie(...):
    # Start app → spawn a real run → trigger shutdown(grace=0.1).
    # Assert the run ended with status=cancelled (not failed via zombie path).
    # Contrast: zombie reconciliation only touches rows left `running` by a *crashed* process.
```

### 2. Make lifespan test-friendly

- The fixture `app` in `tests/conftest.py` may skip lifespan (because it calls `create_app()` directly). For these tests, create a dedicated `lifespan_app` fixture that uses the FastAPI `LifespanManager` pattern or an `async with AsyncClient(transport=ASGITransport(app=create_app()))` which does trigger lifespan.

### 3. Update `tests/conftest.py`

Add optional `lifespan_client` fixture for integration tests needing real lifespan. Don't change the default `client` to avoid breaking existing unit tests.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifespan_zombie_reconciliation.py` | Modify | Add 3 scenarios. |
| `tests/conftest.py` | Modify | Add `lifespan_client` fixture. |

## Edge Cases & Risks
- Two test runs in sequence might reuse the same test DB session — ensure each fixture opens a fresh session so zombie state doesn't leak between tests.
- `lifespan_client` fixture must dispose the client (and trigger shutdown) in teardown. Use `async with`.

## Acceptance Verification
- [ ] 3 zombies all reconciled in one pass.
- [ ] Second lifespan pass does nothing (idempotent).
- [ ] Graceful shutdown ends runs `cancelled`, not `failed`.
- [ ] `lifespan_client` fixture works and doesn't regress other tests.
