"""Tests for the signal idempotency helper (FEAT-006 / T-114)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.lifecycle.idempotency import check_and_record, compute_signal_key


class TestComputeSignalKey:
    def test_deterministic(self) -> None:
        eid = uuid.uuid4()
        a = compute_signal_key(eid, "approve-task", {"b": 2, "a": 1})
        b = compute_signal_key(eid, "approve-task", {"a": 1, "b": 2})
        assert a == b

    def test_different_payload_different_key(self) -> None:
        eid = uuid.uuid4()
        a = compute_signal_key(eid, "approve-task", {"a": 1})
        b = compute_signal_key(eid, "approve-task", {"a": 2})
        assert a != b

    def test_different_entity_different_key(self) -> None:
        a = compute_signal_key(uuid.uuid4(), "x", {})
        b = compute_signal_key(uuid.uuid4(), "x", {})
        assert a != b

    def test_handles_uuid_in_payload(self) -> None:
        # Should not raise due to non-JSON-serialisable values.
        eid = uuid.uuid4()
        nested = uuid.uuid4()
        compute_signal_key(eid, "x", {"ref": nested})


@pytest.mark.asyncio
class TestCheckAndRecord:
    async def test_first_call_is_new(self, db_session: AsyncSession) -> None:
        key = "k-" + uuid.uuid4().hex
        eid = uuid.uuid4()
        is_new, ts = await check_and_record(
            db_session, key=key, entity_id=eid, signal_name="x"
        )
        await db_session.commit()
        assert is_new is True
        assert ts is not None

    async def test_replay_returns_not_new_with_original_ts(
        self, db_session: AsyncSession
    ) -> None:
        key = "k-" + uuid.uuid4().hex
        eid = uuid.uuid4()
        _, first_ts = await check_and_record(
            db_session, key=key, entity_id=eid, signal_name="x"
        )
        await db_session.commit()

        is_new, ts = await check_and_record(
            db_session, key=key, entity_id=eid, signal_name="x"
        )
        await db_session.commit()
        assert is_new is False
        assert ts == first_ts
