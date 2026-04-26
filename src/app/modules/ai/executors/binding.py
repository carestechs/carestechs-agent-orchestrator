"""Executor binding + ``no_executor`` exemption (FEAT-009 / T-213).

A binding pairs an ``Executor`` instance with the per-call configuration
the registry hands to the runtime loop on resolve.  ``no_executor``
mirrors the FEAT-008 effector pattern: a node that intentionally has
no executor (e.g. a future ``terminate`` sentinel before the
FlowResolver eats it, or a policy-only node that should never be
dispatchable) gets an explicit exemption with a ≥10-char human-readable
reason — anything shorter is a review blocker.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.modules.ai.executors.base import Executor

_MIN_NO_EXECUTOR_REASON = 10

_exemptions: dict[tuple[str, str], str] = {}


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    """Resolved binding handed to the runtime loop on ``ExecutorRegistry.resolve``.

    ``timeout_seconds`` is consumed by the remote adapter (T-215) and
    optionally by the local adapter as a soft bound.  ``extras`` is
    threaded into ``DispatchContext.extras`` so the bootstrap can attach
    per-node configuration (e.g. an LLM system prompt for a content
    executor) without growing the protocol.
    """

    agent_ref: str
    node_name: str
    executor: Executor
    timeout_seconds: float | None = None
    extras: Mapping[str, Any] = field(default_factory=dict[str, Any])


def no_executor(agent_ref: str, node_name: str, reason: str) -> tuple[str, str]:
    """Mark ``(agent_ref, node_name)`` as intentionally bound to no executor.

    The lifespan-time validator (``validate_executor_coverage``) walks
    every node declared by every loaded agent and flags any node with
    neither a registration nor an exemption.  Calling this twice with
    the same key overwrites the reason — the canonical site is the
    bootstrap module.
    """
    if len(reason.strip()) < _MIN_NO_EXECUTOR_REASON:
        raise ValueError(
            f"no_executor reason must be ≥{_MIN_NO_EXECUTOR_REASON} chars: "
            f"({agent_ref!r}, {node_name!r}) got {reason!r}"
        )
    key = (agent_ref, node_name)
    _exemptions[key] = reason.strip()
    return key


def iter_no_executor_exemptions() -> Iterator[tuple[tuple[str, str], str]]:
    """Yield ``((agent_ref, node_name), reason)`` for every registered exemption."""
    yield from _exemptions.items()


def _reset_exemptions_for_tests() -> None:
    """Test hook — clear the module-level exemption dict between tests."""
    _exemptions.clear()


__all__ = [
    "ExecutorBinding",
    "_reset_exemptions_for_tests",
    "iter_no_executor_exemptions",
    "no_executor",
]
