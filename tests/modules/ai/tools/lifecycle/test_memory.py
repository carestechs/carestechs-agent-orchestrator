"""Tests for LifecycleMemory round-trip serialization (FEAT-005 / T-089)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ai.tools.lifecycle.memory import (
    LifecycleMemory,
    LifecycleReview,
    LifecycleTask,
    WorkItemRef,
    from_run_memory,
    to_run_memory,
)


class TestRoundTrip:
    def test_empty_round_trip(self) -> None:
        empty = LifecycleMemory.empty()
        dumped = to_run_memory(empty)
        hydrated = from_run_memory(dumped)
        assert hydrated == empty

    def test_from_empty_dict_yields_empty_memory(self) -> None:
        assert from_run_memory({}) == LifecycleMemory.empty()

    def test_populated_round_trip(self) -> None:
        memory = LifecycleMemory(
            work_item=WorkItemRef(
                id="IMP-001",
                type="IMP",
                title="Proof target",
                path="docs/work-items/IMP-001.md",
            ),
            tasks=[
                LifecycleTask(id="T-001", title="One", executor="local-claude-code"),
                LifecycleTask(
                    id="T-002",
                    title="Two",
                    executor="local-claude-code",
                    status="in_progress",
                    plan_path="plans/plan-T-002-two.md",
                ),
            ],
            current_task_id="T-002",
            review_history=[
                LifecycleReview(
                    task_id="T-001",
                    attempt=1,
                    verdict="pass",
                    feedback="ok",
                    written_to="plans/plan-T-001-one-review-1.md",
                ),
            ],
            files_touched_per_task={"T-001": ["README.md"]},
            correction_attempts={"T-001": 0, "T-002": 1},
        )
        dumped = to_run_memory(memory)
        rehydrated_dump = to_run_memory(from_run_memory(dumped))
        assert rehydrated_dump == dumped


class TestExtraFieldsForbidden:
    def test_rejects_extra_top_level_field(self) -> None:
        with pytest.raises(ValidationError):
            LifecycleMemory.model_validate({"unknown": 1})

    def test_rejects_extra_nested_field(self) -> None:
        with pytest.raises(ValidationError):
            WorkItemRef.model_validate(
                {
                    "id": "IMP-001",
                    "type": "IMP",
                    "title": "x",
                    "path": "p",
                    "extra": "nope",
                }
            )

    def test_rejects_invalid_work_item_type(self) -> None:
        with pytest.raises(ValidationError):
            WorkItemRef.model_validate(
                {"id": "DOC-001", "type": "DOC", "title": "x", "path": "p"},
            )


class TestCamelCaseAliases:
    def test_dump_uses_camel_case(self) -> None:
        memory = LifecycleMemory(
            work_item=WorkItemRef(id="T", type="FEAT", title="t", path="p"),
            current_task_id="T-1",
            correction_attempts={"T-1": 2},
        )
        dumped = to_run_memory(memory)
        assert "workItem" in dumped
        assert "currentTaskId" in dumped
        assert "correctionAttempts" in dumped
        assert "filesTouchedPerTask" in dumped
        assert "reviewHistory" in dumped
