"""Tests for the JSONL ``TraceStore`` implementation (T-035)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.ai.enums import StepStatus, WebhookEventType
from app.modules.ai.schemas import PolicyCallDto, StepDto, WebhookEventDto
from app.modules.ai.trace import _reset_trace_store_cache, get_trace_store
from app.modules.ai.trace_jsonl import JsonlTraceStore


def _step_dto(n: int = 1) -> StepDto:
    return StepDto(
        id=uuid.uuid4(),
        step_number=n,
        node_name="analyze_brief",
        status=StepStatus.PENDING,
        node_inputs={"brief": "hi"},
    )


def _policy_call_dto() -> PolicyCallDto:
    return PolicyCallDto(
        id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        provider="stub",
        model="stub-model",
        selected_tool="analyze_brief",
        tool_arguments={"brief": "hi"},
        available_tools=[],
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        created_at=datetime.now(UTC),
    )


def _webhook_event_dto() -> WebhookEventDto:
    return WebhookEventDto(
        id=uuid.uuid4(),
        event_type=WebhookEventType.NODE_FINISHED,
        engine_run_id="eng-123",
        payload={"ok": True},
        signature_ok=True,
        received_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Basic writes
# ---------------------------------------------------------------------------


class TestAppend:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_created_on_first_write(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        await store.record_step(run_id, _step_dto())
        path = tmp_path / f"{run_id}.jsonl"
        assert path.is_file()
        line = json.loads(path.read_text().strip())
        assert line["kind"] == "step"
        assert line["data"]["stepNumber"] == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_three_writes_three_lines(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        await store.record_step(run_id, _step_dto(1))
        await store.record_policy_call(run_id, _policy_call_dto())
        await store.record_webhook_event(run_id, _webhook_event_dto())

        lines = (tmp_path / f"{run_id}.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        kinds = [json.loads(line)["kind"] for line in lines]
        assert kinds == ["step", "policy_call", "webhook_event"]

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
    async def test_file_mode_0600(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        await store.record_step(run_id, _step_dto())
        path = tmp_path / f"{run_id}.jsonl"
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_50_concurrent_writes_same_run(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()

        await asyncio.gather(
            *[store.record_step(run_id, _step_dto(i + 1)) for i in range(50)]
        )

        path = tmp_path / f"{run_id}.jsonl"
        lines = path.read_text().splitlines()
        assert len(lines) == 50
        for line in lines:
            record = json.loads(line)  # each line is valid JSON — no interleave
            assert record["kind"] == "step"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_independent_runs_do_not_block(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()

        await asyncio.gather(
            *[store.record_step(run_a, _step_dto(i + 1)) for i in range(20)],
            *[store.record_step(run_b, _step_dto(i + 1)) for i in range(20)],
        )

        assert len((tmp_path / f"{run_a}.jsonl").read_text().splitlines()) == 20
        assert len((tmp_path / f"{run_b}.jsonl").read_text().splitlines()) == 20


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class TestReplay:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_replays_in_order_as_typed_dtos(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        step = _step_dto(1)
        call = _policy_call_dto()
        event = _webhook_event_dto()

        await store.record_step(run_id, step)
        await store.record_policy_call(run_id, call)
        await store.record_webhook_event(run_id, event)

        stream = await store.open_run_stream(run_id)
        items = [item async for item in stream]

        assert len(items) == 3
        assert isinstance(items[0], StepDto)
        assert isinstance(items[1], PolicyCallDto)
        assert isinstance(items[2], WebhookEventDto)
        assert items[0].step_number == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_replay_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        stream = await store.open_run_stream(uuid.uuid4())
        items = [item async for item in stream]
        assert items == []


# ---------------------------------------------------------------------------
# Tail reader (T-078 / T-082)
# ---------------------------------------------------------------------------


class TestTailRunStream:
    @pytest.fixture(autouse=True)
    def _fast_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.modules.ai.trace_jsonl._TAIL_POLL_SECONDS", 0.01
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_follow_yields_every_committed_line(
        self, tmp_path: Path
    ) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        await store.record_step(run_id, _step_dto(1))
        await store.record_policy_call(run_id, _policy_call_dto())
        await store.record_webhook_event(run_id, _webhook_event_dto())

        items = [item async for item in store.tail_run_stream(run_id)]
        assert len(items) == 3
        assert isinstance(items[0], StepDto)
        assert isinstance(items[1], PolicyCallDto)
        assert isinstance(items[2], WebhookEventDto)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_follow_missing_file_yields_empty(
        self, tmp_path: Path
    ) -> None:
        store = JsonlTraceStore(tmp_path)
        items = [
            item async for item in store.tail_run_stream(uuid.uuid4())
        ]
        assert items == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_follow_streams_new_lines_as_writer_appends(
        self, tmp_path: Path
    ) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        target_count = 5
        collected: list[StepDto | PolicyCallDto | WebhookEventDto] = []

        async def _reader() -> None:
            async for item in store.tail_run_stream(run_id, follow=True):
                collected.append(item)
                if len(collected) >= target_count:
                    return

        async def _writer() -> None:
            for i in range(target_count):
                await asyncio.sleep(0.01)
                await store.record_step(run_id, _step_dto(i + 1))

        await asyncio.wait_for(
            asyncio.gather(_reader(), _writer()), timeout=2.0
        )
        assert len(collected) == target_count
        step_numbers = [
            item.step_number for item in collected if isinstance(item, StepDto)
        ]
        assert step_numbers == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_follow_waits_for_filename(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        collected: list[StepDto | PolicyCallDto | WebhookEventDto] = []

        async def _reader() -> None:
            async for item in store.tail_run_stream(run_id, follow=True):
                collected.append(item)
                return  # exit after first item

        async def _writer() -> None:
            await asyncio.sleep(0.02)
            await store.record_step(run_id, _step_dto(1))

        await asyncio.wait_for(
            asyncio.gather(_reader(), _writer()), timeout=2.0
        )
        assert len(collected) == 1
        assert isinstance(collected[0], StepDto)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_kinds_filter_narrows_stream(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        await store.record_step(run_id, _step_dto(1))
        await store.record_policy_call(run_id, _policy_call_dto())
        await store.record_webhook_event(run_id, _webhook_event_dto())

        items = [
            item
            async for item in store.tail_run_stream(
                run_id, kinds=frozenset({"step"})
            )
        ]
        assert len(items) == 1
        assert isinstance(items[0], StepDto)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_since_filter_excludes_earlier_records(
        self, tmp_path: Path
    ) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()

        # Write 3 policy calls with deliberately staggered created_at.
        t0 = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 4, 17, 12, 1, 0, tzinfo=UTC)
        t2 = datetime(2026, 4, 17, 12, 2, 0, tzinfo=UTC)
        for ts in (t0, t1, t2):
            dto = PolicyCallDto(
                id=uuid.uuid4(),
                step_id=uuid.uuid4(),
                provider="stub",
                model="stub-v1",
                selected_tool="analyze",
                tool_arguments={},
                available_tools=[],
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                created_at=ts,
            )
            await store.record_policy_call(run_id, dto)

        items = [
            item
            async for item in store.tail_run_stream(run_id, since=t1)
        ]
        assert len(items) == 2  # t1 and t2; t0 excluded

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_readers_see_same_lines(
        self, tmp_path: Path
    ) -> None:
        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        # Seed 3 lines before readers start.
        for i in range(3):
            await store.record_step(run_id, _step_dto(i + 1))

        target_count = 5  # 3 seeded + 2 to be written during follow
        collected_a: list[StepDto | PolicyCallDto | WebhookEventDto] = []
        collected_b: list[StepDto | PolicyCallDto | WebhookEventDto] = []

        async def _reader(sink: list[StepDto | PolicyCallDto | WebhookEventDto]) -> None:
            async for item in store.tail_run_stream(run_id, follow=True):
                sink.append(item)
                if len(sink) >= target_count:
                    return

        async def _writer() -> None:
            for i in range(3, 5):
                await asyncio.sleep(0.02)
                await store.record_step(run_id, _step_dto(i + 1))

        await asyncio.wait_for(
            asyncio.gather(_reader(collected_a), _reader(collected_b), _writer()),
            timeout=2.0,
        )
        assert len(collected_a) == target_count
        assert len(collected_b) == target_count
        nums_a = [s.step_number for s in collected_a if isinstance(s, StepDto)]
        nums_b = [s.step_number for s in collected_b if isinstance(s, StepDto)]
        assert nums_a == [1, 2, 3, 4, 5]
        assert nums_b == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_malformed_line_logged_and_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.modules.ai.trace_jsonl as module

        calls: list[str] = []

        def _spy_warning(msg: str, *args: object, **_kwargs: object) -> None:
            try:
                calls.append(msg % args if args else msg)
            except TypeError:
                calls.append(msg)

        monkeypatch.setattr(module.logger, "warning", _spy_warning)

        run_id = uuid.uuid4()
        path = tmp_path / f"{run_id}.jsonl"
        # Write valid / garbage / valid by hand, bypassing the store.
        good1 = json.dumps(
            {
                "kind": "step",
                "data": _step_dto(1).model_dump(mode="json", by_alias=True),
            }
        )
        good2 = json.dumps(
            {
                "kind": "step",
                "data": _step_dto(2).model_dump(mode="json", by_alias=True),
            }
        )
        path.write_text(good1 + "\n" + "{oops not json" + "\n" + good2 + "\n")

        store = JsonlTraceStore(tmp_path)
        items = [item async for item in store.tail_run_stream(run_id)]
        assert len(items) == 2
        assert any("malformed" in call for call in calls)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Concurrency edges (T-052)
# ---------------------------------------------------------------------------


class TestConcurrencyEdges:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_independent_runs_run_in_parallel(self, tmp_path: Path) -> None:
        """Two runs writing 50 lines each should finish faster than a strict
        serialization would allow — proving the per-run locks don't share."""
        store = JsonlTraceStore(tmp_path)
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()

        import time as _time

        async def _burst(run_id: uuid.UUID) -> None:
            for _ in range(50):
                await store.record_step(run_id, _step_dto())

        solo_start = _time.monotonic()
        await _burst(run_a)
        solo_elapsed = _time.monotonic() - solo_start

        parallel_start = _time.monotonic()
        await asyncio.gather(_burst(run_a), _burst(run_b))
        parallel_elapsed = _time.monotonic() - parallel_start

        # Generous bound: parallel should not be more than 2x the solo time —
        # if the two runs shared a lock we'd see roughly 3x (solo + another solo
        # stacked behind it + overhead).
        assert parallel_elapsed < 2 * solo_elapsed + 0.5

    @pytest.mark.asyncio(loop_scope="function")
    async def test_chmod_failure_does_not_block_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``os.chmod`` failure during the newly-created branch is logged
        but must not prevent subsequent writes."""

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("chmod denied")

        monkeypatch.setattr("app.modules.ai.trace_jsonl.os.chmod", _raise)

        store = JsonlTraceStore(tmp_path)
        run_id = uuid.uuid4()
        # First write triggers chmod (fails); second does not.
        await store.record_step(run_id, _step_dto(1))
        await store.record_step(run_id, _step_dto(2))

        lines = (tmp_path / f"{run_id}.jsonl").read_text().splitlines()
        assert len(lines) == 2


class TestFactoryDispatch:
    def test_default_factory_returns_jsonl_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_BACKEND", "jsonl")
        monkeypatch.setenv("TRACE_DIR", str(tmp_path))
        from app.config import get_settings

        get_settings.cache_clear()
        _reset_trace_store_cache()
        store = get_trace_store()
        assert isinstance(store, JsonlTraceStore)
        _reset_trace_store_cache()
        get_settings.cache_clear()

    def test_noop_backend_returns_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.ai.trace import NoopTraceStore

        monkeypatch.setenv("TRACE_BACKEND", "noop")
        from app.config import get_settings

        get_settings.cache_clear()
        _reset_trace_store_cache()
        store = get_trace_store()
        assert isinstance(store, NoopTraceStore)
        _reset_trace_store_cache()
        get_settings.cache_clear()
