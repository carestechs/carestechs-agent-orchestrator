# Implementation Plan: T-308 — Unit tests for `AssignmentConfirmedPayload` + `apply_assignment_confirmation`

## Task Reference
- **Task ID:** T-308
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** The builder is a pure function — high-leverage unit tests at this layer mean the integration test in T-309 only has to exercise wiring, not shape semantics.

## Overview
Append a test class to `tests/modules/ai/executors/test_lifecycle_manual_patches.py` covering schema validation, builder happy paths, builder error paths, and merge-preserve semantics. Pure-function tests — no DB, no LLM, no engine, no async.

## Implementation Steps

### Step 1: Locate the existing test file and conventions
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Read

Inspect the file's existing test classes for `BriefConfirmedPayload`, `TasksConfirmedPayload`, `PlanConfirmedPayload`, `ReviewCompletedPayload`. Match their fixture pattern (likely a small `_make_memory` helper that constructs a minimal `lifecycle.v1` blob) and naming (e.g. `class TestPlanCorrectionBuilder:`).

If the file uses `pytest` plain functions instead of classes, follow that style.

### Step 2: Add schema validation tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify

Append (or in matching style):

```python
class TestAssignmentConfirmedPayload:
    def test_minimal_payload_validates(self) -> None:
        payload = AssignmentConfirmedPayload(assignee="alice")
        assert payload.assignee == "alice"
        assert payload.task_id is None

    def test_camelcase_alias_roundtrip(self) -> None:
        payload = AssignmentConfirmedPayload.model_validate(
            {"assignee": "alice", "taskId": "t-1"}
        )
        assert payload.task_id == "t-1"

    def test_empty_assignee_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload(assignee="")

    def test_whitespace_assignee_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload(assignee="   ")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AssignmentConfirmedPayload.model_validate(
                {"assignee": "alice", "bogusField": "x"}
            )
```

### Step 3: Add builder happy-path tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify

```python
class TestApplyAssignmentConfirmation:
    @staticmethod
    def _memory_with(current_task_id: str | None, assignments: dict | None = None) -> dict:
        mem: dict = {
            "lifecycle.v1": {
                "current_task_id": current_task_id,
                "tasks": [],
                "work_item": None,
            }
        }
        if assignments is not None:
            mem["assignments"] = assignments
        return mem

    def test_explicit_task_id_wins(self) -> None:
        mem = self._memory_with(current_task_id="t-2")
        patch = apply_assignment_confirmation(
            {"assignee": "alice", "taskId": "t-1"},
            lifecycle_memory=mem,
        )
        assert patch == {"assignments": {"t-1": "alice"}}

    def test_fallback_to_current_task_id(self) -> None:
        mem = self._memory_with(current_task_id="t-2")
        patch = apply_assignment_confirmation(
            {"assignee": "alice"},
            lifecycle_memory=mem,
        )
        assert patch == {"assignments": {"t-2": "alice"}}

    def test_no_resolvable_task_id_raises(self) -> None:
        mem = self._memory_with(current_task_id=None)
        with pytest.raises(ValueError, match="no resolvable task id"):
            apply_assignment_confirmation(
                {"assignee": "alice"},
                lifecycle_memory=mem,
            )
```

### Step 4: Add merge-preserve and immutability tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify

```python
    def test_merge_preserves_prior_assignees(self) -> None:
        mem = self._memory_with(
            current_task_id="t-2",
            assignments={"t-1": "alice"},
        )
        patch = apply_assignment_confirmation(
            {"assignee": "bob"},
            lifecycle_memory=mem,
        )
        assert patch == {"assignments": {"t-1": "alice", "t-2": "bob"}}

    def test_builder_does_not_mutate_memory(self) -> None:
        mem = self._memory_with(
            current_task_id="t-2",
            assignments={"t-1": "alice"},
        )
        snapshot = copy.deepcopy(mem)
        apply_assignment_confirmation(
            {"assignee": "bob"},
            lifecycle_memory=mem,
        )
        assert mem == snapshot

    def test_existing_assignments_not_mapping_raises(self) -> None:
        mem = self._memory_with(current_task_id="t-1")
        mem["assignments"] = "not-a-dict"
        with pytest.raises(ValueError, match="not a mapping"):
            apply_assignment_confirmation(
                {"assignee": "alice"},
                lifecycle_memory=mem,
            )
```

### Step 5: Run and verify
**File:** N/A
**Action:** Run

```bash
uv run pytest tests/modules/ai/executors/test_lifecycle_manual_patches.py -v
```

All ten new test functions should pass. If the existing test file uses classes, the new tests integrate as additional classes; if it uses module-level functions, restructure accordingly.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/executors/test_lifecycle_manual_patches.py` | Modify | Append ~10 test functions covering schema + builder. |

## Edge Cases & Risks
- **Test isolation:** the builder is pure, so no fixture cleanup is needed. `_memory_with` returns a fresh dict per call.
- **Existing test file may live at a slightly different path:** if the unit tests for the four sibling builders are split across multiple files or in a different directory, follow the convention of the existing file structure. Search for `apply_review_verdict` test references to locate the canonical file before writing.
- **Imports:** ensure `import copy` is present; `ValidationError` from `pydantic`; `AssignmentConfirmedPayload` and `apply_assignment_confirmation` from `app.modules.ai.executors.lifecycle_manual_patches`.
- **`read_lifecycle_memory` test data:** the helper at the module level expects `current_task_id` to be a known field in the `lifecycle.v1` namespace. If `LifecycleMemory` requires more fields to validate (e.g. `tasks: list` rather than empty), the `_memory_with` helper must supply them. Inspect `read_lifecycle_memory` and `LifecycleMemory` in `tools/lifecycle/memory.py` to confirm the minimal valid shape.

## Acceptance Verification
- [ ] All ten test functions pass.
- [ ] `uv run pytest tests/modules/ai/executors/` passes.
- [ ] Tests cover: minimal payload, alias roundtrip, empty/whitespace rejection, extra-field rejection, explicit-taskId override, fallback to `current_task_id`, missing-taskId error, merge-preserve, immutability, malformed existing `assignments`.
- [ ] No real LLM, no DB, no engine calls.
- [ ] `pyright` clean.
