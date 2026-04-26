"""Lifespan-time executor-coverage validator (FEAT-009 / T-213).

For every agent loaded by the orchestrator and every node that agent
declares, exactly one of:

* the executor registry has a binding for ``(agent_ref, node_name)``, or
* the ``no_executor`` exemption table has an entry for that key.

Otherwise the lifespan refuses to boot and names every offending node.
Mirrors the FEAT-008 ``validate_effector_coverage`` shape so reviewers
recognize the pattern at sight.

The validator does not load agents itself — the caller hands it the
agent declarations and the registry, so the test surface stays small.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.modules.ai.executors.binding import iter_no_executor_exemptions
from app.modules.ai.executors.registry import ExecutorRegistry


class ExecutorCoverageError(RuntimeError):
    """Raised when one or more agent nodes have no executor and no exemption."""


@dataclass(frozen=True, slots=True)
class _AgentCoverageInput:
    agent_ref: str
    node_names: frozenset[str]


def validate_executor_coverage(
    registry: ExecutorRegistry,
    agents: Iterable[Mapping[str, Any]],
) -> None:
    """Refuse to return when any node is unbound and unexempted.

    ``agents`` is an iterable of declarations (parsed YAML).  Each
    declaration must carry a top-level ``ref`` (the ``agent_ref``) and
    a ``nodes`` list whose entries each carry a ``name``.

    On failure raises :class:`ExecutorCoverageError` with a single
    message that lists every offending ``(agent_ref, node_name)`` —
    so an operator can fix all bootstrap gaps in one pass instead of
    one-at-a-time.
    """
    exemptions = {key for key, _reason in iter_no_executor_exemptions()}
    registered = registry.registered_keys()

    inputs = [_normalize(decl) for decl in agents]
    missing: list[tuple[str, str]] = []
    for inp in inputs:
        for node_name in sorted(inp.node_names):
            key = (inp.agent_ref, node_name)
            if key in registered or key in exemptions:
                continue
            missing.append(key)

    if missing:
        listing = "\n".join(f"  - {ref!r} :: {node!r}" for ref, node in missing)
        raise ExecutorCoverageError(
            "executor coverage check failed: "
            f"{len(missing)} node(s) have neither a registered executor "
            "nor a no_executor exemption:\n" + listing
        )


def _normalize(decl: Mapping[str, Any]) -> _AgentCoverageInput:
    ref = decl.get("ref")
    if not isinstance(ref, str) or not ref:
        raise ExecutorCoverageError(f"agent declaration missing 'ref': {decl!r}")
    nodes_raw = cast(list[Any], decl.get("nodes") or [])
    names: set[str] = set()
    for node in nodes_raw:
        if isinstance(node, Mapping):
            name = cast(Mapping[str, Any], node).get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return _AgentCoverageInput(agent_ref=ref, node_names=frozenset(names))
