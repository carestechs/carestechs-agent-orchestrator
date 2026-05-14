# Implementation Plan: T-300 — Unit tests for the four memory-patch builders

## Task Reference
- **Task ID:** T-300
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** The builders are the load-bearing surface between operator signals and `RunMemory`. Coverage at the unit level isolates the patch logic from the executor + runtime + DB layers — fast feedback, exhaustive shape testing.

## Overview
Create `tests/modules/ai/executors/test_lifecycle_manual_patches.py`. For each of the four builders, cover (1) empty payload → empty patch, (2) full valid payload → expected nested patch shape, (3) malformed payload → Pydantic ValidationError, (4) builder-specific business rules (length-0 tasks, missing `current_task_id`, invalid verdict, etc.). No DB / session — pass `LifecycleMemory` model directly.

## Implementation Steps

### Step 1: Test module scaffold
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Create

```python
"""Unit tests for FEAT-015 memory-patch builders (T-297)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.ai.executors.lifecycle_manual_patches import (
    _apply_brief_correction,
    _apply_plan_correction,
    _apply_review_verdict,
    _apply_tasks_correction,
)
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    ReviewEntry,
    TaskItem,
    WorkItemRef,
    write_lifecycle_memory,
)


def _memory_with_work_item(*, ref: str = "FEAT-001", title: str = "Title", type_: str = "FEAT") -> dict:
    """Return a RunMemory.data dict with a populated lifecycle.v1.work_item."""
    return write_lifecycle_memory(
        LifecycleMemory(work_item=WorkItemRef(id=ref, type=type_, title=title, path=""))
    )


def _memory_with_current_task(task_id: str = "T-1") -> dict:
    return write_lifecycle_memory(
        LifecycleMemory(
            work_item=WorkItemRef(id="FEAT-001", type="FEAT", title="t", path=""),
            current_task_id=task_id,
        )
    )
```

### Step 2: `_apply_brief_correction` tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify (append)

```python
class TestApplyBriefCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        mem = _memory_with_work_item()
        assert _apply_brief_correction({}, mem) == {}

    def test_title_override_emits_full_work_item_patch(self) -> None:
        mem = _memory_with_work_item(title="Original")
        patch = _apply_brief_correction({"workItem": {"title": "Edited"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["work_item"]["title"] == "Edited"
        assert patch[LIFECYCLE_MEMORY_NS]["work_item"]["id"] == "FEAT-001"  # carried over

    def test_type_override_normalises_to_enum_value(self) -> None:
        mem = _memory_with_work_item(type_="FEAT")
        patch = _apply_brief_correction({"workItem": {"type": "BUG"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["work_item"]["type"] == "BUG"

    def test_invalid_type_raises_validation_error(self) -> None:
        mem = _memory_with_work_item()
        with pytest.raises(ValidationError):
            _apply_brief_correction({"workItem": {"type": "INVALID"}}, mem)

    def test_no_memory_workitem_raises(self) -> None:
        empty_memory = write_lifecycle_memory(LifecycleMemory())
        with pytest.raises(ValueError, match="not yet populated"):
            _apply_brief_correction({"workItem": {"title": "X"}}, empty_memory)

    def test_snake_case_alias_also_accepted(self) -> None:
        mem = _memory_with_work_item()
        # Pydantic populate_by_name=True allows the snake_case form too.
        patch = _apply_brief_correction({"work_item": {"title": "Snake"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["work_item"]["title"] == "Snake"
```

### Step 3: `_apply_tasks_correction` tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify (append)

```python
class TestApplyTasksCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        assert _apply_tasks_correction({}, {}) == {}

    def test_replacement_list_overwrites(self) -> None:
        payload = {"tasks": [
            {"id": "T-1", "title": "First"},
            {"id": "T-2", "title": "Second", "summary": "..."},
        ]}
        patch = _apply_tasks_correction(payload, {})
        assert len(patch[LIFECYCLE_MEMORY_NS]["tasks"]) == 2
        assert patch[LIFECYCLE_MEMORY_NS]["tasks"][0]["id"] == "T-1"
        assert patch[LIFECYCLE_MEMORY_NS]["tasks"][1]["summary"] == "..."

    def test_empty_list_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            _apply_tasks_correction({"tasks": []}, {})

    def test_missing_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            _apply_tasks_correction({"tasks": [{"title": "No ID"}]}, {})

    def test_missing_title_raises(self) -> None:
        with pytest.raises(ValidationError):
            _apply_tasks_correction({"tasks": [{"id": "T-1"}]}, {})
```

### Step 4: `_apply_plan_correction` tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify (append)

```python
class TestApplyPlanCorrection:
    def test_empty_payload_returns_empty_patch(self) -> None:
        mem = _memory_with_current_task()
        assert _apply_plan_correction({}, mem) == {}

    def test_plan_patches_current_task_plans(self) -> None:
        mem = _memory_with_current_task("T-7")
        patch = _apply_plan_correction({"plan": "# Updated"}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["taskPlans"]["T-7"] == "# Updated"

    def test_no_current_task_id_raises(self) -> None:
        no_task = write_lifecycle_memory(
            LifecycleMemory(work_item=WorkItemRef(id="FEAT-1", type="FEAT", title="t", path=""))
        )
        with pytest.raises(ValueError, match="no current_task_id"):
            _apply_plan_correction({"plan": "X"}, no_task)
```

### Step 5: `_apply_review_verdict` tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify (append)

```python
class TestApplyReviewVerdict:
    def test_pass_verdict_appends_human_entry(self) -> None:
        mem = _memory_with_current_task("T-1")
        patch = _apply_review_verdict({"verdict": "pass"}, mem)
        history = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"]
        assert len(history) == 1
        assert history[0]["verdict"] == "pass"
        assert history[0]["reviewer"] == "human"
        assert history[0]["taskId"] == "T-1"
        assert history[0]["feedback"] is None
        assert history[0]["attempt"] == 1

    def test_fail_with_feedback(self) -> None:
        mem = _memory_with_current_task("T-2")
        patch = _apply_review_verdict(
            {"verdict": "fail", "feedback": "needs more tests"}, mem
        )
        entry = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"][-1]
        assert entry["verdict"] == "fail"
        assert entry["feedback"] == "needs more tests"

    def test_attempt_increments_on_second_review_for_same_task(self) -> None:
        mem_data = write_lifecycle_memory(LifecycleMemory(
            work_item=WorkItemRef(id="FEAT-1", type="FEAT", title="t", path=""),
            current_task_id="T-1",
            review_history=[
                ReviewEntry(task_id="T-1", verdict="fail", feedback="first", attempt=1,
                            reviewed_at=datetime.now(UTC), reviewer="human"),
            ],
        ))
        patch = _apply_review_verdict({"verdict": "pass"}, mem_data)
        history = patch[LIFECYCLE_MEMORY_NS]["reviewHistory"]
        assert len(history) == 2  # appended, not replaced
        assert history[-1]["attempt"] == 2

    def test_invalid_verdict_raises(self) -> None:
        mem = _memory_with_current_task()
        with pytest.raises(ValidationError):
            _apply_review_verdict({"verdict": "needs_changes"}, mem)

    def test_missing_verdict_raises(self) -> None:
        mem = _memory_with_current_task()
        with pytest.raises(ValidationError):
            _apply_review_verdict({}, mem)

    def test_no_current_task_id_raises(self) -> None:
        no_task = write_lifecycle_memory(LifecycleMemory(
            work_item=WorkItemRef(id="FEAT-1", type="FEAT", title="t", path="")
        ))
        with pytest.raises(ValueError, match="no current_task_id"):
            _apply_review_verdict({"verdict": "pass"}, no_task)
```

### Step 6: Run the suite
**File:** N/A
**Action:** Verify

```bash
uv run pytest tests/modules/ai/executors/test_lifecycle_manual_patches.py -v
# Expected: 18+ tests pass
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/executors/test_lifecycle_manual_patches.py` | Create | 4 test classes, ~18-20 tests, pure-function coverage. |

## Edge Cases & Risks
- **`LifecycleMemory` / `ReviewEntry` / `TaskItem` import paths.** Confirm the actual class names in `src/app/modules/ai/tools/lifecycle/memory.py` before pasting. Adjust if the model names differ (e.g. `Task` vs `TaskItem`, `Review` vs `ReviewEntry`).
- **`write_lifecycle_memory` shape.** Some helpers prefer building the dict directly; if `write_lifecycle_memory` doesn't exist, build the dict literal: `{"lifecycle.v1": {"workItem": {...}}}` and pass that as `current_memory`.
- **Camel vs snake in patches.** The test assertions use camelCase keys (`taskPlans`, `reviewHistory`, `taskId`) — matching the project convention for JSON aliases. If the actual `LIFECYCLE_MEMORY_NS` data uses snake_case, switch the assertions.
- **`ValueError` vs `ValidationError`.** Pydantic raises `ValidationError`; the builders re-raise as `ValueError` for business rules (empty memory, missing current_task_id). Tests must distinguish — `pytest.raises(ValidationError)` vs `pytest.raises(ValueError)`.

## Acceptance Verification
- [ ] AC-1 — All four builders covered: empty payload, valid payload, malformed payload, business-rule violation.
- [ ] AC-2 — At least one test per builder asserts the exact `__memory_patch` shape it returns.
- [ ] AC-3 — `_apply_review_verdict` tests pin the `reviewer: "human"` discriminator and the `attempt` increment.
- [ ] AC-4 — All tests pass on real Postgres-less environment (these are pure-function tests; no DB fixture needed).
- [ ] AC-5 — `pyright` clean.
