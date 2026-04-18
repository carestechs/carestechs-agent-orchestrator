# Implementation Plan: T-037 — Run-loop supervisor (async task registry)

## Task Reference
- **Task ID:** T-037
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-035

## Overview
In-process registry holding an `asyncio.Task` + wake-up `Event` per run. The loop awaits `wake()` between dispatches; webhooks wake it. Also the cancellation choke-point (AC-4) and the "graceful shutdown" handler's partner.

## Steps

### 1. Create `src/app/modules/ai/supervisor.py`
- `from __future__ import annotations`, stdlib imports.
- Internal dataclass:
  ```python
  @dataclass
  class _SupervisedRun:
      run_id: uuid.UUID
      task: asyncio.Task[None]
      wake_event: asyncio.Event
      cancel_requested: bool = False
  ```
- `class RunSupervisor:`
  - `__init__`: `self._runs: dict[uuid.UUID, _SupervisedRun] = {}`, `self._lock = asyncio.Lock()`.
  - `def spawn(self, run_id: uuid.UUID, coro_factory: Callable[[asyncio.Event], Awaitable[None]]) -> asyncio.Task[None]`: creates the `Event`, calls `coro_factory(event)`, wraps in `asyncio.create_task`, registers. Task done-callback removes from `_runs` and logs any uncaught exception at `ERROR`.
  - `async def wake(self, run_id)`: under lock, if registered, `event.set()`; the loop is responsible for `.clear()` after observing.
  - `async def cancel(self, run_id)`: mark `cancel_requested=True`, `task.cancel()`, `wake()` so the coro can observe the cancel flag even if not awaiting.
  - `async def await_wake(self, run_id)`: looks up the Event; returns `await event.wait()`; caller calls `event.clear()` next iteration.
  - `async def shutdown(self, grace: float = 5.0)`: `await asyncio.wait([r.task for r in self._runs.values()], timeout=grace)`; any still-running tasks are `.cancel()`'d and re-awaited with `return_exceptions=True`.
  - Provide `is_cancelled(run_id) -> bool` accessor for the loop.

### 2. Modify `src/app/core/dependencies.py`
- Add module-level `_supervisor: RunSupervisor | None = None`.
- `def get_supervisor() -> RunSupervisor`: lazy singleton; created once per process. App lifespan (T-045) will rebind explicitly.
- Expose as FastAPI dep.

### 3. Create `tests/modules/ai/test_supervisor.py`
- `spawn` runs the coro; return happens after task is scheduled (`await asyncio.sleep(0)`).
- `wake` timing: `event.set()` observed by awaiter within 10 ms (use `time.perf_counter`).
- `cancel` timing: coro exits within 50 ms via `CancelledError`.
- Shutdown cancels a long-running task and completes within grace.
- Exception in supervised coro is logged (via `caplog`) but supervisor remains responsive to new `spawn` calls.
- After task completes, `_runs` no longer contains the entry.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/supervisor.py` | Create | `RunSupervisor` with spawn/wake/cancel/shutdown. |
| `src/app/core/dependencies.py` | Modify | `get_supervisor` dep. |
| `tests/modules/ai/test_supervisor.py` | Create | Spawn/wake/cancel/shutdown/exception tests. |

## Edge Cases & Risks
- **Wake-up loss**: if a webhook `set()`s the event before the loop enters `.wait()`, the loop must still see it. `asyncio.Event` semantics handle this correctly — `wait()` returns immediately if already set. Documented in `await_wake` docstring. Add an explicit test.
- Timing assertions (10 ms / 50 ms) may flake on loaded CI. Use generous bounds with a tighter assertion noted in the test docstring.
- Single-worker constraint (`--workers 1`): document in `CLAUDE.md` (T-061). Multiple uvicorn workers duplicate the supervisor → duplicated spawns.

## Acceptance Verification
- [ ] Wake-up < 10 ms (or documented generous CI bound).
- [ ] Cancel < 50 ms.
- [ ] Supervisor survives a failing supervised coro.
- [ ] `shutdown` drains all tasks within grace + grace.
- [ ] Documented wake-up-loss invariant verified by test.
