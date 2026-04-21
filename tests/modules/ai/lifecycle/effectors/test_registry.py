"""Unit tests for the effector registry + transition-key scheme."""

from __future__ import annotations

from typing import cast

import pytest

from app.modules.ai.lifecycle.effectors import (
    EffectorContext,
    EffectorRegistry,
    EffectorResult,
    build_transition_key,
    no_effector,
)
from app.modules.ai.lifecycle.effectors.base import (
    _reset_exemptions_for_tests,
    iter_no_effector_exemptions,
)

# ---------------------------------------------------------------------------
# build_transition_key
# ---------------------------------------------------------------------------


def test_build_transition_key_entry_shape() -> None:
    assert (
        build_transition_key("work_item", None, "pending_tasks")
        == "work_item:entry:pending_tasks"
    )


def test_build_transition_key_transition_shape() -> None:
    assert (
        build_transition_key("task", "implementing", "impl_review")
        == "task:implementing->impl_review"
    )


# ---------------------------------------------------------------------------
# Registry dispatch
# ---------------------------------------------------------------------------


class _RecordingEffector:
    def __init__(self, name: str, status: str = "ok") -> None:
        self.name = name
        self._status = status
        self.fire_calls: list[EffectorContext] = []

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        self.fire_calls.append(ctx)
        return EffectorResult(
            effector_name=self.name,
            status=cast("str", self._status),  # type: ignore[arg-type]
            duration_ms=1,
        )


class _RaisingEffector:
    name = "raiser"

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_register_and_dispatch_in_insertion_order(
    trace_store, make_context
) -> None:
    registry = EffectorRegistry(trace=trace_store)
    a, b = _RecordingEffector("a"), _RecordingEffector("b")
    key = build_transition_key("task", "implementing", "impl_review")
    registry.register(key, a)
    registry.register(key, b)

    ctx = make_context()
    results = await registry.fire_all(ctx)

    assert [r.effector_name for r in results] == ["a", "b"]
    assert len(a.fire_calls) == 1
    assert len(b.fire_calls) == 1


@pytest.mark.asyncio
async def test_empty_dispatch_returns_no_results(
    trace_store, make_context
) -> None:
    registry = EffectorRegistry(trace=trace_store)
    results = await registry.fire_all(make_context(to_state="nowhere"))

    assert results == []
    assert trace_store.effector_calls == []


@pytest.mark.asyncio
async def test_failure_does_not_halt_pipeline(
    trace_store, make_context
) -> None:
    registry = EffectorRegistry(trace=trace_store)
    second = _RecordingEffector("after-raise")
    key = build_transition_key("task", "implementing", "impl_review")
    registry.register(key, _RaisingEffector())
    registry.register(key, second)

    results = await registry.fire_all(make_context())

    assert [r.status for r in results] == ["error", "ok"]
    assert results[0].error_code == "effector-exception"
    assert "RuntimeError: boom" in (results[0].detail or "")
    assert len(second.fire_calls) == 1


@pytest.mark.asyncio
async def test_skipped_status_propagates(trace_store, make_context) -> None:
    registry = EffectorRegistry(trace=trace_store)
    key = build_transition_key("task", "implementing", "impl_review")
    registry.register(key, _RecordingEffector("skipper", status="skipped"))

    results = await registry.fire_all(make_context())

    assert results[0].status == "skipped"
    assert trace_store.effector_calls[0][1].status == "skipped"


@pytest.mark.asyncio
async def test_trace_emission_is_per_result(
    trace_store, make_context
) -> None:
    registry = EffectorRegistry(trace=trace_store)
    key = build_transition_key("task", "implementing", "impl_review")
    registry.register(key, _RecordingEffector("a"))
    registry.register(key, _RecordingEffector("b"))
    registry.register(key, _RecordingEffector("c"))

    ctx = make_context()
    await registry.fire_all(ctx)

    names = [dto.effector_name for _, dto in trace_store.effector_calls]
    assert names == ["a", "b", "c"]
    # Keyed on entity_id, not correlation id.
    assert all(eid == ctx.entity_id for eid, _ in trace_store.effector_calls)


@pytest.mark.asyncio
async def test_registered_keys_reflects_registrations(
    trace_store,
) -> None:
    registry = EffectorRegistry(trace=trace_store)
    registry.register("task:entry:assigning", _RecordingEffector("req"))
    registry.register("work_item:entry:pending_tasks", _RecordingEffector("gen"))

    assert registry.registered_keys() == frozenset(
        {"task:entry:assigning", "work_item:entry:pending_tasks"}
    )


# ---------------------------------------------------------------------------
# no_effector exemption marker
# ---------------------------------------------------------------------------


def test_no_effector_stores_reason() -> None:
    _reset_exemptions_for_tests()
    no_effector("task:proposed->rejected", "rejections only record Approval row")

    exemptions = dict(iter_no_effector_exemptions())
    assert exemptions["task:proposed->rejected"] == (
        "rejections only record Approval row"
    )


def test_no_effector_rejects_short_reason() -> None:
    _reset_exemptions_for_tests()
    with pytest.raises(ValueError, match=r"≥10 chars"):
        no_effector("task:x->y", "n/a")
