"""Typed shape of the lifecycle agent's per-run memory (FEAT-005 / T-089).

The lifecycle agent accumulates state across its 8 stages.  The runtime
persists the whole shape inside :attr:`RunMemory.data` (JSONB in v2,
embedded JSON in the JSONL trace in v1).  Serialization is a straight
dump of this model; loading validates back through it.

Per AD-4, the shape is per-run only — never shared across runs.
"""

from __future__ import annotations

from typing import Any, Literal

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
