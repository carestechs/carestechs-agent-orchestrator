"""Pydantic v2 result schemas for the lifecycle-agent@0.3.0 LLM-content nodes.

Each composite node binds an :class:`LLMContentExecutor` to one of these
schemas (FEAT-011 / T-254).  The executor validates the provider's
``ToolCall.arguments`` payload against the schema; a failed validation
returns a ``failed`` envelope with ``detail="result_schema_validation_failed"``.

The schemas are intentionally minimal — enough to drive the resolver's
branch predicates and to populate the typed :class:`LifecycleMemory`.
A future PR may grow extra fields (e.g. plan markdown formatting) but
field-name additions are backward compatible: every model uses
``extra='ignore'`` so unrecognised keys don't fail validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_FORBID_NONE = ConfigDict(extra="ignore")


class LoadWorkItemResult(BaseModel):
    """LLM-synthesized brief for the ``load_work_item`` composite node."""

    model_config = _FORBID_NONE

    work_item_id: str = Field(description="External ref (e.g. FEAT-099) parsed from the brief")
    title: str
    summary: str


class GenerateTasksTask(BaseModel):
    """One task in the generated task list (item shape inside :class:`GenerateTasksResult`)."""

    model_config = _FORBID_NONE

    id: str
    title: str
    executor: str = Field(description="Symbolic executor for the task (e.g. 'claude-code')")


class GenerateTasksResult(BaseModel):
    """LLM-synthesized task breakdown for the ``generate_tasks`` composite node."""

    model_config = _FORBID_NONE

    tasks: list[GenerateTasksTask]


class GeneratePlanResult(BaseModel):
    """LLM-synthesized implementation plan for the ``generate_plan`` composite node."""

    model_config = _FORBID_NONE

    task_id: str
    plan_markdown: str


class ReviewImplementationResult(BaseModel):
    """LLM verdict for the ``review_implementation`` composite node."""

    model_config = _FORBID_NONE

    task_id: str
    verdict: Literal["pass", "fail"]
    feedback: str


__all__ = [
    "GeneratePlanResult",
    "GenerateTasksResult",
    "GenerateTasksTask",
    "LoadWorkItemResult",
    "ReviewImplementationResult",
]
