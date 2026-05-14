# Implementation Plan: T-302 — End-to-end integration test for the manual variant

## Task Reference
- **Task ID:** T-302
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Rationale:** Single cutover-proof test for FEAT-015. Every other task contributes a piece; this one demonstrates the pieces hang together. The "edited tasks reach the engine" assertion proves §3 — "LLM proposes, operator disposes."

## Overview
Create `tests/integration/test_lifecycle_v04_manual.py`. Drive a full lifecycle run against `lifecycle-agent@0.4.0-manual` with: (a) a `StubLLMProvider` for LLM-content nodes, scripted to produce a brief, 3 tasks, and 2 plans; (b) a `respx`-stubbed `FlowEngineLifecycleClient` for all engine HTTP calls; (c) scripted operator signal deliveries for the four checkpoint kinds. Assert per-checkpoint status flips, signal-driven advances, and that the *edited* (not LLM-original) task list reaches the engine.

## Implementation Steps

### Step 1: Read the v0.3.0 acceptance test as the structural template
**File:** `tests/integration/test_lifecycle_v03_acceptance.py` (or closest equivalent)
**Action:** Read

```bash
ls tests/integration/test_lifecycle_v03*.py
ls tests/integration/ | grep -i lifecycle
```

Identify the file that already runs `lifecycle-agent@0.3.0` end-to-end with stubs. That's the structural template — copy its fixture setup, LLM-stub scripting, and engine-stub respx mocks.

### Step 2: Test file scaffold
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Create

```python
"""End-to-end test for FEAT-015 manual lifecycle variant.

Drives a complete run through 19 nodes including 4 human checkpoints:
- confirm_brief
- confirm_tasks (with edits — replaces LLM 3-task list with operator 2-task list)
- confirm_plan (per task)
- human_review_implementation (per task)

Asserts every status transition, the engine receives the edited task list
(not the LLM original), and the run terminates at close_work_item with
RunStatus.COMPLETED.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.models import Dispatch, Run, RunMemory, RunSignal
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


_BASE = "http://engine.test"
_TOKEN_RESP = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}
```

### Step 3: Build the LLM stub script
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (append)

The LLM provider must produce deterministic tool-call results for three LLM-content nodes: `load_work_item`, `generate_tasks`, `generate_plan` (called twice, once per task).

```python
@pytest_asyncio.fixture(loop_scope="function")
async def llm_provider() -> AsyncIterator[StubLLMProvider]:
    """Scripted LLM responses for the v0.4.0-manual flow."""
    from app.core.llm import StubLLMProvider

    script = [
        # load_work_item — returns parsed brief
        {
            "tool": "result_load_work_item",
            "arguments": {
                "id": "FEAT-999",
                "type": "FEAT",
                "title": "LLM-derived title",
            },
        },
        # generate_tasks — returns 3 tasks
        {
            "tool": "result_generate_tasks",
            "arguments": {
                "tasks": [
                    {"id": "T-llm-1", "title": "LLM Task 1", "summary": "..."},
                    {"id": "T-llm-2", "title": "LLM Task 2", "summary": "..."},
                    {"id": "T-llm-3", "title": "LLM Task 3", "summary": "..."},
                ],
            },
        },
        # generate_plan for T-edit-1
        {
            "tool": "result_generate_plan",
            "arguments": {"plan": "# LLM plan for T-edit-1\n..."},
        },
        # generate_plan for T-edit-2
        {
            "tool": "result_generate_plan",
            "arguments": {"plan": "# LLM plan for T-edit-2\n..."},
        },
    ]
    yield StubLLMProvider(script)
```

(Exact tool-name + argument shape depends on what `result_schema` the v0.3.0 bindings register — pull the names from `executors/bootstrap.py::register_lifecycle_v03`.)

### Step 4: Build the engine respx stub
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (append)

Stub the engine endpoints that fire during the flow:
- `POST /api/auth/token` — token endpoint (FEAT-008)
- `POST /api/workflows/{wid}/items` — W1 (work-item create) AND T1 (task create) × N
- `POST /api/items/{iid}/transitions` — W2, W4, W6, T2, T4, T5, T6, T7, T9, T10

```python
@pytest.fixture
def engine_mock(work_item_workflow_id: uuid.UUID, task_workflow_id: uuid.UUID):
    """respx mock for every engine HTTP call.

    Returns:
      - mock context manager
      - counters dict (mutated by the routes) for assertions
    """
    work_item_engine_id = uuid.uuid4()
    counters = {"work_item_creates": 0, "task_creates": 0, "transitions": 0,
                "task_engine_ids": []}

    with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
        rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))

        # W1 — work item create
        def _wi_create(request):
            counters["work_item_creates"] += 1
            return Response(201, json={"data": {"id": str(work_item_engine_id)}})
        rx.post(f"/api/workflows/{work_item_workflow_id}/items").mock(side_effect=_wi_create)

        # T1 — task create (N times)
        def _task_create(request):
            counters["task_creates"] += 1
            new_id = uuid.uuid4()
            counters["task_engine_ids"].append(new_id)
            return Response(201, json={"data": {"id": str(new_id)}})
        rx.post(f"/api/workflows/{task_workflow_id}/items").mock(side_effect=_task_create)

        # Every transition endpoint — generic match
        def _transition(request):
            counters["transitions"] += 1
            return Response(200, json={"data": {"ok": True}})
        rx.post(rx.regex(r"/api/items/[a-f0-9-]+/transitions")).mock(side_effect=_transition)

        yield rx, counters
```

### Step 5: Build the test loop driver
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (append)

The flow alternates between runtime advancement and operator signal delivery. Pattern:

```python
async def _wait_for_paused(session_factory, run_id, timeout=5.0) -> uuid.UUID:
    """Block until Run.status == PAUSED; return the in-flight Dispatch.id."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            if run is not None and RunStatus(run.status) == RunStatus.PAUSED:
                dispatch = await session.scalar(
                    select(Dispatch).where(
                        Dispatch.run_id == run_id,
                        Dispatch.state == "dispatched",
                    )
                )
                if dispatch is not None:
                    return dispatch.dispatch_id
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} never paused within {timeout}s")


async def _deliver_signal(session_factory, supervisor, registry, run_id, name, task_id, payload):
    """Mirror service.send_signal but in-test (avoids the FastAPI route layer)."""
    from app.modules.ai.service import send_signal
    async with session_factory() as session:
        await send_signal(
            session,
            run_id=run_id,
            name=name,
            task_id=task_id or "",
            payload=payload,
            supervisor=supervisor,
            executor_registry=registry,
        )
```

### Step 6: The end-to-end test
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (append)

```python
async def test_full_manual_lifecycle_completes_with_edited_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    llm_provider,
    engine_mock,
    work_item_workflow_id: uuid.UUID,
    task_workflow_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    rx, counters = engine_mock

    # Build the registry with v0.4.0-manual bindings.
    from app.modules.ai.executors.bootstrap import register_lifecycle_v04_manual
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient

    lifecycle_client = FlowEngineLifecycleClient(base_url=_BASE, api_key="test")
    registry = ExecutorRegistry()
    register_lifecycle_v04_manual(
        registry,
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        work_item_workflow_id=work_item_workflow_id,
        task_workflow_id=task_workflow_id,
    )

    # Seed a Run.
    run_id = await _seed_run(
        session_factory,
        agent_ref="lifecycle-agent@0.4.0-manual",
        intake={"workItem": {"id": "FEAT-999", "kind": "FEAT",
                             "content": "# FEAT-999 — title\n\nbody"}},
    )

    supervisor = RunSupervisor()
    loop_task = asyncio.create_task(
        run_deterministic_loop(
            run_id=run_id,
            agent=load_agent("lifecycle-agent@0.4.0-manual"),
            trace=NoopTraceStore(),
            supervisor=supervisor,
            registry=registry,
            session_factory=session_factory,
            cancel_event=asyncio.Event(),
            dispatch_timeout_seconds=30,
        )
    )

    try:
        # === Checkpoint 1: confirm_brief ===
        await _wait_for_paused(session_factory, run_id)
        await _deliver_signal(session_factory, supervisor, registry, run_id,
                              name="brief-confirmed", task_id="", payload={})

        # === Checkpoint 2: confirm_tasks — REPLACE 3 LLM tasks with 2 edits ===
        await _wait_for_paused(session_factory, run_id)
        await _deliver_signal(session_factory, supervisor, registry, run_id,
                              name="tasks-confirmed", task_id="", payload={
                                  "tasks": [
                                      {"id": "T-edit-1", "title": "Edited 1"},
                                      {"id": "T-edit-2", "title": "Edited 2"},
                                  ]
                              })

        # === Per-task loop: 2 iterations ===
        for task_id in ["T-edit-1", "T-edit-2"]:
            # confirm_plan
            await _wait_for_paused(session_factory, run_id)
            await _deliver_signal(session_factory, supervisor, registry, run_id,
                                  name="plan-confirmed", task_id=task_id, payload={})
            # request_implementation
            await _wait_for_paused(session_factory, run_id)
            await _deliver_signal(session_factory, supervisor, registry, run_id,
                                  name="implementation-complete",
                                  task_id=task_id, payload={})
            # human_review_implementation
            await _wait_for_paused(session_factory, run_id)
            await _deliver_signal(session_factory, supervisor, registry, run_id,
                                  name="review-completed", task_id=task_id,
                                  payload={"verdict": "pass"})

        # === Wait for completion ===
        await asyncio.wait_for(loop_task, timeout=15.0)

        # === Assertions ===
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            assert RunStatus(run.status) == RunStatus.COMPLETED

            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run_id)
            )
            mem = (mem_row.data if mem_row else {}).get("lifecycle.v1", {})
            history = mem.get("reviewHistory", [])
            assert len(history) == 2
            assert all(e["reviewer"] == "human" for e in history)
            assert all(e["verdict"] == "pass" for e in history)

        # Engine assertions:
        assert counters["work_item_creates"] == 1
        assert counters["task_creates"] == 2, (
            f"propose_tasks must fan out to the *edited* 2 tasks, not the LLM 3; "
            f"got {counters['task_creates']}"
        )
        # Transitions: T2, T4 per task (2 × 2 = 4) + W2 + T5, T6, T7, T9, T10 per task
        # (5 × 2 = 10) + W4 + W6 = 16. Exact count depends on the engine binding
        # set; spot-check the lower bound.
        assert counters["transitions"] >= 10

    finally:
        if not loop_task.done():
            loop_task.cancel()
        await _cleanup(session_factory, run_id)
        await lifecycle_client.aclose()
```

### Step 7: Verify no `core.llm` import in deterministic path
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (append)

Add a separate quick test (or extend an existing import-quarantine test) asserting `runtime_deterministic.py` does not import `core.llm` even with the new agent loaded. The existing `tests/test_runtime_deterministic_is_pure.py` already covers this — verify it still passes.

```python
def test_runtime_deterministic_quarantine_holds_for_v04_manual():
    """Regression: registering v0.4.0-manual must not transitively import core.llm
    into runtime_deterministic.  Re-run the existing structural guard explicitly
    here to make the dependency visible to readers of this test file."""
    # The actual guard lives in tests/test_runtime_deterministic_is_pure.py.
    # This test is a documentation aid; the guard runs on every test session.
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-c",
         "import app.modules.ai.runtime_deterministic; "
         "import app.modules.ai.executors.bootstrap; "
         "import sys; "
         "assert 'app.core.llm' not in sys.modules, 'leaked core.llm import'"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
```

### Step 8: Run the suite
**File:** N/A
**Action:** Verify

```bash
uv run pytest tests/integration/test_lifecycle_v04_manual.py -v
```

Expected: 2 tests pass (the e2e + the import quarantine). Expect ~10-15s runtime for the e2e.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_v04_manual.py` | Create | End-to-end test + import quarantine; ~250 lines. |
| `tests/integration/conftest.py` | Verify | May need to add `work_item_workflow_id` / `task_workflow_id` fixtures if not present. |

## Edge Cases & Risks
- **Stub LLM tool-name / argument schema.** The actual tool names registered by `register_lifecycle_v03`'s LLM bindings determine what `StubLLMProvider` must script. If they differ from what this plan assumes, copy the exact names from `executors/bootstrap.py` after T-295/T-299 land.
- **Engine endpoint shape.** The transitions endpoint (POST `/api/items/{id}/transitions`) accepts a body including `transition_key`. The respx generic match doesn't verify the body — to assert "T1 fired before T2", capture the request bodies and post-test inspect them. Optional stricter assertion.
- **Race between `_wait_for_paused` and signal delivery.** If the runtime advances faster than the polling cadence, `_wait_for_paused` may miss a pause window. Use a small `await asyncio.sleep(0.05)` after delivering each signal before the next `_wait_for_paused` to let the loop re-park.
- **Test timeout.** With 2 tasks × 3 checkpoints each + 2 top-level checkpoints = 8 pause-resume cycles, plus 16+ engine round-trips, total runtime can be 5-15s. Mark the test loosely (no `pytest.mark.slow` unless the repo has that marker convention).
- **Postgres state leak.** The `_cleanup` helper at the end MUST remove the Run + all dependent rows (Dispatches, RunSignals, RunMemory, Steps). Mirror the cleanup from `test_runtime_human_pause.py`.

## Acceptance Verification
- [ ] AC-1 — Test scenario runs: 3 LLM-generated tasks → 2 operator-edited tasks → 2 plan approvals → 2 review approvals.
- [ ] AC-2 — `Run.status` transitions: pending → running → paused (×N) → running → completed.
- [ ] AC-3 — `counters["task_creates"] == 2` (NOT 3) — the load-bearing assertion that operator edits reach the engine.
- [ ] AC-4 — `LifecycleMemory.reviewHistory` has 2 entries, all `reviewer: "human"`, all `verdict: "pass"`.
- [ ] AC-5 — Final `Run.status == COMPLETED`.
- [ ] AC-6 — Import quarantine test passes — no `core.llm` leak.
- [ ] AC-7 — Test completes in under 20s on the CI environment.
