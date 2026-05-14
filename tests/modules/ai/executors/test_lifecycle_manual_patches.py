"""Unit tests for FEAT-015 memory-patch builders (T-297 / T-300).

Pure-function coverage — no DB, no session.  Each builder is exercised
across: empty payload (approve unchanged), valid payload (expected
patch shape), malformed payload (Pydantic ValidationError), and
business-rule violation (ValueError raised by the builder when memory
preconditions aren't met).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.ai.executors.lifecycle_manual_patches import (
    apply_brief_correction,
    apply_plan_correction,
    apply_review_verdict,
    apply_tasks_correction,
)
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleReview,
    LifecycleTask,
    WorkItemRef,
    to_run_memory,
)


# ---------------------------------------------------------------------------
# Memory builders for tests
# ---------------------------------------------------------------------------


def _memory(
    *,
    work_item: WorkItemRef | None = None,
    tasks: list[LifecycleTask] | None = None,
    current_task_id: str | None = None,
    review_history: list[LifecycleReview] | None = None,
) -> dict[str, Any]:
    """Return a RunMemory.data dict for the given LifecycleMemory shape."""
    mem = LifecycleMemory(
        work_item=work_item,
        tasks=tasks or [],
        current_task_id=current_task_id,
        review_history=review_history or [],
    )
    return {LIFECYCLE_MEMORY_NS: to_run_memory(mem)}


def _memory_with_work_item(
    *, ref: str = "FEAT-001", title: str = "Title", type_: str = "FEAT"
) -> dict[str, Any]:
    return _memory(
        work_item=WorkItemRef(id=ref, type=type_, title=title, path="")  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# apply_brief_correction
# ---------------------------------------------------------------------------


class TestApplyBriefCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        mem = _memory_with_work_item()
        assert apply_brief_correction({}, mem) == {}

    def test_work_item_with_all_none_returns_empty_patch(self) -> None:
        """workItem present but every field unset → no edit → empty patch."""
        mem = _memory_with_work_item()
        assert apply_brief_correction({"workItem": {}}, mem) == {}

    def test_title_override_emits_namespace_patch(self) -> None:
        mem = _memory_with_work_item(title="Original")
        patch = apply_brief_correction({"workItem": {"title": "Edited"}}, mem)
        assert LIFECYCLE_MEMORY_NS in patch
        wi = patch[LIFECYCLE_MEMORY_NS]["workItem"]
        assert wi["title"] == "Edited"
        assert wi["id"] == "FEAT-001"  # carried over

    def test_type_override_changes_kind(self) -> None:
        mem = _memory_with_work_item(type_="FEAT")
        patch = apply_brief_correction({"workItem": {"type": "BUG"}}, mem)
        wi = patch[LIFECYCLE_MEMORY_NS]["workItem"]
        assert wi["type"] == "BUG"
        assert wi["title"] == "Title"  # carried over

    def test_invalid_type_raises_validation_error(self) -> None:
        mem = _memory_with_work_item()
        with pytest.raises(ValidationError):
            apply_brief_correction({"workItem": {"type": "INVALID"}}, mem)

    def test_no_memory_workitem_raises_value_error(self) -> None:
        empty = _memory()
        with pytest.raises(ValueError, match="not yet populated"):
            apply_brief_correction({"workItem": {"title": "X"}}, empty)

    def test_snake_case_alias_accepted(self) -> None:
        mem = _memory_with_work_item()
        patch = apply_brief_correction({"work_item": {"title": "Snake"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["workItem"]["title"] == "Snake"

    def test_unknown_field_rejected_by_extra_forbid(self) -> None:
        mem = _memory_with_work_item()
        with pytest.raises(ValidationError):
            apply_brief_correction({"unknownField": "x"}, mem)


# ---------------------------------------------------------------------------
# apply_tasks_correction
# ---------------------------------------------------------------------------


class TestApplyTasksCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        assert apply_tasks_correction({}, _memory()) == {}

    def test_replacement_list_carries_over_optional_fields(self) -> None:
        """Operator preserves LLM-generated description / criteria by id match."""
        existing = LifecycleTask(
            id="T-1",
            title="LLM Title",
            description="LLM description",
            acceptance_criteria=["a", "b"],
            complexity="large",
        )
        mem = _memory(tasks=[existing])
        patch = apply_tasks_correction(
            {"tasks": [{"id": "T-1", "title": "Operator Title"}]}, mem
        )
        tasks_out = patch[LIFECYCLE_MEMORY_NS]["tasks"]
        assert len(tasks_out) == 1
        assert tasks_out[0]["id"] == "T-1"
        assert tasks_out[0]["title"] == "Operator Title"
        # Optional fields preserved.
        assert tasks_out[0]["description"] == "LLM description"
        assert tasks_out[0]["acceptanceCriteria"] == ["a", "b"]
        assert tasks_out[0]["complexity"] == "large"

    def test_new_task_id_uses_defaults(self) -> None:
        """Operator-introduced task: no existing entry → safe defaults."""
        mem = _memory()
        patch = apply_tasks_correction(
            {"tasks": [{"id": "T-new", "title": "Brand new"}]}, mem
        )
        task = patch[LIFECYCLE_MEMORY_NS]["tasks"][0]
        assert task["id"] == "T-new"
        assert task["complexity"] == "medium"
        assert task["acceptanceCriteria"] == []

    def test_operator_summary_becomes_description_for_new_tasks(self) -> None:
        mem = _memory()
        patch = apply_tasks_correction(
            {"tasks": [{"id": "T-1", "title": "X", "summary": "short doc"}]},
            mem,
        )
        assert patch[LIFECYCLE_MEMORY_NS]["tasks"][0]["description"] == "short doc"

    def test_replacement_can_be_longer_or_shorter_than_original(self) -> None:
        """Operator can grow or shrink the LLM list."""
        mem = _memory(tasks=[LifecycleTask(id="T-1", title="A")])
        patch_grow = apply_tasks_correction(
            {"tasks": [{"id": "T-1", "title": "A"}, {"id": "T-2", "title": "B"}]},
            mem,
        )
        assert len(patch_grow[LIFECYCLE_MEMORY_NS]["tasks"]) == 2
        patch_shrink = apply_tasks_correction(
            {"tasks": [{"id": "T-only", "title": "Solo"}]}, mem
        )
        assert len(patch_shrink[LIFECYCLE_MEMORY_NS]["tasks"]) == 1

    def test_empty_list_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            apply_tasks_correction({"tasks": []}, _memory())

    def test_missing_required_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            apply_tasks_correction({"tasks": [{"title": "No ID"}]}, _memory())

    def test_missing_required_title_raises(self) -> None:
        with pytest.raises(ValidationError):
            apply_tasks_correction({"tasks": [{"id": "T-1"}]}, _memory())


# ---------------------------------------------------------------------------
# apply_plan_correction
# ---------------------------------------------------------------------------


class TestApplyPlanCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        mem = _memory(current_task_id="T-1")
        assert apply_plan_correction({}, mem) == {}

    def test_plan_writes_to_top_level_plans_sidecar(self) -> None:
        """plans[task_id].plan_markdown — NOT under lifecycle.v1."""
        mem = _memory(current_task_id="T-7")
        patch = apply_plan_correction({"plan": "# Updated"}, mem)
        assert "plans" in patch
        assert LIFECYCLE_MEMORY_NS not in patch  # explicitly NOT here
        assert patch["plans"]["T-7"] == {"plan_markdown": "# Updated"}

    def test_plan_merges_with_existing_plans(self) -> None:
        """Per-task iterations must not stomp prior plans (BUG-013 shape)."""
        mem = _memory(current_task_id="T-2")
        mem["plans"] = {"T-1": {"plan_markdown": "first plan"}}
        patch = apply_plan_correction({"plan": "second plan"}, mem)
        assert patch["plans"]["T-1"] == {"plan_markdown": "first plan"}
        assert patch["plans"]["T-2"] == {"plan_markdown": "second plan"}

    def test_no_current_task_id_raises(self) -> None:
        no_task = _memory_with_work_item()
        with pytest.raises(ValueError, match="no current_task_id"):
            apply_plan_correction({"plan": "X"}, no_task)


# ---------------------------------------------------------------------------
# apply_review_verdict
# ---------------------------------------------------------------------------


class TestApplyReviewVerdict:
    def test_pass_verdict_appends_entry(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_review_verdict({"verdict": "pass"}, mem)
        history = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"]
        assert len(history) == 1
        assert history[0]["verdict"] == "pass"
        assert history[0]["taskId"] == "T-1"

    def test_fail_with_feedback_records_feedback(self) -> None:
        mem = _memory(current_task_id="T-2")
        patch = apply_review_verdict(
            {"verdict": "fail", "feedback": "needs more tests"}, mem
        )
        entry = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"][-1]
        assert entry["verdict"] == "fail"
        assert entry["feedback"] == "needs more tests"

    def test_fail_without_feedback_writes_empty_string(self) -> None:
        """Optional feedback maps to empty string in the persisted entry."""
        mem = _memory(current_task_id="T-1")
        patch = apply_review_verdict({"verdict": "fail"}, mem)
        entry = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"][-1]
        # _patch_review uses str() so None feedback → ""; we pass "" up.
        assert entry["feedback"] == ""

    def test_attempt_increments_on_second_review(self) -> None:
        prior = LifecycleReview(
            task_id="T-1",
            attempt=1,
            verdict="fail",
            feedback="first",
            written_to="memory",
        )
        mem = _memory(current_task_id="T-1", review_history=[prior])
        patch = apply_review_verdict({"verdict": "pass"}, mem)
        history = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"]
        assert len(history) == 2
        assert history[-1]["attempt"] == 2

    def test_invalid_verdict_raises_validation_error(self) -> None:
        mem = _memory(current_task_id="T-1")
        with pytest.raises(ValidationError):
            apply_review_verdict({"verdict": "needs_changes"}, mem)

    def test_missing_verdict_raises_validation_error(self) -> None:
        mem = _memory(current_task_id="T-1")
        with pytest.raises(ValidationError):
            apply_review_verdict({}, mem)

    def test_no_current_task_id_raises(self) -> None:
        no_task = _memory_with_work_item()
        with pytest.raises(ValueError, match="no current_task_id"):
            apply_review_verdict({"verdict": "pass"}, no_task)
