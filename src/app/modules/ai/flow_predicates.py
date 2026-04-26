"""Branch-rule predicates for the FlowResolver (FEAT-009 / T-211).

The resolver picks the next node from the agent's flow declaration.  When a
transition has multiple targets, the YAML carries a ``branch`` block whose
``rule`` is either a *predicate name* registered here, or a tiny expression
of the form ``result.<field> <op> <literal>`` (handled inline in the
resolver — not here).

Predicates are pure functions over ``(memory, last_dispatch_result)``.  They
must not perform I/O, must not import an LLM client, and must be safe to
call from any thread (no shared mutable state).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

PredicateFn = Callable[[Mapping[str, Any], Mapping[str, Any] | None], bool]

_REGISTRY: dict[str, PredicateFn] = {}


def register(name: str) -> Callable[[PredicateFn], PredicateFn]:
    """Decorator: register a branch predicate under ``name``."""

    def _wrap(fn: PredicateFn) -> PredicateFn:
        if name in _REGISTRY:
            raise ValueError(f"branch predicate already registered: {name!r}")
        _REGISTRY[name] = fn
        return fn

    return _wrap


def get(name: str) -> PredicateFn:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown branch predicate {name!r}; " f"known: {sorted(_REGISTRY)}") from exc


def known() -> frozenset[str]:
    return frozenset(_REGISTRY)


# ---------------------------------------------------------------------------
# Built-in predicates for lifecycle-agent@0.2.0 (T-222)
# ---------------------------------------------------------------------------


@register("unplanned_tasks_remaining")
def _unplanned_tasks_remaining(  # pyright: ignore[reportUnusedFunction] -- accessed via registry
    memory: Mapping[str, Any], _last: Mapping[str, Any] | None
) -> bool:
    """True iff ``memory.tasks`` contains a task without an entry in ``memory.plans``."""
    tasks = cast(Mapping[str, Any], memory.get("tasks") or {})
    plans = cast(Mapping[str, Any], memory.get("plans") or {})
    return any(task_id not in plans for task_id in tasks)


@register("correction_attempts_under_bound")
def _correction_attempts_under_bound(  # pyright: ignore[reportUnusedFunction] -- accessed via registry
    memory: Mapping[str, Any], last: Mapping[str, Any] | None
) -> bool:
    """True iff the bound has not yet been reached for the task in ``last.task_id``.

    Reads ``memory.correction_attempts[task_id]`` and ``memory.correction_bound``.
    Defaults: attempts=0, bound=2 (matches ``LIFECYCLE_MAX_CORRECTIONS`` default).
    """
    if last is None:
        return True
    task_id = last.get("task_id")
    if task_id is None:
        return True
    attempts_map = cast(Mapping[str, Any], memory.get("correction_attempts") or {})
    attempts = cast(int, attempts_map.get(task_id, 0))
    bound = cast(int, memory.get("correction_bound", 2))
    return int(attempts) < int(bound)
