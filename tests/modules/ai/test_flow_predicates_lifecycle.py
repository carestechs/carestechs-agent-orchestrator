"""Unit tests for the FEAT-011 / T-251 lifecycle branch predicates.

The two new predicates — ``review_passed`` and ``task_rejected`` — are pure
functions over ``(memory, last_dispatch_result)``. Each has three paths:
True (matches the positive literal), False (matches the negative literal),
and raise (anything else, including a missing field).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.ai import flow_predicates

# ---------------------------------------------------------------------------
# review_passed
# ---------------------------------------------------------------------------


class TestReviewPassedPredicate:
    def test_registered(self) -> None:
        assert "review_passed" in flow_predicates.known()

    def test_pass_returns_true(self) -> None:
        predicate = flow_predicates.get("review_passed")
        result_envelope: dict[str, Any] = {"verdict": "pass", "task_id": "T-1"}
        assert predicate({}, result_envelope) is True

    def test_fail_returns_false(self) -> None:
        predicate = flow_predicates.get("review_passed")
        result_envelope: dict[str, Any] = {"verdict": "fail", "task_id": "T-1"}
        assert predicate({}, result_envelope) is False

    def test_unexpected_verdict_raises(self) -> None:
        predicate = flow_predicates.get("review_passed")
        with pytest.raises(ValueError, match="must be 'pass' or 'fail'"):
            predicate({}, {"verdict": "maybe"})

    def test_missing_verdict_raises(self) -> None:
        predicate = flow_predicates.get("review_passed")
        with pytest.raises(ValueError, match="must be 'pass' or 'fail'"):
            predicate({}, {"task_id": "T-1"})

    def test_no_last_and_no_memory_history_raises(self) -> None:
        predicate = flow_predicates.get("review_passed")
        with pytest.raises(ValueError, match="must be 'pass' or 'fail'"):
            predicate({}, None)

    def test_falls_back_to_memory_review_history(self) -> None:
        """When ``last`` lacks ``verdict``, predicate reads ``memory.review_history[-1].verdict``.

        FEAT-011 composite executors persist the LLM verdict to memory
        before the engine wake leg, but the wake-delivered envelope's
        ``result`` carries engine metadata only.  The fallback lets the
        predicate stay accurate after the engine round-trip.
        """
        predicate = flow_predicates.get("review_passed")
        memory: dict[str, Any] = {
            "review_history": [
                {"task_id": "T-1", "verdict": "fail", "feedback": "older"},
                {"task_id": "T-1", "verdict": "pass", "feedback": "later"},
            ]
        }
        # ``last`` is the wake-delivered envelope: only engine metadata.
        last: dict[str, Any] = {"correlation_id": "x", "transition_key": "task.T10"}
        assert predicate(memory, last) is True

    def test_last_verdict_takes_precedence_over_memory(self) -> None:
        predicate = flow_predicates.get("review_passed")
        memory: dict[str, Any] = {"review_history": [{"verdict": "fail"}]}
        last: dict[str, Any] = {"verdict": "pass"}
        assert predicate(memory, last) is True


# ---------------------------------------------------------------------------
# task_rejected
# ---------------------------------------------------------------------------


class TestTaskRejectedPredicate:
    def test_registered(self) -> None:
        assert "task_rejected" in flow_predicates.known()

    def test_rejected_returns_true(self) -> None:
        predicate = flow_predicates.get("task_rejected")
        envelope: dict[str, Any] = {"outcome": "rejected", "task_id": "T-1"}
        assert predicate({}, envelope) is True

    def test_approved_returns_false(self) -> None:
        predicate = flow_predicates.get("task_rejected")
        envelope: dict[str, Any] = {"outcome": "approved", "task_id": "T-1"}
        assert predicate({}, envelope) is False

    def test_unexpected_outcome_raises(self) -> None:
        predicate = flow_predicates.get("task_rejected")
        with pytest.raises(ValueError, match="must be 'approved' or 'rejected'"):
            predicate({}, {"outcome": "deferred"})

    def test_missing_outcome_raises(self) -> None:
        predicate = flow_predicates.get("task_rejected")
        with pytest.raises(ValueError, match="must be 'approved' or 'rejected'"):
            predicate({}, {"task_id": "T-1"})

    def test_no_last_raises(self) -> None:
        predicate = flow_predicates.get("task_rejected")
        with pytest.raises(ValueError, match="no dispatch result available"):
            predicate({}, None)


# ---------------------------------------------------------------------------
# unplanned_tasks_remaining (BUG-009 regression)
# ---------------------------------------------------------------------------


class TestUnplannedTasksRemaining:
    """Reads canonical ``lifecycle.v1.tasks`` against top-level ``plans``.

    Pre-fix, this predicate read top-level ``memory["tasks"]`` — a
    sidecar that was only ever populated for *already-planned* tasks,
    making the predicate always-False on multi-task work items.
    """

    @staticmethod
    def _memory_with(*, task_ids: tuple[str, ...], planned: tuple[str, ...]) -> dict[str, Any]:
        from app.modules.ai.tools.lifecycle.memory import (
            LIFECYCLE_MEMORY_NS,
            LifecycleMemory,
            LifecycleTask,
            to_run_memory,
        )

        memory_model = LifecycleMemory(
            tasks=[LifecycleTask(id=tid, title=f"Task {tid}") for tid in task_ids],
        )
        return {
            LIFECYCLE_MEMORY_NS: to_run_memory(memory_model),
            "plans": {tid: {"plan_markdown": "# x"} for tid in planned},
        }

    def test_registered(self) -> None:
        assert "unplanned_tasks_remaining" in flow_predicates.known()

    def test_no_tasks_returns_false(self) -> None:
        predicate = flow_predicates.get("unplanned_tasks_remaining")
        memory = self._memory_with(task_ids=(), planned=())
        assert predicate(memory, None) is False

    def test_all_planned_returns_false(self) -> None:
        predicate = flow_predicates.get("unplanned_tasks_remaining")
        memory = self._memory_with(task_ids=("T-1", "T-2"), planned=("T-1", "T-2"))
        assert predicate(memory, None) is False

    def test_some_unplanned_returns_true(self) -> None:
        """The bug case — multi-task item with one planned, one not."""
        predicate = flow_predicates.get("unplanned_tasks_remaining")
        memory = self._memory_with(task_ids=("T-1", "T-2"), planned=("T-1",))
        assert predicate(memory, None) is True

    def test_no_plans_at_all_returns_true(self) -> None:
        predicate = flow_predicates.get("unplanned_tasks_remaining")
        memory = self._memory_with(task_ids=("T-1",), planned=())
        assert predicate(memory, None) is True


class TestTasksRemainingPredicate:
    """BUG-013: True iff at least one task is not yet in ``completed_task_ids``.

    Powers the loop-back from ``approve_review`` so multi-task work items
    walk every task through the per-task pipeline before ``close_work_item``.
    """

    @staticmethod
    def _memory_with(*, task_ids: tuple[str, ...], completed: tuple[str, ...]) -> dict[str, Any]:
        from app.modules.ai.tools.lifecycle.memory import (
            LIFECYCLE_MEMORY_NS,
            LifecycleMemory,
            LifecycleTask,
            to_run_memory,
        )

        memory_model = LifecycleMemory(
            tasks=[LifecycleTask(id=tid, title=f"Task {tid}") for tid in task_ids],
            completed_task_ids=list(completed),
        )
        return {LIFECYCLE_MEMORY_NS: to_run_memory(memory_model)}

    def test_registered(self) -> None:
        assert "tasks_remaining" in flow_predicates.known()

    def test_no_tasks_returns_false(self) -> None:
        predicate = flow_predicates.get("tasks_remaining")
        memory = self._memory_with(task_ids=(), completed=())
        assert predicate(memory, None) is False

    def test_all_completed_returns_false(self) -> None:
        predicate = flow_predicates.get("tasks_remaining")
        memory = self._memory_with(task_ids=("T-1", "T-2"), completed=("T-1", "T-2"))
        assert predicate(memory, None) is False

    def test_some_remaining_returns_true(self) -> None:
        """The loop case — one approved, one still to do."""
        predicate = flow_predicates.get("tasks_remaining")
        memory = self._memory_with(task_ids=("T-1", "T-2"), completed=("T-1",))
        assert predicate(memory, None) is True

    def test_none_completed_returns_true(self) -> None:
        predicate = flow_predicates.get("tasks_remaining")
        memory = self._memory_with(task_ids=("T-1",), completed=())
        assert predicate(memory, None) is True


# ---------------------------------------------------------------------------
# Resolver integration — confirm both predicates are reachable through the
# resolver's ``branch.rule`` lookup path. (Does not modify ``flow_resolver``;
# this is an integration check that the registry extension is wired.)
# ---------------------------------------------------------------------------


class TestResolverIntegration:
    @staticmethod
    def _decl(rule: str) -> dict[str, Any]:
        return {
            "terminalNodes": ["done"],
            "flow": {
                "transitions": {
                    "judge": {
                        "branch": {
                            "rule": rule,
                            "true": "done",
                            "false": "retry",
                        }
                    },
                    "retry": [],
                    "done": [],
                }
            },
        }

    def test_review_passed_resolves_through_branch(self) -> None:
        from app.modules.ai.flow_resolver import NextNode, resolve_next

        decl = self._decl("review_passed")
        result = resolve_next(decl, "judge", memory={}, last_dispatch_result={"verdict": "pass"})
        assert result == NextNode(name="done")
        result = resolve_next(decl, "judge", memory={}, last_dispatch_result={"verdict": "fail"})
        assert result == NextNode(name="retry")

    def test_task_rejected_resolves_through_branch(self) -> None:
        from app.modules.ai.flow_resolver import NextNode, resolve_next

        decl = self._decl("task_rejected")
        result = resolve_next(decl, "judge", memory={}, last_dispatch_result={"outcome": "rejected"})
        assert result == NextNode(name="done")
        result = resolve_next(decl, "judge", memory={}, last_dispatch_result={"outcome": "approved"})
        assert result == NextNode(name="retry")
