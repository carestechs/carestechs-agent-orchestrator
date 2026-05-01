"""Lifecycle-memory namespaced helpers (FEAT-011 / T-255).

The helpers persist :class:`LifecycleMemory` under a stable
``lifecycle.v1`` namespace inside :attr:`RunMemory.data` while remaining
backward-compatible with v0.1.0 runs that stored the shape at the top
level.
"""

from __future__ import annotations

from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleTask,
    WorkItemRef,
    read_lifecycle_memory,
    to_run_memory,
    write_lifecycle_memory,
)


def _sample_memory() -> LifecycleMemory:
    return LifecycleMemory(
        work_item=WorkItemRef(id="FEAT-099", type="FEAT", title="x", path="docs/work-items/FEAT-099.md"),
        tasks=[LifecycleTask(id="T-1", title="t1", executor="claude-code")],
        current_task_id="T-1",
        correction_attempts={"T-1": 1},
    )


class TestRoundTrip:
    def test_write_then_read_returns_equal_model(self) -> None:
        original = _sample_memory()
        patch = write_lifecycle_memory(original)
        # The patch is shaped as ``{ns: serialised_model}``.
        assert set(patch.keys()) == {LIFECYCLE_MEMORY_NS}
        # Simulate the runtime merging the patch into RunMemory.data shallowly.
        run_memory_data: dict[str, object] = {"__feat009": {"current_node": "x"}}
        run_memory_data.update(patch)
        round_tripped = read_lifecycle_memory(run_memory_data)
        assert round_tripped == original
        # And the bookkeeping namespace was not touched.
        assert run_memory_data.get("__feat009") == {"current_node": "x"}


class TestMissingNamespace:
    def test_empty_input_returns_empty_model(self) -> None:
        assert read_lifecycle_memory(None) == LifecycleMemory.empty()
        assert read_lifecycle_memory({}) == LifecycleMemory.empty()

    def test_input_without_namespace_and_no_v01_shape_returns_empty(self) -> None:
        # Top-level data with extra keys but no recognisable v0.1.0
        # shape — the fallback validation fails, helper returns empty.
        assert read_lifecycle_memory({"some_other_key": 1}) == LifecycleMemory.empty()


class TestV01BackwardCompat:
    def test_top_level_shape_hydrates_when_namespace_missing(self) -> None:
        """A v0.1.0 run wrote ``LifecycleMemory`` at the top level of RunMemory.data.

        ``read_lifecycle_memory`` must hydrate it so a resumed run can
        continue executing under v0.3.0 without a one-shot migration.
        """
        original = _sample_memory()
        v01_data = to_run_memory(original)
        result = read_lifecycle_memory(v01_data)
        assert result == original
