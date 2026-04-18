# Implementation Plan: T-096 — `wait_for_implementation` tool + `PauseForSignal` sentinel + runtime pause handling

## Task Reference
- **Task ID:** T-096
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-089

## Overview
The pause/resume contract. The `wait_for_implementation` tool returns a typed sentinel (`PauseForSignal`) instead of dispatching to the engine; the runtime loop, on seeing this sentinel, persists the step as `in_progress` with `engine_run_id IS NULL`, skips the engine call, and awaits `supervisor.await_signal` on a new (run_id, name, task_id) keyspace. Signal preload supported.

## Steps

### 1. Modify `src/app/modules/ai/runtime_helpers.py`
Add the sentinel:
```python
@dataclass(frozen=True)
class PauseForSignal:
    task_id: str
    name: str = "implementation-complete"
```

### 2. Create `src/app/modules/ai/tools/lifecycle/wait_for_implementation.py`
```python
TOOL_NAME = "wait_for_implementation"

async def handle(args, *, memory: LifecycleMemory) -> tuple[LifecycleMemory, PauseForSignal]:
    task_id = args["task_id"]
    match = next((t for t in memory.tasks if t.id == task_id), None)
    if match is None:
        raise PolicyError(f"unknown task: {task_id}")
    new_memory = memory.model_copy(update={"current_task_id": task_id})
    return new_memory, PauseForSignal(task_id=task_id)
```

Tool schema: `task_id` (required string).

### 3. Modify `src/app/modules/ai/supervisor.py`
Add per-key signal waits:
```python
class RunSupervisor:
    def __init__(self) -> None:
        ...existing...
        self._signal_events: dict[tuple[uuid.UUID, str, str], asyncio.Event] = {}
        self._signal_buffers: dict[tuple[uuid.UUID, str, str], dict[str, Any]] = {}

    async def await_signal(self, run_id, name, task_id) -> dict[str, Any]:
        key = (run_id, name, task_id)
        # Preload: signal already arrived?
        if key in self._signal_buffers:
            return self._signal_buffers.pop(key)
        event = self._signal_events.setdefault(key, asyncio.Event())
        await event.wait()
        return self._signal_buffers.pop(key, {})

    async def deliver_signal(self, run_id, name, task_id, payload) -> None:
        key = (run_id, name, task_id)
        self._signal_buffers[key] = payload
        event = self._signal_events.get(key)
        if event is not None:
            event.set()
```

### 4. Modify `src/app/modules/ai/runtime.py`
In the main iteration (after tool dispatch):
```python
tool_result = await tool_handler(args, memory=memory)
if isinstance(tool_result, tuple) and len(tool_result) == 2 and isinstance(tool_result[1], PauseForSignal):
    memory, pause = tool_result
    step = await _persist_pause_step(run_id, session_factory, node_name, memory, pause)
    await trace.record_step(StepDto.from_model(step))
    payload = await supervisor.await_signal(run_id, pause.name, pause.task_id)
    step = await _complete_pause_step(run_id, session_factory, step.id, payload)
    await trace.record_step(StepDto.from_model(step))
    continue
```

`_persist_pause_step` writes a `Step` with `engine_run_id=None`, `status=in_progress`. `_complete_pause_step` marks it `completed` with `node_result=payload`.

### 5. Modify `src/app/modules/ai/repository.py` / `service.py`
Relax any `assert step.engine_run_id is not None` validation. Add a service-layer comment near the NULL permission pointing at FEAT-005.

### 6. Tests
- `tests/modules/ai/test_supervisor.py` — extend with 5 cases (basic wait-then-deliver, preload-then-wait, two concurrent tasks with independent wakes, cancel during wait, idempotent re-deliver).
- `tests/modules/ai/tools/lifecycle/test_wait_for_implementation.py` — 3 cases (returns `PauseForSignal`, unknown task, sets `current_task_id`).
- `tests/integration/test_runtime_pause.py` — 1 case: stub-policy run hits pause, test code calls `supervisor.deliver_signal` directly, run advances.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/runtime_helpers.py` | Modify | Add `PauseForSignal` dataclass. |
| `src/app/modules/ai/tools/lifecycle/wait_for_implementation.py` | Create | Tool adapter. |
| `src/app/modules/ai/supervisor.py` | Modify | `await_signal` + `deliver_signal` + per-key state. |
| `src/app/modules/ai/runtime.py` | Modify | Pause/resume branch in iteration. |
| `src/app/modules/ai/repository.py` / `service.py` | Modify | Relax `engine_run_id NOT NULL` guard. |
| `tests/modules/ai/test_supervisor.py` | Modify | +5 cases. |
| `tests/modules/ai/tools/lifecycle/test_wait_for_implementation.py` | Create | 3 cases. |
| `tests/integration/test_runtime_pause.py` | Create | 1 integration case. |

## Edge Cases & Risks
- **Event re-entry**: `_signal_events.setdefault(key, ...)` ensures two concurrent `await_signal` calls on the same key share one event. If one waiter completes, the second call sees an empty buffer and re-awaits — that's a bug in v1 scope (only one loop awaits per run per task at a time). Add a sanity log if the second wait fires.
- **Cancellation propagation**: when `cancel_run` fires, the runtime's `except asyncio.CancelledError` path triggers; the `await_signal` gets cancelled through standard task cancellation. No special-casing needed.
- **Buffer leak**: if `await_signal` is cancelled, any preloaded payload in `_signal_buffers` stays. Add cleanup in the `cancel` path: pop any keys with this run_id on run termination (wire via `_on_task_done`).
- **Tool return-type polymorphism**: the runtime now inspects tool return values (`dict` vs. `tuple[memory, PauseForSignal]`). Keep this branch obvious with a type-narrowing helper function rather than inline `isinstance` scattered in the loop.
- **`engine_run_id NULL` drift**: update docstrings on `Step` model/DTO to mention lifecycle pause steps as the legitimate NULL case.

## Acceptance Verification
- [ ] `PauseForSignal` is a typed sentinel, not a magic string.
- [ ] Pause steps persist with `engine_run_id IS NULL`, `status=in_progress`.
- [ ] No outbound `/nodes/dispatch` call during pause (respx asserts zero).
- [ ] `await_signal` keyed on `(run_id, name, task_id)` — independent wakes per task.
- [ ] Preload works: `deliver_signal` before `await_signal` returns immediately.
- [ ] Cancellation during pause terminates cleanly as `cancelled`.
- [ ] 9 new tests pass (5 supervisor + 3 tool + 1 integration).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
