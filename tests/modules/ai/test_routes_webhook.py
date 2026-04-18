"""Integration tests for POST /hooks/engine/events.

Covers all five branches per T-016:

1. Valid signature + known engineRunId    → 202 + row persisted (signature_ok=True).
2. Valid signature + duplicate dedupe_key → 202 + exactly 1 row total (idempotent).
3. Valid signature + unknown engineRunId  → 404 + 0 rows persisted (FK would fail).
4. Invalid signature + known run          → 401 + row persisted (signature_ok=False).
5. Valid signature + malformed payload    → 400 (Pydantic handler) + 0 rows.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import Run, Step, WebhookEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_run_id() -> str:
    return "eng-run-xyz-123"


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_step(
    db_session: AsyncSession, engine_run_id: str
) -> Step:
    """Insert a Run + Step whose engine_run_id matches the webhook payload."""
    run = Run(
        agent_ref="test-agent@1.0",
        agent_definition_hash="sha256:" + "a" * 64,
        intake={"brief": "seed"},
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/test.jsonl",
    )
    db_session.add(run)
    await db_session.flush()

    step = Step(
        run_id=run.id,
        step_number=1,
        node_name="analyze-brief",
        node_inputs={"brief": "seed"},
        engine_run_id=engine_run_id,
    )
    db_session.add(step)
    await db_session.flush()
    return step


def _payload(engine_run_id: str, dedupe_key: str = "evt-001") -> dict[str, object]:
    return {
        "eventType": "node_finished",
        "engineRunId": engine_run_id,
        "engineEventId": dedupe_key,
        "occurredAt": "2026-04-17T12:00:00Z",
        "payload": {"result": "ok"},
    }


async def _count_events(db_session: AsyncSession) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(WebhookEvent)
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidSignature:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_known_run_returns_202_and_persists(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        seeded_step: Step,
        engine_run_id: str,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        body = json.dumps(_payload(engine_run_id)).encode()
        resp = await client.post(
            "/hooks/engine/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Signature": webhook_signer(body),
            },
        )
        assert resp.status_code == 202, resp.text
        envelope = resp.json()
        assert envelope["data"]["received"] is True

        # DB assertions
        await db_session.commit()  # flush the outer savepoint view
        rows = (await db_session.execute(select(WebhookEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].signature_ok is True
        assert rows[0].dedupe_key == "evt-001"
        assert rows[0].step_id == seeded_step.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_duplicate_dedupe_key_is_idempotent(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        seeded_step: Step,
        engine_run_id: str,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        body = json.dumps(_payload(engine_run_id, dedupe_key="evt-dup")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Engine-Signature": webhook_signer(body),
        }

        first = await client.post("/hooks/engine/events", content=body, headers=headers)
        second = await client.post("/hooks/engine/events", content=body, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 202
        # Same event id returned both times
        assert first.json()["data"]["eventId"] == second.json()["data"]["eventId"]
        # Only one row persisted
        assert await _count_events(db_session) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_engine_run_id_returns_404(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        body = json.dumps(_payload("eng-run-unknown")).encode()
        resp = await client.post(
            "/hooks/engine/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Signature": webhook_signer(body),
            },
        )
        assert resp.status_code == 404
        problem = resp.json()
        assert problem["status"] == 404
        assert "unknown" in problem["detail"].lower()
        # Nothing persisted (FK prevents it)
        assert await _count_events(db_session) == 0


class TestInvalidSignature:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_bad_signature_401_but_event_persisted(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        seeded_step: Step,
        engine_run_id: str,
    ) -> None:
        body = json.dumps(_payload(engine_run_id, dedupe_key="evt-bad-sig")).encode()
        resp = await client.post(
            "/hooks/engine/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Signature": "sha256=" + "00" * 32,  # wrong digest
            },
        )
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/problem+json")

        # Event is persisted with signature_ok=False per CLAUDE.md
        await db_session.commit()
        rows = (await db_session.execute(select(WebhookEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].signature_ok is False
        assert rows[0].dedupe_key == "evt-bad-sig"


class TestMalformedBody:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_required_field_returns_400(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        # Missing engineRunId + engineEventId — Pydantic rejects before
        # reaching the service, so nothing is persisted.
        body = json.dumps({"eventType": "node_finished"}).encode()
        resp = await client.post(
            "/hooks/engine/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Signature": webhook_signer(body),
            },
        )
        assert resp.status_code == 400
        problem = resp.json()
        assert problem["status"] == 400
        assert "errors" in problem
        assert await _count_events(db_session) == 0
