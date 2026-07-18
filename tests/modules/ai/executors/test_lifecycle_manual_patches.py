"""Unit tests for FEAT-015 memory-patch builders (T-297 / T-300).

Pure-function coverage — no DB, no session.  Each builder is exercised
across: empty payload (approve unchanged), valid payload (expected
patch shape), malformed payload (Pydantic ValidationError), and
business-rule violation (ValueError raised by the builder when memory
preconditions aren't met).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.ai.executors.lifecycle_manual_patches import (
    AssignmentConfirmedPayload,
    apply_assignment_confirmation,
    apply_brief_correction,
    apply_implementation_signal,
    apply_plan_correction,
    apply_review_verdict,
    apply_tasks_correction,
    intake_for_human_review,
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

    def test_no_memory_workitem_without_id_raises_value_error(self) -> None:
        # @0.6.0-human create-from-scratch path: requires id, title, type.
        # Supplying only title (missing id) must raise ValueError.
        empty = _memory()
        with pytest.raises(ValueError, match="requires workItem.id"):
            apply_brief_correction({"workItem": {"title": "X"}}, empty)

    def test_snake_case_alias_accepted(self) -> None:
        mem = _memory_with_work_item()
        patch = apply_brief_correction({"work_item": {"title": "Snake"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["workItem"]["title"] == "Snake"

    def test_unknown_field_rejected_by_extra_forbid(self) -> None:
        mem = _memory_with_work_item()
        with pytest.raises(ValidationError):
            apply_brief_correction({"unknownField": "x"}, mem)

    def test_reject_writes_rejections_sidecar(self) -> None:
        mem = _memory_with_work_item()
        patch = apply_brief_correction({"verdict": "reject", "feedback": "too vague"}, mem)
        assert "rejections" in patch
        assert patch["rejections"]["confirm_brief"]["feedback"] == "too vague"
        assert patch["rejections"]["confirm_brief"]["attempt"] == 1
        assert LIFECYCLE_MEMORY_NS not in patch

    def test_reject_without_feedback(self) -> None:
        mem = _memory_with_work_item()
        patch = apply_brief_correction({"verdict": "reject"}, mem)
        assert patch["rejections"]["confirm_brief"]["feedback"] == ""

    def test_reject_increments_attempt(self) -> None:
        mem = _memory_with_work_item()
        mem["rejections"] = {"confirm_brief": {"feedback": "first", "attempt": 1}}
        patch = apply_brief_correction({"verdict": "reject", "feedback": "second"}, mem)
        assert patch["rejections"]["confirm_brief"]["attempt"] == 2

    def test_approve_explicit_verdict(self) -> None:
        mem = _memory_with_work_item()
        assert apply_brief_correction({"verdict": "approve"}, mem) == {}


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

    def test_reject_writes_rejections_sidecar(self) -> None:
        mem = _memory(tasks=[LifecycleTask(id="T-1", title="A")])
        patch = apply_tasks_correction({"verdict": "reject", "feedback": "wrong tasks"}, mem)
        assert "rejections" in patch
        assert patch["rejections"]["confirm_tasks"]["feedback"] == "wrong tasks"
        assert LIFECYCLE_MEMORY_NS not in patch

    def test_reject_skips_task_replacement(self) -> None:
        mem = _memory(tasks=[LifecycleTask(id="T-1", title="A")])
        patch = apply_tasks_correction(
            {"verdict": "reject", "feedback": "nope", "tasks": [{"id": "T-new", "title": "X"}]},
            mem,
        )
        assert LIFECYCLE_MEMORY_NS not in patch


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

    def test_reject_writes_rejections_sidecar(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_plan_correction({"verdict": "reject", "feedback": "too shallow"}, mem)
        assert "rejections" in patch
        assert patch["rejections"]["confirm_plan"]["feedback"] == "too shallow"
        assert "plans" not in patch


# ---------------------------------------------------------------------------
# AssignmentConfirmedPayload + apply_assignment_confirmation (IMP-004 / T-305)
# ---------------------------------------------------------------------------


class TestAssignmentConfirmedPayload:
    def test_minimal_payload_validates(self) -> None:
        payload = AssignmentConfirmedPayload(assignee="alice")
        assert payload.assignee == "alice"
        assert payload.task_id is None

    def test_camel_case_alias_roundtrip(self) -> None:
        payload = AssignmentConfirmedPayload.model_validate(
            {"assignee": "alice", "taskId": "T-1"}
        )
        assert payload.task_id == "T-1"

    def test_empty_assignee_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload(assignee="")

    def test_whitespace_assignee_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload(assignee="   ")

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload.model_validate(
                {"assignee": "alice", "bogusField": "x"}
            )


class TestApplyAssignmentConfirmation:
    def test_explicit_task_id_wins(self) -> None:
        mem = _memory(current_task_id="T-2")
        patch = apply_assignment_confirmation(
            {"assignee": "alice", "taskId": "T-1"}, mem
        )
        assert patch == {"assignments": {"T-1": "alice"}}

    def test_fallback_to_current_task_id(self) -> None:
        mem = _memory(current_task_id="T-2")
        patch = apply_assignment_confirmation({"assignee": "alice"}, mem)
        assert patch == {"assignments": {"T-2": "alice"}}

    def test_no_resolvable_task_id_raises(self) -> None:
        mem = _memory()  # current_task_id=None
        with pytest.raises(ValueError, match="no resolvable task id"):
            apply_assignment_confirmation({"assignee": "alice"}, mem)

    def test_writes_top_level_sidecar_not_under_lifecycle_ns(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_assignment_confirmation({"assignee": "alice"}, mem)
        assert "assignments" in patch
        assert LIFECYCLE_MEMORY_NS not in patch

    def test_merge_preserves_prior_assignees(self) -> None:
        """Per-task loop-back must not stomp prior assignees."""
        mem = _memory(current_task_id="T-2")
        mem["assignments"] = {"T-1": "alice"}
        patch = apply_assignment_confirmation({"assignee": "bob"}, mem)
        assert patch["assignments"] == {"T-1": "alice", "T-2": "bob"}

    def test_builder_does_not_mutate_memory(self) -> None:
        mem = _memory(current_task_id="T-2")
        mem["assignments"] = {"T-1": "alice"}
        snapshot = copy.deepcopy(mem)
        apply_assignment_confirmation({"assignee": "bob"}, mem)
        assert mem == snapshot

    def test_existing_assignments_not_mapping_raises(self) -> None:
        mem = _memory(current_task_id="T-1")
        mem["assignments"] = "not-a-dict"
        with pytest.raises(ValueError, match="not a mapping"):
            apply_assignment_confirmation({"assignee": "alice"}, mem)

    def test_empty_assignee_rejected_via_builder(self) -> None:
        mem = _memory(current_task_id="T-1")
        with pytest.raises(ValidationError):
            apply_assignment_confirmation({"assignee": ""}, mem)


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


# ---------------------------------------------------------------------------
# apply_implementation_signal (FEAT-016)
# ---------------------------------------------------------------------------


class TestApplyImplementationSignal:
    def test_empty_payload_returns_empty_patch(self) -> None:
        mem = _memory(current_task_id="T-1")
        assert apply_implementation_signal({}, mem) == {}

    def test_pr_url_writes_implementation_refs_sidecar(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_implementation_signal({"prUrl": "https://github.com/org/repo/pull/42"}, mem)
        assert "implementation_refs" in patch
        assert LIFECYCLE_MEMORY_NS not in patch
        ref = patch["implementation_refs"]["T-1"]
        assert ref["prUrl"] == "https://github.com/org/repo/pull/42"
        assert ref["commitSha"] is None
        assert ref["summary"] is None

    def test_full_payload_persists_all_fields(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_implementation_signal(
            {"prUrl": "https://github.com/org/repo/pull/42", "commitSha": "abc123", "summary": "done"},
            mem,
        )
        ref = patch["implementation_refs"]["T-1"]
        assert ref["prUrl"] == "https://github.com/org/repo/pull/42"
        assert ref["commitSha"] == "abc123"
        assert ref["summary"] == "done"

    def test_snake_case_aliases_accepted(self) -> None:
        mem = _memory(current_task_id="T-1")
        patch = apply_implementation_signal(
            {"pr_url": "https://github.com/org/repo/pull/1", "commit_sha": "def456"}, mem
        )
        assert patch["implementation_refs"]["T-1"]["prUrl"] == "https://github.com/org/repo/pull/1"

    def test_multi_task_accumulates_without_overwriting(self) -> None:
        """Second task's entry merges alongside first."""
        mem = _memory(current_task_id="T-2")
        mem["implementation_refs"] = {"T-1": {"prUrl": "https://github.com/org/repo/pull/1", "commitSha": None, "summary": None}}
        patch = apply_implementation_signal({"prUrl": "https://github.com/org/repo/pull/2"}, mem)
        refs = patch["implementation_refs"]
        assert "T-1" in refs
        assert refs["T-1"]["prUrl"] == "https://github.com/org/repo/pull/1"
        assert refs["T-2"]["prUrl"] == "https://github.com/org/repo/pull/2"

    def test_no_current_task_id_returns_empty_patch(self) -> None:
        mem = _memory()  # current_task_id=None
        assert apply_implementation_signal({"prUrl": "https://github.com/org/repo/pull/1"}, mem) == {}

    def test_unknown_field_rejected_by_extra_forbid(self) -> None:
        mem = _memory(current_task_id="T-1")
        with pytest.raises(ValidationError):
            apply_implementation_signal({"prUrl": "https://x.com/pull/1", "bogus": "x"}, mem)

    def test_only_commit_sha_also_writes_refs(self) -> None:
        """Any non-null field triggers the write."""
        mem = _memory(current_task_id="T-1")
        patch = apply_implementation_signal({"commitSha": "abc"}, mem)
        assert patch["implementation_refs"]["T-1"]["commitSha"] == "abc"


# ---------------------------------------------------------------------------
# intake_for_human_review — implementationRef extension (FEAT-016)
# ---------------------------------------------------------------------------


class TestIntakeForHumanReview:
    def test_implementation_ref_none_when_absent(self) -> None:
        mem = _memory(current_task_id="T-1", tasks=[LifecycleTask(id="T-1", title="A")])
        intake = intake_for_human_review(mem)
        assert "implementationRef" in intake
        assert intake["implementationRef"] is None

    def test_implementation_ref_populated_when_present(self) -> None:
        mem = _memory(current_task_id="T-1", tasks=[LifecycleTask(id="T-1", title="A")])
        mem["implementation_refs"] = {
            "T-1": {"prUrl": "https://github.com/org/repo/pull/7", "commitSha": "abc", "summary": None}
        }
        intake = intake_for_human_review(mem)
        assert intake["implementationRef"]["prUrl"] == "https://github.com/org/repo/pull/7"

    def test_implementation_ref_for_other_task_not_included(self) -> None:
        """current_task_id=T-2 should not see T-1's ref."""
        mem = _memory(current_task_id="T-2", tasks=[LifecycleTask(id="T-2", title="B")])
        mem["implementation_refs"] = {
            "T-1": {"prUrl": "https://github.com/org/repo/pull/1", "commitSha": None, "summary": None}
        }
        intake = intake_for_human_review(mem)
        assert intake["implementationRef"] is None

    def test_existing_fields_still_present(self) -> None:
        mem = _memory(current_task_id="T-1", tasks=[LifecycleTask(id="T-1", title="A")])
        intake = intake_for_human_review(mem)
        assert "currentTask" in intake
        assert "planMarkdown" in intake
        assert "reviewHistory" in intake
