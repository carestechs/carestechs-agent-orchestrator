# Implementation Plan: T-283 — `RunIntakeWorkItem` Pydantic DTO + `RunIntake` integration

## Task Reference
- **Task ID:** T-283
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-1..AC-4, AC-7, AC-9 — every downstream task (service, route, executor swap) imports this model. Lands in parallel with T-282 because it has no DB dependency.

## Overview
Add a typed sub-object to the run-start request body so registration is real DTO access at the boundary, not `dict[str, Any]` lookups. Defines `RunIntakeWorkItem` (id, kind, optional content) and wires it into the existing run-start request DTO. Backward-compat keys (`workItemPath` / `workItemId` / `workItemEngineId`) remain present and untouched — they're handled in T-288.

## Implementation Steps

### Step 1: Add `RunIntakeWorkItem` to schemas
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify
After the existing run-start request DTO, add:
```python
class RunIntakeWorkItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    id: str = Field(..., min_length=3, max_length=64)
    kind: WorkItemType
    content: str | None = Field(default=None)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        import re
        if not re.match(r"^[A-Z]+-\d+(-[a-z0-9-]+)?$", v):
            raise ValueError("id must match ^[A-Z]+-\\d+(-[a-z0-9-]+)?$")
        return v
```
Follow CLAUDE.md: camelCase JSON aliases (via `to_camel`), snake_case Python attribute names, `WorkItemType` enum already exists in `models.py` — re-export through `schemas.py` if not already imported.

### Step 2: Integrate into the run-start request DTO
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify
Find the existing run-start request DTO (`RunStartRequestDto` or similar — confirm by reading the file). It currently exposes `intake: dict[str, Any]`. Replace with:
```python
class RunIntake(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="allow")
    work_item: RunIntakeWorkItem | None = None
    # All other current keys remain accessible via .model_extra (workItemPath, etc.)
```
`extra="allow"` keeps backward-compat: callers that still send `workItemPath` see it on `model_extra` without a validation error. The route/service can read it via `intake.model_extra.get("workItemPath")` in T-288.

Update the run-start request DTO to use `intake: RunIntake`.

### Step 3: Unit tests
**File:** `tests/modules/ai/test_run_intake_schema.py`
**Action:** Create
Tests:
1. `test_valid_shape_parses` — `{"id": "FEAT-100", "kind": "FEAT", "content": "..."}` parses cleanly.
2. `test_id_pattern_rejects_lowercase` — `"feat-100"` raises `ValidationError`.
3. `test_id_pattern_rejects_no_number` — `"FEAT-abc"` raises.
4. `test_kind_must_be_enum_value` — `"WIDGET"` raises.
5. `test_oversized_content_passes_at_model_level` — content >1 MB parses (hard cap is at the route in T-285).
6. `test_extras_pass_through_on_runintake` — `{"workItemPath": "x"}` accessible via `model_extra`.

Use `pytest-asyncio` only if needed (these are sync model tests). Per CLAUDE.md, no `dict | Any` in test assertions — assert on typed attributes.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Add `RunIntakeWorkItem`, `RunIntake`; wire into run-start DTO |
| `tests/modules/ai/test_run_intake_schema.py` | Create | Six unit tests covering the model boundary |

## Edge Cases & Risks
- **Pydantic v2 `model_extra` behavior.** Confirm with a test that `RunIntake(model_config: extra="allow")` exposes unknown keys via `model_extra`. If the rest of the codebase relies on `intake.workItemPath` as a direct attribute, T-288's compat shim must read from `model_extra` instead.
- **`id` regex strictness.** Existing work-items in `docs/work-items/` all match `[A-Z]+-\d+(-[a-z0-9-]+)?$` — verified by `ls`. If future kinds use a different prefix scheme, the regex grows. Worth a docstring note.
- **`kind` enum drift.** `WorkItemType` is the source of truth (`FEAT | BUG | IMP`). The Pydantic enum coercion is case-sensitive — clients must send uppercase.

## Acceptance Verification
- [ ] `RunIntakeWorkItem` defined with regex on `id`, length validator on `content` (soft, not hard).
- [ ] `RunStartRequestDto.intake.workItem` resolves as `RunIntakeWorkItem | None`.
- [ ] camelCase JSON aliases verified by a serialization round-trip test (input `"id"`, `"kind"`, `"content"`; output matches).
- [ ] Six unit tests pass under `uv run pytest tests/modules/ai/test_run_intake_schema.py`.
- [ ] Pyright strict mode clean.
