"""Tests for FEAT-017 mockup flow additions.

Covers:
- ``current_task_is_mockup`` predicate (T-002)
- ``_patch_generate_mockup`` via ``apply_mockup_approval`` (T-003)
- ``intake_for_confirm_mockup`` intake builder (T-004)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.modules.ai.executors.lifecycle_manual_patches import (
    MockupApprovedPayload,
    apply_mockup_approval,
    intake_for_confirm_mockup,
)
from app.modules.ai.flow_predicates import get as get_predicate
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleTask,
    write_lifecycle_memory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_memory(
    tasks: list[LifecycleTask],
    current_task_id: str | None = None,
    mockups: dict[str, Any] | None = None,
    rejections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mem = LifecycleMemory(tasks=tasks, current_task_id=current_task_id)
    data: dict[str, Any] = {LIFECYCLE_MEMORY_NS: write_lifecycle_memory(mem)[LIFECYCLE_MEMORY_NS]}
    if mockups is not None:
        data["mockups"] = mockups
    if rejections is not None:
        data["rejections"] = rejections
    return data


def _task(task_id: str, kind: str = "feature") -> LifecycleTask:
    return LifecycleTask(id=task_id, title=f"Task {task_id}", kind=kind)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# current_task_is_mockup predicate
# ---------------------------------------------------------------------------


_current_task_is_mockup = get_predicate("current_task_is_mockup")


class TestCurrentTaskIsMockup:

    def test_returns_true_for_mockup_kind(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id="T-001",
        )
        assert _current_task_is_mockup(memory, None) is True

    def test_returns_false_for_feature_kind(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "feature")],
            current_task_id="T-001",
        )
        assert _current_task_is_mockup(memory, None) is False

    def test_returns_false_for_bug_kind(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "bug")],
            current_task_id="T-001",
        )
        assert _current_task_is_mockup(memory, None) is False

    def test_returns_false_for_chore_kind(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "chore")],
            current_task_id="T-001",
        )
        assert _current_task_is_mockup(memory, None) is False

    def test_returns_false_when_no_current_task_id(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id=None,
        )
        assert _current_task_is_mockup(memory, None) is False

    def test_returns_false_when_task_not_found(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id="T-999",
        )
        assert _current_task_is_mockup(memory, None) is False

    def test_returns_false_on_empty_task_list(self) -> None:
        memory = _make_memory(tasks=[], current_task_id="T-001")
        assert _current_task_is_mockup(memory, None) is False

    def test_routes_correctly_in_multi_task_memory(self) -> None:
        """With mixed kinds, only the current task's kind is checked."""
        memory = _make_memory(
            tasks=[_task("T-001", "feature"), _task("T-002", "mockup")],
            current_task_id="T-002",
        )
        assert _current_task_is_mockup(memory, None) is True

        memory2 = _make_memory(
            tasks=[_task("T-001", "feature"), _task("T-002", "mockup")],
            current_task_id="T-001",
        )
        assert _current_task_is_mockup(memory2, None) is False


# ---------------------------------------------------------------------------
# apply_mockup_approval
# ---------------------------------------------------------------------------


class TestApplyMockupApproval:
    def test_approve_returns_empty_patch(self) -> None:
        memory = _make_memory(tasks=[_task("T-001", "mockup")], current_task_id="T-001")
        patch = apply_mockup_approval({"verdict": "approve"}, memory)
        assert patch == {}

    def test_approve_with_verdict_field(self) -> None:
        memory = _make_memory(tasks=[], current_task_id=None)
        patch = apply_mockup_approval({"verdict": "approve"}, memory)
        assert patch == {}

    def test_reject_writes_rejection_sidecar(self) -> None:
        memory = _make_memory(tasks=[], current_task_id=None)
        patch = apply_mockup_approval(
            {"verdict": "reject", "feedback": "layout too cluttered"}, memory
        )
        assert "rejections" in patch
        entry = patch["rejections"]["confirm_mockup"]
        assert entry["feedback"] == "layout too cluttered"
        assert entry["attempt"] == 1

    def test_reject_increments_attempt_counter(self) -> None:
        memory = _make_memory(
            tasks=[],
            current_task_id=None,
            rejections={"confirm_mockup": {"feedback": "first rejection", "attempt": 1}},
        )
        patch = apply_mockup_approval(
            {"verdict": "reject", "feedback": "still cluttered"}, memory
        )
        assert patch["rejections"]["confirm_mockup"]["attempt"] == 2

    def test_reject_preserves_other_rejection_keys(self) -> None:
        memory = _make_memory(
            tasks=[],
            current_task_id=None,
            rejections={"confirm_plan": {"feedback": "bad plan", "attempt": 1}},
        )
        patch = apply_mockup_approval({"verdict": "reject", "feedback": "fix it"}, memory)
        rejections = patch["rejections"]
        assert "confirm_plan" in rejections
        assert "confirm_mockup" in rejections

    def test_invalid_payload_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            apply_mockup_approval({"verdict": "unknown"}, {})


# ---------------------------------------------------------------------------
# intake_for_confirm_mockup
# ---------------------------------------------------------------------------


class TestIntakeForConfirmMockup:
    def test_surfaces_mockup_html_and_description(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id="T-001",
            mockups={
                "T-001": {
                    "mockup_html": "<html>...</html>",
                    "description": "Login screen",
                }
            },
        )
        intake = intake_for_confirm_mockup(memory)
        assert intake["mockupHtml"] == "<html>...</html>"
        assert intake["mockupDescription"] == "Login screen"

    def test_surfaces_current_task(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id="T-001",
            mockups={"T-001": {"mockup_html": "<html/>", "description": "x"}},
        )
        intake = intake_for_confirm_mockup(memory)
        assert intake["currentTask"] is not None
        assert intake["currentTask"]["id"] == "T-001"

    def test_degrades_gracefully_when_no_mockup_sidecar(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "mockup")],
            current_task_id="T-001",
        )
        intake = intake_for_confirm_mockup(memory)
        assert intake["mockupHtml"] == ""
        assert intake["mockupDescription"] == ""

    def test_degrades_gracefully_when_task_not_found(self) -> None:
        memory = _make_memory(tasks=[], current_task_id="T-999")
        intake = intake_for_confirm_mockup(memory)
        assert intake["currentTask"] is None
        assert intake["mockupHtml"] == ""
        assert intake["mockupDescription"] == ""

    def test_returns_correct_task_from_multi_task_memory(self) -> None:
        memory = _make_memory(
            tasks=[_task("T-001", "feature"), _task("T-002", "mockup")],
            current_task_id="T-002",
            mockups={"T-002": {"mockup_html": "<p/>", "description": "Dashboard"}},
        )
        intake = intake_for_confirm_mockup(memory)
        assert intake["currentTask"]["id"] == "T-002"
        assert intake["mockupDescription"] == "Dashboard"


# ---------------------------------------------------------------------------
# LifecycleTask.kind backward compatibility
# ---------------------------------------------------------------------------


class TestLifecycleTaskKindDefault:
    def test_kind_defaults_to_feature(self) -> None:
        task = LifecycleTask(id="T-001", title="test")
        assert task.kind == "feature"

    def test_kind_roundtrips_through_json(self) -> None:
        task = LifecycleTask(id="T-001", title="test", kind="mockup")  # type: ignore[arg-type]
        dumped = task.model_dump(mode="json")
        restored = LifecycleTask.model_validate(dumped)
        assert restored.kind == "mockup"

    def test_old_memory_without_kind_deserializes(self) -> None:
        """Rows written before FEAT-017 have no ``kind`` field — must use default."""
        raw: dict[str, Any] = {"id": "T-001", "title": "old task"}
        task = LifecycleTask.model_validate(raw)
        assert task.kind == "feature"
