"""validate_executor_coverage unit tests (FEAT-009 / T-213)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.modules.ai.executors import (
    DispatchContext,
    ExecutorCoverageError,
    ExecutorRegistry,
    no_executor,
    validate_executor_coverage,
)
from app.modules.ai.executors.base import ExecutorMode
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.schemas import DispatchEnvelope


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


class _StubExecutor:
    name: ClassVar[str] = "stub"
    mode: ClassVar[ExecutorMode] = "local"

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        raise NotImplementedError  # not invoked in coverage tests


def _agent(ref: str, *node_names: str) -> dict[str, object]:
    return {"ref": ref, "nodes": [{"name": n} for n in node_names]}


class TestCoveragePass:
    def test_all_nodes_registered(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "a", _StubExecutor())
        reg.register("agent@1", "b", _StubExecutor())
        validate_executor_coverage(reg, [_agent("agent@1", "a", "b")])

    def test_node_covered_by_exemption(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "a", _StubExecutor())
        no_executor("agent@1", "b", reason="terminal node, runtime never dispatches")
        validate_executor_coverage(reg, [_agent("agent@1", "a", "b")])


class TestCoverageFail:
    def test_unbound_node_raises_with_listing(self) -> None:
        reg = ExecutorRegistry()
        reg.register("agent@1", "a", _StubExecutor())
        with pytest.raises(ExecutorCoverageError) as excinfo:
            validate_executor_coverage(reg, [_agent("agent@1", "a", "b", "c")])
        msg = str(excinfo.value)
        assert "'agent@1' :: 'b'" in msg
        assert "'agent@1' :: 'c'" in msg
        assert "2 node(s)" in msg

    def test_multiple_agents_aggregated(self) -> None:
        reg = ExecutorRegistry()
        with pytest.raises(ExecutorCoverageError) as excinfo:
            validate_executor_coverage(
                reg,
                [_agent("agent@1", "a"), _agent("agent@2", "x")],
            )
        msg = str(excinfo.value)
        assert "'agent@1' :: 'a'" in msg
        assert "'agent@2' :: 'x'" in msg

    def test_missing_ref_raises(self) -> None:
        reg = ExecutorRegistry()
        with pytest.raises(ExecutorCoverageError, match="missing 'ref'"):
            validate_executor_coverage(reg, [{"nodes": [{"name": "a"}]}])


class TestExemptionDiscipline:
    def test_short_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="≥10 chars"):
            no_executor("agent@1", "a", reason="too short")

    def test_long_reason_accepted(self) -> None:
        no_executor("agent@1", "a", reason="this reason is long enough")

    def test_repeated_exemption_overwrites(self) -> None:
        no_executor("agent@1", "a", reason="first long enough reason")
        no_executor("agent@1", "a", reason="second long enough reason")
        from app.modules.ai.executors import iter_no_executor_exemptions

        items = dict(iter_no_executor_exemptions())
        assert items[("agent@1", "a")] == "second long enough reason"
