"""Tests for the stop-condition rules + priority ``evaluate`` (T-033)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.llm import ToolCall, Usage
from app.modules.ai.enums import StopReason
from app.modules.ai.stop_conditions import (
    RuntimeState,
    evaluate,
    is_budget_exceeded,
    is_cancelled,
    is_done_node,
    is_error,
    is_policy_terminated,
)
from app.modules.ai.tools import TERMINATE_TOOL_NAME

_TERMINAL_NODES = frozenset({"review_plan"})


def _tool(name: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        name=name,
        arguments=arguments or {},
        usage=Usage(input_tokens=0, output_tokens=0, latency_ms=0),
        raw_response=None,
    )


def _state(**overrides: Any) -> RuntimeState:
    defaults: dict[str, Any] = {
        "last_tool": None,
        "step_count": 0,
        "token_count": 0,
        "max_steps": None,
        "max_tokens": None,
        "last_policy_error": None,
        "last_engine_error": None,
        "cancel_requested": False,
        "terminal_nodes": _TERMINAL_NODES,
    }
    defaults.update(overrides)
    return RuntimeState(**defaults)


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


class TestCancelled:
    def test_matches_when_flag_set(self) -> None:
        assert is_cancelled(_state(cancel_requested=True)) is StopReason.CANCELLED

    def test_no_match_by_default(self) -> None:
        assert is_cancelled(_state()) is None


class TestError:
    def test_matches_on_policy_error(self) -> None:
        assert is_error(_state(last_policy_error=RuntimeError("x"))) is StopReason.ERROR

    def test_matches_on_engine_error(self) -> None:
        assert is_error(_state(last_engine_error=RuntimeError("x"))) is StopReason.ERROR

    def test_no_match_by_default(self) -> None:
        assert is_error(_state()) is None


class TestBudget:
    @pytest.mark.parametrize(
        ("step_count", "max_steps", "expected"),
        [
            (0, 10, None),
            (9, 10, None),
            (10, 10, StopReason.BUDGET_EXCEEDED),
            (11, 10, StopReason.BUDGET_EXCEEDED),
            (5, None, None),
        ],
    )
    def test_step_boundary(
        self, step_count: int, max_steps: int | None, expected: StopReason | None
    ) -> None:
        assert is_budget_exceeded(_state(step_count=step_count, max_steps=max_steps)) is expected

    @pytest.mark.parametrize(
        ("token_count", "max_tokens", "expected"),
        [
            (0, 100, None),
            (99, 100, None),
            (100, 100, StopReason.BUDGET_EXCEEDED),
            (250, 100, StopReason.BUDGET_EXCEEDED),
            (500, None, None),
        ],
    )
    def test_token_boundary(
        self, token_count: int, max_tokens: int | None, expected: StopReason | None
    ) -> None:
        assert is_budget_exceeded(_state(token_count=token_count, max_tokens=max_tokens)) is expected


class TestPolicyTerminated:
    def test_matches_terminate_tool(self) -> None:
        assert (
            is_policy_terminated(_state(last_tool=_tool(TERMINATE_TOOL_NAME)))
            is StopReason.POLICY_TERMINATED
        )

    def test_no_match_on_regular_tool(self) -> None:
        assert is_policy_terminated(_state(last_tool=_tool("analyze_brief"))) is None

    def test_no_match_when_no_tool_yet(self) -> None:
        assert is_policy_terminated(_state()) is None


class TestDoneNode:
    def test_matches_terminal_node(self) -> None:
        assert is_done_node(_state(last_tool=_tool("review_plan"))) is StopReason.DONE_NODE

    def test_no_match_on_non_terminal(self) -> None:
        assert is_done_node(_state(last_tool=_tool("analyze_brief"))) is None

    def test_no_match_when_no_tool(self) -> None:
        assert is_done_node(_state()) is None


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriority:
    def test_cancelled_beats_error(self) -> None:
        assert evaluate(
            _state(cancel_requested=True, last_engine_error=RuntimeError("x"))
        ) is StopReason.CANCELLED

    def test_cancelled_beats_budget(self) -> None:
        assert evaluate(
            _state(cancel_requested=True, step_count=10, max_steps=10)
        ) is StopReason.CANCELLED

    def test_error_beats_budget(self) -> None:
        assert evaluate(
            _state(last_engine_error=RuntimeError("x"), step_count=10, max_steps=10)
        ) is StopReason.ERROR

    def test_budget_beats_policy_terminated(self) -> None:
        assert evaluate(
            _state(
                last_tool=_tool(TERMINATE_TOOL_NAME),
                step_count=10,
                max_steps=10,
            )
        ) is StopReason.BUDGET_EXCEEDED

    def test_policy_terminated_beats_done_node(self) -> None:
        # Contrived: terminate tool + a state-hash match on terminal nodes.
        # terminate is not in terminal_nodes, so only is_policy_terminated matches — confirms rule order.
        assert evaluate(_state(last_tool=_tool(TERMINATE_TOOL_NAME))) is StopReason.POLICY_TERMINATED

    def test_no_match_returns_none(self) -> None:
        assert evaluate(_state()) is None

    def test_only_done_node(self) -> None:
        assert evaluate(_state(last_tool=_tool("review_plan"))) is StopReason.DONE_NODE


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


class TestStateImmutability:
    def test_runtime_state_is_hashable(self) -> None:
        # frozenset terminal_nodes + frozen dataclass → hashable.
        # Note: a ToolCall contains a dict in `arguments` which makes it unhashable,
        # but the stop-condition rules only inspect `last_tool.name`.
        # So hashing the whole state requires last_tool=None.
        hash(_state())  # should not raise


# ---------------------------------------------------------------------------
# Priority matrix (T-049)
# ---------------------------------------------------------------------------


class TestPriorityMatrix:
    @pytest.mark.parametrize(
        ("state_kwargs", "expected"),
        [
            (
                {"cancel_requested": True, "last_engine_error": RuntimeError("x")},
                StopReason.CANCELLED,
            ),
            (
                {"cancel_requested": True, "max_steps": 1, "step_count": 1},
                StopReason.CANCELLED,
            ),
            (
                {"cancel_requested": True, "last_tool": _tool(TERMINATE_TOOL_NAME)},
                StopReason.CANCELLED,
            ),
            (
                {
                    "last_engine_error": RuntimeError("x"),
                    "max_steps": 1,
                    "step_count": 1,
                },
                StopReason.ERROR,
            ),
            (
                {
                    "last_policy_error": RuntimeError("x"),
                    "last_tool": _tool(TERMINATE_TOOL_NAME),
                },
                StopReason.ERROR,
            ),
            (
                {
                    "max_steps": 1,
                    "step_count": 1,
                    "last_tool": _tool(TERMINATE_TOOL_NAME),
                },
                StopReason.BUDGET_EXCEEDED,
            ),
            (
                {
                    "max_tokens": 10,
                    "token_count": 20,
                    "last_tool": _tool("review_plan"),
                },
                StopReason.BUDGET_EXCEEDED,
            ),
            (
                {"last_tool": _tool(TERMINATE_TOOL_NAME)},
                StopReason.POLICY_TERMINATED,
            ),
            (
                {"last_tool": _tool("review_plan")},
                StopReason.DONE_NODE,
            ),
            ({}, None),
        ],
        ids=[
            "cancel-beats-error",
            "cancel-beats-budget",
            "cancel-beats-terminate",
            "error-beats-budget",
            "error-beats-terminate",
            "budget-beats-terminate",
            "tokens-beat-done-node",
            "terminate-alone",
            "done-node-alone",
            "no-match",
        ],
    )
    def test_priority(
        self, state_kwargs: dict[str, Any], expected: StopReason | None
    ) -> None:
        assert evaluate(_state(**state_kwargs)) is expected


class TestBoundary:
    def test_steps_off_by_one_below(self) -> None:
        assert evaluate(_state(step_count=9, max_steps=10)) is None

    def test_steps_at_boundary(self) -> None:
        assert evaluate(_state(step_count=10, max_steps=10)) is StopReason.BUDGET_EXCEEDED

    def test_tokens_off_by_one_below(self) -> None:
        assert evaluate(_state(token_count=99, max_tokens=100)) is None

    def test_tokens_at_boundary(self) -> None:
        assert (
            evaluate(_state(token_count=100, max_tokens=100)) is StopReason.BUDGET_EXCEEDED
        )


# ---------------------------------------------------------------------------
# Correction bound (FEAT-005 / T-097)
# ---------------------------------------------------------------------------


class TestCorrectionBound:
    def test_under_bound(self) -> None:
        state = _state(
            correction_attempts={"T-001": 2}, max_corrections=2,
        )
        from app.modules.ai.stop_conditions import (
            correction_budget_exceeded,
            find_correction_exceedance,
        )
        assert correction_budget_exceeded(state) is None
        assert find_correction_exceedance(state) is None

    def test_over_bound(self) -> None:
        state = _state(
            correction_attempts={"T-001": 3}, max_corrections=2,
        )
        from app.modules.ai.stop_conditions import (
            correction_budget_exceeded,
            find_correction_exceedance,
        )
        assert correction_budget_exceeded(state) is StopReason.ERROR
        assert find_correction_exceedance(state) == ("T-001", 3)

    def test_missing_config_is_noop(self) -> None:
        """Agents without ``max_corrections`` set never trip this rule."""
        state = _state(correction_attempts={"T-001": 99}, max_corrections=None)
        assert evaluate(state) is None

    def test_cancelled_beats_correction_bound(self) -> None:
        """Priority order: cancel wins over correction bound (both are ERROR class)."""
        state = _state(
            correction_attempts={"T-001": 99},
            max_corrections=2,
            cancel_requested=True,
        )
        assert evaluate(state) is StopReason.CANCELLED

    def test_error_beats_correction_bound(self) -> None:
        """last_policy_error fires the generic ERROR before the correction variant."""
        state = _state(
            correction_attempts={"T-001": 99},
            max_corrections=2,
            last_policy_error=RuntimeError("boom"),
        )
        # Both return ERROR, but the generic is_error wins by priority order.
        assert evaluate(state) is StopReason.ERROR

    def test_correction_bound_beats_budget(self) -> None:
        state = _state(
            correction_attempts={"T-001": 99},
            max_corrections=2,
            step_count=100,
            max_steps=50,
        )
        assert evaluate(state) is StopReason.ERROR  # correction trips ERROR before BUDGET_EXCEEDED
