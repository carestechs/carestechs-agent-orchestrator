"""Unit tests for the FEAT-009 / T-211 FlowResolver.

The resolver must remain a pure function with no LLM access — verified by
the import-quarantine assertion at the bottom of this module.  The
exhaustive-walk test uses a fixture flow that mirrors the *shape* of
``lifecycle-agent@0.2.0`` (1→1 chain + ``generate_plan`` self-loop +
``review_implementation`` verdict branch + correction-bound terminal
short-circuit); T-222 swaps the fixture for the real YAML.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app.modules.ai.flow_resolver import (
    FlowDeclarationError,
    NextNode,
    TerminalSentinel,
    resolve_next,
)

# v0.2.0-shaped declaration used by the exhaustive walk.  Once T-222 lands
# the real YAML, this fixture becomes ``yaml.safe_load`` of that file.
_DECLARATION: dict[str, Any] = {
    "terminalNodes": ["request_closure"],
    "flow": {
        "entryNode": "request_work_item_load",
        "transitions": {
            "request_work_item_load": ["request_task_generation"],
            "request_task_generation": ["request_assignment"],
            "request_assignment": ["request_plan"],
            "request_plan": {
                "branch": {
                    "rule": "unplanned_tasks_remaining",
                    "true": "request_plan",
                    "false": "request_implementation",
                }
            },
            "request_implementation": ["request_review"],
            "request_review": {
                "branch": {
                    "rule": "result.verdict == 'pass'",
                    "true": "request_closure",
                    "false": "request_correction",
                }
            },
            "request_correction": ["request_implementation"],
            "request_closure": [],
        },
    },
}


class TestOneToOneTransitions:
    def test_simple_chain(self) -> None:
        for current, expected in [
            ("request_work_item_load", "request_task_generation"),
            ("request_task_generation", "request_assignment"),
            ("request_assignment", "request_plan"),
            ("request_implementation", "request_review"),
            ("request_correction", "request_implementation"),
        ]:
            result = resolve_next(_DECLARATION, current, memory={}, last_dispatch_result=None)
            assert result == NextNode(name=expected)


class TestTerminalNode:
    def test_terminal_returns_done_node(self) -> None:
        result = resolve_next(_DECLARATION, "request_closure", memory={}, last_dispatch_result={})
        assert result == TerminalSentinel(reason="done_node")

    def test_empty_target_list_is_terminal(self) -> None:
        decl = {"terminalNodes": [], "flow": {"transitions": {"x": []}}}
        assert resolve_next(decl, "x", memory={}, last_dispatch_result=None) == TerminalSentinel(reason="done_node")


class TestExecutorTerminalShortCircuit:
    def test_executor_can_force_terminal(self) -> None:
        result = resolve_next(
            _DECLARATION,
            "request_correction",
            memory={},
            last_dispatch_result={"terminal": True, "terminal_reason": "correction_budget_exceeded"},
        )
        assert result == TerminalSentinel(reason="correction_budget_exceeded")

    def test_terminal_reason_defaults(self) -> None:
        result = resolve_next(
            _DECLARATION,
            "request_correction",
            memory={},
            last_dispatch_result={"terminal": True},
        )
        assert result == TerminalSentinel(reason="policy_terminated")


class TestPredicateBranch:
    def test_unplanned_tasks_remaining_true(self) -> None:
        memory = {"tasks": {"T-1": {}, "T-2": {}}, "plans": {"T-1": "..."}}
        result = resolve_next(_DECLARATION, "request_plan", memory=memory, last_dispatch_result={})
        assert result == NextNode(name="request_plan")

    def test_unplanned_tasks_remaining_false(self) -> None:
        memory = {"tasks": {"T-1": {}}, "plans": {"T-1": "..."}}
        result = resolve_next(_DECLARATION, "request_plan", memory=memory, last_dispatch_result={})
        assert result == NextNode(name="request_implementation")


class TestExpressionBranch:
    @pytest.mark.parametrize(
        ("verdict", "expected"),
        [("pass", "request_closure"), ("fail", "request_correction")],
    )
    def test_verdict_routes(self, verdict: str, expected: str) -> None:
        result = resolve_next(
            _DECLARATION,
            "request_review",
            memory={},
            last_dispatch_result={"verdict": verdict},
        )
        assert result == NextNode(name=expected)

    @pytest.mark.parametrize(
        ("rule", "result_payload", "expected_truth"),
        [
            ("result.verdict == 'pass'", {"verdict": "pass"}, True),
            ("result.verdict != 'pass'", {"verdict": "fail"}, True),
            ("result.count == 0", {"count": 0}, True),
            ("result.count == 0", {"count": 1}, False),
            ("result.flag == true", {"flag": True}, True),
            ("result.value == null", {"value": None}, True),
            ('result.name == "foo"', {"name": "foo"}, True),
        ],
    )
    def test_expression_grammar(self, rule: str, result_payload: dict[str, Any], expected_truth: bool) -> None:
        decl = {
            "terminalNodes": [],
            "flow": {"transitions": {"n": {"branch": {"rule": rule, "true": "yes", "false": "no"}}}},
        }
        result = resolve_next(decl, "n", memory={}, last_dispatch_result=result_payload)
        assert result == NextNode(name="yes" if expected_truth else "no")


class TestErrors:
    def test_unknown_node_raises(self) -> None:
        with pytest.raises(FlowDeclarationError, match="no transition entry"):
            resolve_next(_DECLARATION, "ghost_node", memory={}, last_dispatch_result=None)

    def test_multi_target_without_branch_raises(self) -> None:
        decl = {
            "terminalNodes": [],
            "flow": {"transitions": {"n": ["a", "b"]}},
        }
        with pytest.raises(FlowDeclarationError, match="no 'branch' block"):
            resolve_next(decl, "n", memory={}, last_dispatch_result=None)

    def test_branch_without_targets_raises(self) -> None:
        decl = {
            "terminalNodes": [],
            "flow": {"transitions": {"n": {"branch": {"rule": "predicate"}}}},
        }
        with pytest.raises(FlowDeclarationError, match="'true' and 'false' targets"):
            resolve_next(decl, "n", memory={}, last_dispatch_result=None)

    def test_unknown_predicate_raises(self) -> None:
        decl = {
            "terminalNodes": [],
            "flow": {"transitions": {"n": {"branch": {"rule": "no_such_predicate", "true": "a", "false": "b"}}}},
        }
        with pytest.raises(FlowDeclarationError, match="recognized expression nor a registered"):
            resolve_next(decl, "n", memory={}, last_dispatch_result=None)

    def test_expression_without_dispatch_result_raises(self) -> None:
        decl = {
            "terminalNodes": [],
            "flow": {"transitions": {"n": {"branch": {"rule": "result.verdict == 'pass'", "true": "a", "false": "b"}}}},
        }
        with pytest.raises(FlowDeclarationError, match="no dispatch result is available"):
            resolve_next(decl, "n", memory={}, last_dispatch_result=None)


def test_resolver_does_not_pull_in_anthropic() -> None:
    """The runtime-loop's node selection must not transitively import an LLM SDK.

    Run in a subprocess so the resolver's import graph is measured in isolation
    rather than against the test session's global ``sys.modules`` state — other
    tests legitimately import LLM SDKs and would fool an in-process check.
    Mirrors the import-quarantine pattern in ``tests/test_adapters_are_thin.py``
    and is extended to ``service.py`` / ``runtime_helpers.py`` in T-228.
    """
    import subprocess

    src = (
        "import sys\n"
        "from app.modules.ai import flow_resolver, flow_predicates  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m == 'anthropic' or m.startswith('anthropic.') "
        "or m == 'openai' or m.startswith('openai.')]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "resolver pulls in an LLM SDK transitively:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
