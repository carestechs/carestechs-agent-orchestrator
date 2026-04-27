"""AC-8 exhaustive branch-walk for ``lifecycle-agent@0.3.0`` (FEAT-011 / T-263).

For every transition declared in the v0.3.0 YAML, exercise the
``FlowResolver`` against a synthetic ``(memory, last_dispatch_result)``
payload designed to satisfy each branch outcome.  The test is a permanent
guarantee that:

1. Every multi-target transition declares a branch rule that resolves
   through the existing predicate registry (or the inline expression
   form) — no missing branch declarations slip past review.
2. Every branch outcome maps to exactly one declared successor.
3. Every transition target is reachable from the entry node.
4. Resolving the flow does NOT instantiate any LLM client — the
   deterministic-flow promise that node selection is a pure function is
   asserted at the file level by an ``import`` snapshot of ``sys.modules``.

This test sits alongside ``tests/test_runtime_deterministic_is_pure.py``
(FEAT-009 / T-228) — that test polices the runtime module's import
graph; this one polices a real agent's branch graph.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from app.modules.ai import flow_predicates
from app.modules.ai.agents import load_agent
from app.modules.ai.flow_resolver import (
    NextNode,
    TerminalSentinel,
    resolve_next,
)

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
_AGENT_REF = "lifecycle-agent@0.3.0"


@pytest.fixture(scope="module")
def declaration() -> Mapping[str, Any]:
    agent = load_agent(_AGENT_REF, _AGENTS_DIR)
    return {
        "ref": agent.ref,
        "flow": {
            "policy": agent.flow.policy,
            "entryNode": agent.flow.entry_node,
            "transitions": agent.flow.transitions,
        },
        "terminalNodes": sorted(agent.terminal_nodes),
        "_entry": agent.flow.entry_node,
        "_node_names": [n.name for n in agent.nodes],
    }


# ---------------------------------------------------------------------------
# Branch-payload registry
#
# Each multi-target transition gets a list of ``(label, memory, last)``
# scenarios; the test asserts the resolver picks the declared
# branch-target for each.  Adding a new multi-target transition to the
# YAML without an entry here makes the parametrize block fail loud.
# ---------------------------------------------------------------------------


_BRANCH_SCENARIOS: dict[str, list[tuple[str, dict[str, Any], dict[str, Any] | None]]] = {
    "generate_plan": [
        (
            "true",
            # one task without a plan -> still unplanned
            {"tasks": {"T-1": {}}, "plans": {}},
            None,
        ),
        (
            "false",
            # every task has a plan -> done planning
            {"tasks": {"T-1": {}}, "plans": {"T-1": {}}},
            None,
        ),
    ],
    "review_implementation": [
        ("true", {}, {"verdict": "pass"}),
        ("false", {}, {"verdict": "fail"}),
    ],
    "correct_implementation": [
        (
            "true",
            {"correction_attempts": {"T-1": 0}, "correction_bound": 2},
            {"task_id": "T-1"},
        ),
        (
            "false",
            {"correction_attempts": {"T-1": 5}, "correction_bound": 2},
            {"task_id": "T-1"},
        ),
    ],
}


# ---------------------------------------------------------------------------
# Structural assertions
# ---------------------------------------------------------------------------


class TestBranchDeclarations:
    def test_every_multi_target_transition_declares_a_branch(self, declaration: Mapping[str, Any]) -> None:
        """Each non-1->1 transition must carry a ``branch`` block."""
        transitions = declaration["flow"]["transitions"]
        offenders: list[tuple[str, Any]] = []
        for node, edge in transitions.items():
            if isinstance(edge, list):
                if len(edge) > 1:
                    offenders.append((node, edge))
            elif isinstance(edge, Mapping):
                if "branch" not in edge:
                    offenders.append((node, edge))
            else:
                offenders.append((node, edge))
        assert not offenders, f"transitions without proper branch declaration: {offenders}"

    def test_every_branch_rule_is_resolvable(self, declaration: Mapping[str, Any]) -> None:
        """Each branch ``rule`` is either a registered predicate name
        or an inline ``result.<field> <op> <literal>`` expression."""
        from app.modules.ai.flow_resolver import _EXPR_RE  # type: ignore[attr-defined]

        transitions = declaration["flow"]["transitions"]
        known_predicates = flow_predicates.known()
        offenders: list[tuple[str, str]] = []
        for node, edge in transitions.items():
            if not isinstance(edge, Mapping):
                continue
            branch = edge.get("branch")
            if not isinstance(branch, Mapping):
                continue
            rule = branch.get("rule")
            assert isinstance(rule, str), f"node {node!r} branch missing 'rule'"
            assert rule.strip(), f"node {node!r} branch has empty 'rule'"
            if rule in known_predicates:
                continue
            if _EXPR_RE.match(rule) is not None:
                continue
            offenders.append((node, rule))
        assert not offenders, f"branch rules neither registered predicates nor inline " f"expressions: {offenders}"

    def test_every_branch_scenario_matches_a_declared_branch(self, declaration: Mapping[str, Any]) -> None:
        """The scenario registry covers every branch in the YAML — and only those."""
        transitions = declaration["flow"]["transitions"]
        branched_nodes = {node for node, edge in transitions.items() if isinstance(edge, Mapping) and "branch" in edge}
        scenario_nodes = set(_BRANCH_SCENARIOS)
        assert (
            branched_nodes == scenario_nodes
        ), f"scenario coverage drift: declared={branched_nodes} scenarios={scenario_nodes}"


# ---------------------------------------------------------------------------
# Resolver walk — every transition produces exactly one successor
# ---------------------------------------------------------------------------


class TestExhaustiveBranchWalk:
    def test_one_to_one_transitions_resolve_to_their_only_target(self, declaration: Mapping[str, Any]) -> None:
        transitions = declaration["flow"]["transitions"]
        for node, edge in transitions.items():
            if not isinstance(edge, list):
                continue
            if len(edge) == 0:
                # Terminal in the transition map -> resolver yields done_node sentinel.
                # Skip — terminal-node behavior is asserted separately below.
                continue
            (target,) = edge
            result = resolve_next(declaration, node, memory={}, last_dispatch_result=None)
            assert result == NextNode(name=target), f"node {node!r}: expected NextNode({target!r}), got {result!r}"

    @pytest.mark.parametrize(
        ("node", "branch_label", "memory", "last", "expected"),
        [
            (
                node,
                label,
                memory,
                last,
                # ``true``/``false`` yaml keys are quoted -> str keys at runtime.
                # We resolve the actual successor via the declaration.
                None,
            )
            for node, scenarios in _BRANCH_SCENARIOS.items()
            for (label, memory, last) in scenarios
        ],
    )
    def test_every_branch_outcome_resolves_to_its_declared_target(
        self,
        declaration: Mapping[str, Any],
        node: str,
        branch_label: str,
        memory: dict[str, Any],
        last: dict[str, Any] | None,
        expected: None,
    ) -> None:
        del expected  # parametrize placeholder; real expected resolved below
        transitions = declaration["flow"]["transitions"]
        edge = transitions[node]
        assert isinstance(edge, Mapping)
        branch = edge["branch"]
        target = branch[branch_label]
        result = resolve_next(declaration, node, memory=memory, last_dispatch_result=last)
        assert result == NextNode(name=target), (
            f"node {node!r} branch={branch_label!r}: " f"expected NextNode({target!r}), got {result!r}"
        )

    def test_terminal_nodes_resolve_to_done_node_sentinel(self, declaration: Mapping[str, Any]) -> None:
        for node in declaration["terminalNodes"]:
            result = resolve_next(declaration, node, memory={}, last_dispatch_result=None)
            assert isinstance(result, TerminalSentinel)
            assert result.reason == "done_node"


# ---------------------------------------------------------------------------
# Reachability — every transition target is reachable from the entry node.
# ---------------------------------------------------------------------------


class TestReachability:
    def test_every_node_is_reachable_from_entry(self, declaration: Mapping[str, Any]) -> None:
        transitions = declaration["flow"]["transitions"]
        entry = declaration["_entry"]
        all_nodes = set(declaration["_node_names"])

        # BFS through every declared edge target (1->1 + every branch successor).
        seen: set[str] = {entry}
        frontier: list[str] = [entry]
        while frontier:
            current = frontier.pop()
            edge = transitions.get(current)
            successors: list[str] = []
            if isinstance(edge, list):
                successors.extend(edge)
            elif isinstance(edge, Mapping) and "branch" in edge:
                branch = edge["branch"]
                successors.append(branch["true"])
                successors.append(branch["false"])
            for successor in successors:
                if successor not in seen:
                    seen.add(successor)
                    frontier.append(successor)

        unreachable = all_nodes - seen
        assert not unreachable, f"nodes unreachable from entry {entry!r}: {sorted(unreachable)}"


# ---------------------------------------------------------------------------
# Purity — the walk must not pull an LLM SDK / core.llm into sys.modules.
# ---------------------------------------------------------------------------


class TestNoLLMClientInstantiation:
    def test_resolver_walk_does_not_pull_core_llm_or_provider_sdk(self, declaration: Mapping[str, Any]) -> None:
        """Snapshot ``sys.modules`` before/after a full walk; assert no LLM modules leaked.

        The resolver is already pure (FEAT-009 / T-211); this guard
        catches a future regression where someone adds a "helper" import
        in ``flow_resolver.py`` or ``flow_predicates.py`` that pulls
        ``core.llm`` transitively.
        """
        forbidden_prefixes = ("anthropic", "openai")

        def _llm_modules() -> set[str]:
            mods: set[str] = set()
            for name in sys.modules:
                if name == "app.core.llm":
                    mods.add(name)
                if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes):
                    mods.add(name)
            return mods

        before = _llm_modules()
        # Walk every branch + every 1->1 transition.
        transitions = declaration["flow"]["transitions"]
        for node, edge in transitions.items():
            if isinstance(edge, list):
                if len(edge) == 0:
                    continue
                resolve_next(declaration, node, memory={}, last_dispatch_result=None)
            elif isinstance(edge, Mapping) and "branch" in edge:
                for _label, memory, last in _BRANCH_SCENARIOS[node]:
                    resolve_next(declaration, node, memory=memory, last_dispatch_result=last)
        after = _llm_modules()

        leaked = after - before
        assert not leaked, f"resolver walk pulled LLM modules into sys.modules: {sorted(leaked)}"
