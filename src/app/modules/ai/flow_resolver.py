"""Pure-function next-node resolver (FEAT-009 / T-211).

After FEAT-006 made the lifecycle flow deterministic, the runtime loop's
node selection became a function of the YAML declaration plus run state —
not a call to an LLM.  This module is the resolver.

Contract::

    resolve_next(declaration, current_node, memory, last_dispatch_result)
        -> NextNode(name=str) | TerminalSentinel(reason=str)

* For 1→1 transitions the resolver returns the only target.
* For multi-target transitions the YAML edge must carry a ``branch`` block:

  .. code-block:: yaml

      transitions:
        review_implementation:
          branch:
            rule: "result.verdict == 'pass'"   # or a predicate name
            true: close_work_item
            false: corrections

  ``rule`` is either a predicate name registered in
  :mod:`app.modules.ai.flow_predicates`, or a tiny expression of the form
  ``result.<field> <op> <literal>`` with ``op`` in ``{==, !=}``.

The resolver is a pure function: no I/O, no DB session, no LLM client, no
asyncio.  It is the load-bearing artifact for the FEAT-006 alignment in
the runtime loop (T-220).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.modules.ai import flow_predicates


class FlowDeclarationError(ValueError):
    """Raised when the resolver is asked to advance from an under-specified flow."""


@dataclass(frozen=True, slots=True)
class NextNode:
    name: str


@dataclass(frozen=True, slots=True)
class TerminalSentinel:
    reason: str  # one of: "done_node" | "correction_budget_exceeded" | future...


ResolverResult = NextNode | TerminalSentinel


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def resolve_next(
    declaration: Mapping[str, Any],
    current_node: str,
    memory: Mapping[str, Any],
    last_dispatch_result: Mapping[str, Any] | None,
) -> ResolverResult:
    """Return the next node, or a terminal sentinel.

    ``declaration`` is the parsed agent YAML (the ``flow`` + ``terminalNodes``
    blocks specifically).  ``memory`` is the run's current memory snapshot.
    ``last_dispatch_result`` is the result envelope from the executor that
    just completed (``None`` only on the very first iteration before any
    dispatch has run).
    """
    terminal_nodes: frozenset[str] = frozenset(
        cast(list[str], declaration.get("terminalNodes") or [])
    )
    flow = cast(Mapping[str, Any], declaration.get("flow") or {})
    transitions = cast(Mapping[str, Any], flow.get("transitions") or {})

    # Executor short-circuit: an executor can ask the run to terminate
    # by returning ``result.terminal=true`` with a reason.
    if last_dispatch_result is not None and last_dispatch_result.get("terminal"):
        reason = str(last_dispatch_result.get("terminal_reason") or "policy_terminated")
        return TerminalSentinel(reason=reason)

    if current_node in terminal_nodes:
        return TerminalSentinel(reason="done_node")

    edge: Any = transitions.get(current_node)
    if edge is None:
        raise FlowDeclarationError(
            f"node {current_node!r} has no transition entry in flow.transitions"
        )

    # 1→1 — the simple list form ``[next_node]``.
    if isinstance(edge, list):
        edge_list = cast(list[str], edge)
        if len(edge_list) == 0:
            return TerminalSentinel(reason="done_node")
        if len(edge_list) == 1:
            return NextNode(name=edge_list[0])
        raise FlowDeclarationError(
            f"node {current_node!r} has {len(edge_list)} targets but no 'branch' block; "
            "multi-target transitions must declare a branch rule"
        )

    # Branch form — ``{branch: {rule, true, false}}``.
    if isinstance(edge, dict) and "branch" in edge:
        branch = cast(Mapping[str, Any], edge["branch"])
        return _resolve_branch(current_node, branch, memory, last_dispatch_result)

    raise FlowDeclarationError(
        f"node {current_node!r} has an unrecognized transition shape: {edge!r}"
    )


# ---------------------------------------------------------------------------
# Branch evaluation
# ---------------------------------------------------------------------------


def _resolve_branch(
    current_node: str,
    branch: Mapping[str, Any],
    memory: Mapping[str, Any],
    last: Mapping[str, Any] | None,
) -> NextNode:
    rule = branch.get("rule")
    if not isinstance(rule, str) or not rule.strip():
        raise FlowDeclarationError(f"node {current_node!r} branch is missing a non-empty 'rule'")
    true_target = branch.get("true")
    false_target = branch.get("false")
    if not isinstance(true_target, str) or not isinstance(false_target, str):
        raise FlowDeclarationError(f"node {current_node!r} branch must declare 'true' and 'false' targets")

    chosen = _evaluate_rule(rule, memory, last, current_node=current_node)
    return NextNode(name=true_target if chosen else false_target)


_EXPR_RE = re.compile(
    r"""^\s*result\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)
        \s*(?P<op>==|!=)\s*
        (?P<literal>'[^']*'|"[^"]*"|true|false|null|-?\d+(?:\.\d+)?)\s*$""",
    re.VERBOSE,
)


def _evaluate_rule(
    rule: str,
    memory: Mapping[str, Any],
    last: Mapping[str, Any] | None,
    *,
    current_node: str,
) -> bool:
    expr_match = _EXPR_RE.match(rule)
    if expr_match is not None:
        return _evaluate_expression(
            field=expr_match["field"],
            op=expr_match["op"],
            literal=expr_match["literal"],
            last=last,
            current_node=current_node,
        )
    # Treat the rule as a predicate name.
    try:
        predicate = flow_predicates.get(rule)
    except KeyError as exc:
        raise FlowDeclarationError(
            f"node {current_node!r} branch rule {rule!r} is neither a "
            f"recognized expression nor a registered predicate"
        ) from exc
    return bool(predicate(memory, last))


def _evaluate_expression(
    *,
    field: str,
    op: str,
    literal: str,
    last: Mapping[str, Any] | None,
    current_node: str,
) -> bool:
    if last is None:
        raise FlowDeclarationError(
            f"node {current_node!r} branch rule references result.{field} but " "no dispatch result is available"
        )
    actual = last.get(field)
    expected = _parse_literal(literal)
    if op == "==":
        return actual == expected
    return actual != expected


def _parse_literal(literal: str) -> Any:
    if literal in ("true", "false"):
        return literal == "true"
    if literal == "null":
        return None
    if literal.startswith("'") or literal.startswith('"'):
        return literal[1:-1]
    if "." in literal:
        return float(literal)
    return int(literal)
