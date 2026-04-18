"""Tests for TraceStore protocol and NoopTraceStore implementation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.ai.enums import StepStatus, WebhookEventType
from app.modules.ai.schemas import PolicyCallDto, StepDto, WebhookEventDto
from app.modules.ai.trace import NoopTraceStore, TraceStore, get_trace_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RUN_ID = uuid.uuid4()


def _make_step_dto() -> StepDto:
    return StepDto(
        id=uuid.uuid4(),
        step_number=1,
        node_name="generate_code",
        status=StepStatus.COMPLETED,
        node_inputs={"file": "main.py"},
        node_result={"ok": True},
        dispatched_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC),
    )


def _make_policy_call_dto() -> PolicyCallDto:
    return PolicyCallDto(
        id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        selected_tool="run_tests",
        tool_arguments={"suite": "unit"},
        available_tools=[{"name": "run_tests"}, {"name": "deploy"}],
        input_tokens=150,
        output_tokens=30,
        latency_ms=420,
        created_at=datetime.now(tz=UTC),
    )


def _make_webhook_event_dto() -> WebhookEventDto:
    return WebhookEventDto(
        id=uuid.uuid4(),
        event_type=WebhookEventType.NODE_FINISHED,
        engine_run_id="eng-run-001",
        payload={"node": "generate_code", "status": "ok"},
        signature_ok=True,
        received_at=datetime.now(tz=UTC),
        processed_at=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestTraceStoreProtocol:
    def test_noop_is_instance_of_protocol(self) -> None:
        store = NoopTraceStore()
        assert isinstance(store, TraceStore)


# ---------------------------------------------------------------------------
# NoopTraceStore behaviour
# ---------------------------------------------------------------------------


class TestNoopRecordStep:
    @pytest.mark.asyncio
    async def test_record_step_accepts_call(self) -> None:
        store = NoopTraceStore()
        result = await store.record_step(_RUN_ID, _make_step_dto())
        assert result is None


class TestNoopRecordPolicyCall:
    @pytest.mark.asyncio
    async def test_record_policy_call_accepts_call(self) -> None:
        store = NoopTraceStore()
        result = await store.record_policy_call(_RUN_ID, _make_policy_call_dto())
        assert result is None


class TestNoopRecordWebhookEvent:
    @pytest.mark.asyncio
    async def test_record_webhook_event_accepts_call(self) -> None:
        store = NoopTraceStore()
        result = await store.record_webhook_event(_RUN_ID, _make_webhook_event_dto())
        assert result is None


class TestNoopOpenRunStream:
    @pytest.mark.asyncio
    async def test_open_run_stream_returns_empty_iterator(self) -> None:
        store = NoopTraceStore()
        items: list[StepDto | PolicyCallDto | WebhookEventDto] = []
        async for entry in await store.open_run_stream(_RUN_ID):
            items.append(entry)
        assert items == []


class TestNoopTailRunStream:
    @pytest.mark.asyncio
    async def test_tail_yields_nothing_without_follow(self) -> None:
        store = NoopTraceStore()
        items = [item async for item in store.tail_run_stream(_RUN_ID)]
        assert items == []

    @pytest.mark.asyncio
    async def test_tail_yields_nothing_with_follow(self) -> None:
        """Follow mode must return immediately on the noop backend — not hang."""
        store = NoopTraceStore()
        items = [item async for item in store.tail_run_stream(_RUN_ID, follow=True)]
        assert items == []

    @pytest.mark.asyncio
    async def test_tail_respects_kinds_and_since_without_effect(self) -> None:
        store = NoopTraceStore()
        items = [
            item
            async for item in store.tail_run_stream(
                _RUN_ID,
                follow=False,
                since=datetime.now(tz=UTC),
                kinds=frozenset({"step"}),
            )
        ]
        assert items == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetTraceStore:
    def test_returns_noop_when_backend_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import get_settings
        from app.modules.ai.trace import _reset_trace_store_cache

        monkeypatch.setenv("TRACE_BACKEND", "noop")
        get_settings.cache_clear()
        _reset_trace_store_cache()
        try:
            store = get_trace_store()
            assert isinstance(store, NoopTraceStore)
        finally:
            _reset_trace_store_cache()
            get_settings.cache_clear()

    def test_return_type_satisfies_protocol(self) -> None:
        store = get_trace_store()
        assert isinstance(store, TraceStore)
