# Implementation Plan: T-089 — `LifecycleMemory` typed shape + trace serialization

## Task Reference
- **Task ID:** T-089
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Define the single typed shape of the lifecycle agent's per-run memory and its round-trip to/from `RunMemory.data` (JSONB). Everything downstream — tools, tests, trace assertions — reads this shape. Pydantic models with `extra="forbid"` so typos in tool writers fail fast.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/__init__.py`
Minimal module init:
```python
"""Lifecycle agent tools (FEAT-005)."""

from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

__all__ = ["LifecycleMemory"]
```

### 2. Create `src/app/modules/ai/tools/lifecycle/memory.py`
Define five Pydantic models with camelCase aliases + `extra="forbid"`:
```python
_FORBID_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="forbid")

class WorkItemRef(BaseModel):
    model_config = _FORBID_CAMEL
    id: str
    type: Literal["FEAT", "BUG", "IMP"]
    title: str
    path: str

class LifecycleTask(BaseModel):
    model_config = _FORBID_CAMEL
    id: str
    title: str
    executor: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    plan_path: str | None = None

class LifecycleReview(BaseModel):
    model_config = _FORBID_CAMEL
    task_id: str
    attempt: int
    verdict: Literal["pass", "fail"]
    feedback: str
    written_to: str

class LifecycleMemory(BaseModel):
    model_config = _FORBID_CAMEL
    work_item: WorkItemRef | None = None
    tasks: list[LifecycleTask] = Field(default_factory=list)
    current_task_id: str | None = None
    review_history: list[LifecycleReview] = Field(default_factory=list)
    files_touched_per_task: dict[str, list[str]] = Field(default_factory=dict)
    correction_attempts: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def empty(cls) -> LifecycleMemory:
        return cls()
```

Add two serializers:
```python
def from_run_memory(data: dict[str, Any]) -> LifecycleMemory:
    if not data:
        return LifecycleMemory.empty()
    return LifecycleMemory.model_validate(data)

def to_run_memory(memory: LifecycleMemory) -> dict[str, Any]:
    return memory.model_dump(mode="json", by_alias=True, exclude_none=False)
```

### 3. Create `tests/modules/ai/tools/lifecycle/__init__.py` + `tests/modules/ai/tools/lifecycle/test_memory.py`
Three cases:
1. `test_empty_round_trip` — `empty()` → `to_run_memory` → `from_run_memory` → equal.
2. `test_populated_round_trip` — build a fully-populated `LifecycleMemory`; assert `to_run_memory(from_run_memory(to_run_memory(m))) == to_run_memory(m)`.
3. `test_rejects_extra_fields` — `LifecycleMemory.model_validate({"unknown": 1})` raises `ValidationError`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/__init__.py` | Create | Package init exporting `LifecycleMemory`. |
| `src/app/modules/ai/tools/lifecycle/memory.py` | Create | 5 Pydantic models + 2 serializers. |
| `tests/modules/ai/tools/lifecycle/__init__.py` | Create | Empty package marker. |
| `tests/modules/ai/tools/lifecycle/test_memory.py` | Create | 3 round-trip tests. |

## Edge Cases & Risks
- **`extra="forbid"` vs. future field additions**: if a v2 adds a field to `LifecycleMemory`, old persisted rows loaded via `from_run_memory` will fail. Acceptable per AD-4 (memory is per-run-only — no persisted rows survive a run).
- **Literal enum drift**: `status` on `LifecycleTask` intentionally uses string literals distinct from `StepStatus`; do NOT collapse them even though values look similar.
- **Datetime fields**: none yet; if `LifecycleReview` grows a timestamp later, remember to handle ISO-8601 serialization explicitly (Pydantic v2 does this by default in `mode="json"`).
- **JSON key casing**: `to_run_memory` uses `by_alias=True` → camelCase in the DB. `from_run_memory` accepts both via `populate_by_name=True`. Verified by round-trip tests.

## Acceptance Verification
- [ ] 5 Pydantic models declared with `extra="forbid"`.
- [ ] `empty()` constructor returns a valid `LifecycleMemory` with safe defaults.
- [ ] `from_run_memory({}) == LifecycleMemory.empty()`.
- [ ] Round-trip test passes (populated).
- [ ] Unknown-field rejection test passes.
- [ ] `uv run pyright` clean — no `Any` leakage in the memory API's public surface.
