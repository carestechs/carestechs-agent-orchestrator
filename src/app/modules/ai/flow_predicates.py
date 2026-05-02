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
    """True iff any task in ``lifecycle.v1.tasks`` has no entry in ``memory.plans``.

    Reads the canonical task list via ``read_lifecycle_memory`` (the
    same accessor used by every other lifecycle reader) and matches it
    against the top-level ``plans`` dict written by ``_patch_generate_plan``.

    Previously this read top-level ``memory["tasks"]`` directly — that
    slot is a sidecar written *only* by ``_patch_generate_plan`` for
    already-planned tasks, so the predicate was always-False on
    multi-task work items (the not-yet-planned tasks never appeared in
    the sidecar to compare against).  Single-task items got lucky.
    """
    # Lazy import — flow_predicates is reachable from the resolver
    # before any lifecycle module has been imported.
    from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory

    lifecycle_memory = read_lifecycle_memory(memory)
    plans = cast(Mapping[str, Any], memory.get("plans") or {})
    return any(task.id not in plans for task in lifecycle_memory.tasks)


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


# ---------------------------------------------------------------------------
# Built-in predicates for lifecycle-agent@0.3.0 (FEAT-011 / T-251)
# ---------------------------------------------------------------------------


@register("review_passed")
def _review_passed(  # pyright: ignore[reportUnusedFunction] -- accessed via registry
    memory: Mapping[str, Any], last: Mapping[str, Any] | None
) -> bool:
    """True iff the latest review verdict is ``"pass"``; False on ``"fail"``; raises otherwise.

    Reads ``result.verdict`` produced by the ``review_implementation`` node
    (LLM-content executor that returns a structured ``{verdict, feedback}``
    payload).  The v0.3.0 composite executor pattern wraps the LLM verdict
    behind an engine transition, and the wake-delivered envelope's
    ``result`` carries engine metadata only — so the predicate also falls
    back to ``memory.review_history[-1].verdict``, which the composite
    persists via its memory-patch builder.  Any value other than
    ``"pass"`` / ``"fail"`` is a contract violation.
    """
    verdict: Any = None
    if last is not None:
        verdict = last.get("verdict")
    if verdict is None:
        history_raw: Any = memory.get("review_history")
        if isinstance(history_raw, list) and history_raw:
            history_list = cast(list[Any], history_raw)
            tail: Any = history_list[-1]
            if isinstance(tail, Mapping):
                tail_typed = cast(Mapping[str, Any], tail)
                verdict = tail_typed.get("verdict")
    if verdict == "pass":
        return True
    if verdict == "fail":
        return False
    raise ValueError(f"review_passed predicate: result.verdict must be 'pass' or 'fail'; got {verdict!r}")


@register("task_rejected")
def _task_rejected(  # pyright: ignore[reportUnusedFunction] -- accessed via registry
    _memory: Mapping[str, Any], last: Mapping[str, Any] | None
) -> bool:
    """True iff ``last.outcome == "rejected"``; False on ``"approved"``; raises otherwise.

    Reads ``result.outcome`` produced by an approval-stage node (e.g. the
    ``correct_implementation`` LocalExecutor — see FEAT-011 design doc). Any
    value other than ``"approved"`` / ``"rejected"`` is a contract violation;
    the producing executor must constrain its output via ``result_schema``.
    """
    if last is None:
        raise ValueError(
            "task_rejected predicate: no dispatch result available; "
            "the producing approval-stage node must run before this branch"
        )
    outcome = last.get("outcome")
    if outcome == "rejected":
        return True
    if outcome == "approved":
        return False
    raise ValueError(f"task_rejected predicate: result.outcome must be 'approved' or 'rejected'; got {outcome!r}")
