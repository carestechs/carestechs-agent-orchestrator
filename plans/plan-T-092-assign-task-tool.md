# Implementation Plan: T-092 — `assign_task` tool

## Task Reference
- **Task ID:** T-092
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-089

## Overview
Deterministic-v1 tool that records an executor assignment for one task. The stage exists in the flow so the trace shows it explicitly; the logic is hardcoded (every task gets `local-claude-code`). Idempotent — re-entering on a loop-back from `corrections` is a no-op.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/assign_task.py`
```python
TOOL_NAME = "assign_task"
DEFAULT_EXECUTOR = "local-claude-code"

def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description="Assign an executor to a task. v1: always 'local-claude-code'.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    )

async def handle(args, *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id = args["task_id"]
    tasks = list(memory.tasks)
    for i, t in enumerate(tasks):
        if t.id == task_id:
            tasks[i] = t.model_copy(update={"executor": DEFAULT_EXECUTOR})
            return memory.model_copy(update={"tasks": tasks})
    raise PolicyError(f"unknown task: {task_id}")
```

### 2. Create `tests/modules/ai/tools/lifecycle/test_assign_task.py`
Three cases:
- `test_assign_happy` — `tasks` has `T-001`; call assigns executor; return includes updated task.
- `test_assign_unknown_task` — `T-999` not in list → `PolicyError("unknown task: T-999")`.
- `test_reassign_idempotent` — re-call on an already-assigned task returns memory with the same executor; no state corruption (this is the load-bearing case for the loop-back from `corrections`).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/assign_task.py` | Create | Tool adapter. |
| `tests/modules/ai/tools/lifecycle/test_assign_task.py` | Create | 3 unit tests. |

## Edge Cases & Risks
- **Intentional minimalism**: no service-layer helper; this is 10 lines. Resist the urge to build a pluggable executor-routing scaffold — FEAT-006 will add it.
- **`model_copy` on lists**: Pydantic's `model_copy(update={"tasks": tasks})` replaces the list wholesale. Don't mutate `memory.tasks` in place — Pydantic v2's immutable semantics expect fresh objects.
- **Ordering preservation**: building `tasks` with the comprehension preserves input order, which is required by the `plan_creation` node (it iterates tasks in declared order).

## Acceptance Verification
- [ ] `memory.tasks[<match>].executor == "local-claude-code"` after `handle`.
- [ ] Unknown `task_id` → `PolicyError`.
- [ ] Re-assignment is a no-op (state unchanged beyond the executor field).
- [ ] Tool schema advertises one parameter.
- [ ] 3 unit tests pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
