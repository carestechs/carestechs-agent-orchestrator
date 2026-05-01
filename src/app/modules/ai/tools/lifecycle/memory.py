"""Typed shape of the lifecycle agent's per-run memory (FEAT-005 / T-089).

The lifecycle agent accumulates state across its 8 stages.  The runtime
persists the whole shape inside :attr:`RunMemory.data` (JSONB in v2,
embedded JSON in the JSONL trace in v1).  Serialization is a straight
dump of this model; loading validates back through it.

Per AD-4, the shape is per-run only — never shared across runs.

FEAT-011 / T-255: shape (1) decision lands a stable namespace
(``lifecycle.v1``) under :attr:`RunMemory.data` so v0.3.0 executors
read/write the typed schema while leaving room for ``lifecycle.v2`` if a
future schema change is needed.  The ``read_lifecycle_memory`` /
``write_lifecycle_memory`` helpers are the executor-facing surface.
v0.1.0 stored the typed schema at the top level of
:attr:`RunMemory.data`; ``read_lifecycle_memory`` falls back to that
shape for backward compatibility on first read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_FORBID_CAMEL = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
)


class WorkItemRef(BaseModel):
    """Identity of the work item driving the run."""

    model_config = _FORBID_CAMEL

    id: str
    type: Literal["FEAT", "BUG", "IMP"]
    title: str
    path: str


class LifecycleTask(BaseModel):
    """One task in the generated task list."""

    model_config = _FORBID_CAMEL

    id: str
    title: str
    executor: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    plan_path: str | None = None
    # BUG-004: per-task engine entity id minted by ``propose_tasks``
    # (T1 ``create_item`` against the task workflow).  Downstream
    # engine-bound nodes (``assign_task``, ``generate_plan``,
    # ``submit_implementation``, ``approve_review``) resolve their
    # transition target from this field, not from ``Run.intake.engineItemId``
    # (which carries the *work item's* engine id, not the task's).
    engine_item_id: str | None = None
    # BUG-004: idempotency guard for ``submit_implementation``.  T9
    # (``implementing → impl_review``) must fire exactly once per task —
    # subsequent visits (after a rejection loop) skip the engine call
    # because the engine task is already past ``implementing``.
    submitted: bool = False


class LifecycleReview(BaseModel):
    """One recorded review verdict for a task."""

    model_config = _FORBID_CAMEL

    task_id: str
    attempt: int
    verdict: Literal["pass", "fail"]
    feedback: str
    written_to: str


class LifecycleMemory(BaseModel):
    """Full per-run memory shape for the lifecycle agent."""

    model_config = _FORBID_CAMEL

    work_item: WorkItemRef | None = None
    tasks: list[LifecycleTask] = Field(default_factory=list[LifecycleTask])
    current_task_id: str | None = None
    review_history: list[LifecycleReview] = Field(default_factory=list[LifecycleReview])
    files_touched_per_task: dict[str, list[str]] = Field(default_factory=dict)
    correction_attempts: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def empty(cls) -> LifecycleMemory:
        return cls()


def from_run_memory(data: dict[str, Any]) -> LifecycleMemory:
    """Hydrate a :class:`LifecycleMemory` from ``RunMemory.data`` JSON."""
    if not data:
        return LifecycleMemory.empty()
    return LifecycleMemory.model_validate(data)


def to_run_memory(memory: LifecycleMemory) -> dict[str, Any]:
    """Return a JSON-safe dict suitable for assigning to ``RunMemory.data``."""
    return memory.model_dump(mode="json", by_alias=True, exclude_none=False)


# ---------------------------------------------------------------------------
# FEAT-011 / T-255 — namespaced helpers for v0.3.0 executors
# ---------------------------------------------------------------------------

LIFECYCLE_MEMORY_NS = "lifecycle.v1"
"""Stable namespace inside ``RunMemory.data`` for the typed lifecycle shape.

The ``.v1`` suffix is reserved for the schema-evolution case; a future
breaking change would land ``lifecycle.v2`` alongside, with v0.1.0
readers continuing to find ``lifecycle.v1``.
"""


def read_lifecycle_memory(run_memory_data: Mapping[str, Any] | None) -> LifecycleMemory:
    """Hydrate a :class:`LifecycleMemory` from ``RunMemory.data``.

    Looks up the value under :data:`LIFECYCLE_MEMORY_NS`.  Falls back to
    the v0.1.0 shape (typed schema serialised at the top level) so a run
    that started on v0.1.0 and resumed on v0.3.0 keeps reading.  Returns
    an empty model when the data is absent or the namespace is missing.
    """
    if not run_memory_data:
        return LifecycleMemory.empty()
    nested = run_memory_data.get(LIFECYCLE_MEMORY_NS)
    if nested is None:
        # Backward compat: v0.1.0 stores the shape at the top level.
        # Hydrate as a best-effort; if validation fails, return empty.
        try:
            return from_run_memory(dict(run_memory_data))
        except Exception:
            return LifecycleMemory.empty()
    if not isinstance(nested, Mapping):
        return LifecycleMemory.empty()
    nested_typed = cast(Mapping[str, Any], nested)
    nested_dict: dict[str, Any] = dict(nested_typed)
    return LifecycleMemory.model_validate(nested_dict)


def write_lifecycle_memory(memory: LifecycleMemory) -> dict[str, Any]:
    """Return a ``__memory_patch`` dict that writes the model under the namespace.

    The runtime loop merges the patch into ``RunMemory.data`` shallowly,
    so wrapping the serialised model under :data:`LIFECYCLE_MEMORY_NS`
    leaves any non-namespaced bookkeeping (e.g. the runtime's own
    ``__feat009`` block) untouched.
    """
    return {LIFECYCLE_MEMORY_NS: to_run_memory(memory)}


def find_current_task(memory: LifecycleMemory) -> LifecycleTask | None:
    """Return the task indicated by ``current_task_id``, if any.

    BUG-004 helper: every task-lifecycle node addresses *the current
    task* (not an arbitrary one), so they share this lookup.
    """
    if memory.current_task_id is None:
        return None
    for task in memory.tasks:
        if task.id == memory.current_task_id:
            return task
    return None
