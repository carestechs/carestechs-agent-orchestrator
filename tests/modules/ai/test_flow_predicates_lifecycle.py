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

    def test_no_last_raises(self) -> None:
        predicate = flow_predicates.get("review_passed")
        with pytest.raises(ValueError, match="no dispatch result available"):
            predicate({}, None)

    def test_ignores_memory(self) -> None:
        """Predicate is a pure function of the dispatch result; memory is irrelevant."""
        predicate = flow_predicates.get("review_passed")
        memory_one: dict[str, Any] = {"correction_attempts": {"T-1": 5}}
        memory_two: dict[str, Any] = {}
        envelope: dict[str, Any] = {"verdict": "pass"}
        assert predicate(memory_one, envelope) == predicate(memory_two, envelope)


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
