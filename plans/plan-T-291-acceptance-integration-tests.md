# Implementation Plan: T-291 — Acceptance-named integration tests

## Task Reference
- **Task ID:** T-291
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1..AC-4 + AC-7 + brief Section 9 edge cases. Per-task suites can stay narrow; this file is the regression net mapped by AC name.

## Overview
One file with eight tests, each named for the AC or edge case it covers. Reviewer-facing — the AC mapping is obvious from a glance at the test list. Real Postgres (CLAUDE.md: no SQL mocks). Reuses the FEAT-013 parity-test cleanup pattern.

## Implementation Steps

### Step 1: Shared fixtures
**File:** `tests/integration/test_work_item_upload_acceptance.py`
**Action:** Create
```python
from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.models import Run, WorkItem
from app.modules.ai.enums import WorkItemType


@pytest_asyncio.fixture(loop_scope="function")
async def isolated_external_ref(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """Yield a unique external_ref and clean it up after the test."""
    ref = f"FEAT-{uuid.uuid4().int % 1_000_000_000}"
    yield ref
    async with session_factory() as session, session.begin():
        # Clean up any runs that reference this work-item first
        wi = await session.scalar(select(WorkItem).where(WorkItem.external_ref == ref))
        if wi is not None:
            await session.execute(delete(Run).where(Run.intake["workItem"]["id"].as_string() == ref))
            await session.execute(delete(WorkItem).where(WorkItem.id == wi.id))


def _payload(ref: str, kind: str = "FEAT", content: str | None = "# Test\nbody") -> dict:
    body: dict = {"agentRef": "lifecycle-agent@0.3.0", "intake": {"workItem": {"id": ref, "kind": kind}}}
    if content is not None:
        body["intake"]["workItem"]["content"] = content
    return body
```

### Step 2: The eight tests
**File:** `tests/integration/test_work_item_upload_acceptance.py`
**Action:** Modify (continue)
```python
@pytest.mark.asyncio
async def test_first_sight_inserts_row(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    isolated_external_ref: str,
) -> None:
    """AC-1: first POST with content INSERTs a work_items row + starts a run."""
    resp = await client.post("/api/v1/runs", json=_payload(isolated_external_ref))
    assert resp.status_code == 202
    async with session_factory() as session:
        wi = await session.scalar(select(WorkItem).where(WorkItem.external_ref == isolated_external_ref))
        assert wi is not None
        assert wi.body_md == "# Test\nbody"
        assert wi.body_sha256 == hashlib.sha256(b"# Test\nbody").hexdigest()


@pytest.mark.asyncio
async def test_second_sight_reuses_row(...):
    """AC-2: second POST without content reuses the existing row."""
    # First call with content
    await client.post("/api/v1/runs", json=_payload(ref))
    # Second call without content
    resp = await client.post("/api/v1/runs", json=_payload(ref, content=None))
    assert resp.status_code == 202
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.external_ref == ref))
        assert count == 1


@pytest.mark.asyncio
async def test_content_conflict_returns_409(...):
    """AC-3: differing content for an existing ref returns 409 with diff."""
    await client.post("/api/v1/runs", json=_payload(ref, content="original"))
    resp = await client.post("/api/v1/runs", json=_payload(ref, content="different"))
    assert resp.status_code == 409
    body = resp.json()
    assert body["type"].endswith("/work-item-content-conflict")
    assert "storedSha256" in body["meta"]
    assert "uploadedSha256" in body["meta"]


@pytest.mark.asyncio
async def test_no_row_no_content_returns_400(...):
    """AC-4: POST for an unknown ref without content returns 400."""
    resp = await client.post("/api/v1/runs", json=_payload(ref, content=None))
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/work-item-not-registered")


@pytest.mark.asyncio
async def test_legacy_work_item_path_still_completes_with_warning(client, caplog, ...):
    """AC-7: legacy intake.workItemPath continues working with a deprecation log."""
    payload = {
        "agentRef": "lifecycle-agent@0.3.0",
        "intake": {"workItemPath": "docs/work-items/FEAT-005-lifecycle-agent.md"},
    }
    resp = await client.post("/api/v1/runs", json=payload)
    assert resp.status_code == 202
    deprecation_logs = [r for r in caplog.records if "intake-work-item-path-deprecated" in r.message]
    assert len(deprecation_logs) == 1


@pytest.mark.asyncio
async def test_kind_conflict_returns_409(...):
    """Edge case (Section 9): kind mismatch on second sight returns 409."""
    await client.post("/api/v1/runs", json=_payload(ref, kind="FEAT"))
    resp = await client.post("/api/v1/runs", json=_payload(ref, kind="BUG"))
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/work-item-kind-conflict")


@pytest.mark.asyncio
async def test_concurrent_first_sight_race_resolves_idempotently(...):
    """Edge case (Section 9): two parallel first-sight POSTs with identical content
    settle to one row."""
    payload = _payload(ref, content="same body")
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/runs", json=payload),
        client.post("/api/v1/runs", json=payload),
    )
    assert r1.status_code == 202
    assert r2.status_code == 202
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(WorkItem).where(WorkItem.external_ref == ref)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_oversized_body_returns_413(monkeypatch, client, ...):
    """Edge case (Section 9): body larger than INTAKE_WORK_ITEM_MAX_BYTES returns 413."""
    monkeypatch.setenv("INTAKE_WORK_ITEM_MAX_BYTES", "128")
    # Recreate the app under the new setting (or use a fresh client fixture)
    big_content = "x" * 200
    resp = await client.post("/api/v1/runs", json=_payload(ref, content=big_content))
    assert resp.status_code == 413
```

### Step 3: Docstring AC mapping
Each test's docstring cites the AC or edge case it covers (matches the format above). Reviewers can scan the file in a minute and confirm coverage.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_work_item_upload_acceptance.py` | Create | Eight AC-mapped tests + shared fixtures |

## Edge Cases & Risks
- **Run cleanup is awkward** because `Run.intake` is JSONB. The fixture uses a JSONB index expression to delete dependent runs; if SQLAlchemy doesn't support the indexing path on the project's version, fall back to a raw SQL `DELETE FROM runs WHERE intake->'workItem'->>'id' = :ref`.
- **Concurrent-race test depends on actual asyncio scheduling.** May be flaky on slow CI. Mitigation: a `for _ in range(5)` outer loop ensures the race surfaces; the IntegrityError path is exercised at least once across the iterations.
- **Settings override for 413 test.** If `monkeypatch.setenv` doesn't rebuild settings (pydantic caches), use the existing project pattern for env-var overrides in tests (likely `Settings(...).model_copy(update=...)` or an `override_settings` fixture).
- **`caplog` and FastAPI loggers.** The deprecation log uses the project's structured logger; ensure the test propagates the right logger level (`caplog.set_level(logging.WARNING)`).

## Acceptance Verification
- [ ] All eight tests pass under `uv run pytest tests/integration/test_work_item_upload_acceptance.py`.
- [ ] Each test docstring names the AC or edge case it covers.
- [ ] No mocks of `register_work_item` or the Postgres engine — full integration.
- [ ] Concurrent-race test passes three repeated runs without flakes.
