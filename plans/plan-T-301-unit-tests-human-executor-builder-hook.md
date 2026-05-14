# Implementation Plan: T-301 — Unit tests for `HumanExecutor.memory_patch_builder` hook

## Task Reference
- **Task ID:** T-301
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** T-300 covers builder shape; this task covers the executor's plumbing. The two are complementary unit surfaces — regressions stay local.

## Overview
Extend `tests/modules/ai/executors/test_human_executor.py` (or create it) with three focused tests: (a) executor with no builder behaves like today; (b) executor with a builder has the builder called and its output embedded at `result.__memory_patch`; (c) executor with a raising builder produces a failed envelope.

Most of the hook's plumbing actually lives in `service.py::_deliver_to_human_dispatch` (per T-296 Step 2) — so the tests target that function, not `HumanExecutor.dispatch` directly.

## Implementation Steps

### Step 1: Locate or create the test file
**File:** `tests/modules/ai/executors/test_human_executor.py`
**Action:** Create (if missing) or extend

Confirm with `ls tests/modules/ai/executors/test_human*.py`. If it doesn't exist, create the file with these imports:

```python
"""Unit tests for HumanExecutor + memory_patch_builder integration (T-296 / T-301)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.human import HumanExecutor
from app.modules.ai.executors.registry import ExecutorRegistry

pytestmark = pytest.mark.asyncio(loop_scope="function")
```

### Step 2: Test 1 — executor without builder
**File:** `tests/modules/ai/executors/test_human_executor.py`
**Action:** Modify (append)

```python
class TestHumanExecutorBuilderHook:
    async def test_executor_without_builder_has_none_attribute(self) -> None:
        executor = HumanExecutor(
            ref="human:test",
            expected_signal_name="signal-x",
        )
        assert executor.memory_patch_builder is None

    async def test_dispatch_returns_dispatched_envelope_with_or_without_builder(self) -> None:
        # The dispatch() method itself does not invoke the builder — that's
        # the signal-create adapter's job (see _deliver_to_human_dispatch).
        # This test pins that .dispatch() still returns a `dispatched`
        # envelope unchanged.
        ctx = DispatchContext(
            dispatch_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            step_id=uuid.uuid4(),
            agent_ref="some-agent",
            node_name="some-node",
            intake={"runId": "x", "nodeName": "some-node"},
        )
        no_builder = HumanExecutor(ref="human:a", expected_signal_name="a")
        with_builder = HumanExecutor(
            ref="human:b",
            expected_signal_name="b",
            memory_patch_builder=lambda payload, mem: {"k": "v"},
        )
        env_a = await no_builder.dispatch(ctx)
        env_b = await with_builder.dispatch(ctx)
        assert env_a.state.value == "dispatched"
        assert env_b.state.value == "dispatched"
        # No builder call happened during dispatch — that's the signal adapter's job.
        assert env_a.result is None
        assert env_b.result is None
```

### Step 3: Test 2 — signal delivery invokes the builder
**File:** `tests/modules/ai/executors/test_human_executor.py`
**Action:** Modify (append)

This test exercises `_deliver_to_human_dispatch` through the actual `send_signal` service entry. It's slightly heavier than a pure unit test (needs DB + registry) but it's the only way to hit the codepath that calls the builder.

```python
async def test_signal_delivery_calls_builder_and_embeds_patch(
    self,
    session_factory: async_sessionmaker[AsyncSession],
    # fixtures from conftest: a seeded Run with a HumanExecutor dispatch
    # in `dispatched` state plus a RunMemory row.
) -> None:
    from app.modules.ai.service import send_signal
    from app.modules.ai.supervisor import RunSupervisor

    builder_calls: list[Mapping[str, Any]] = []

    def builder(payload: Mapping[str, Any], mem: Mapping[str, Any]) -> dict:
        builder_calls.append(payload)
        return {"lifecycle.v1": {"work_item": {"title": payload.get("title", "default")}}}

    # Build a registry with the binding under test.
    registry = ExecutorRegistry()
    registry.register(
        "test-agent@1.0.0",
        "checkpoint",
        HumanExecutor(
            ref="human:checkpoint",
            expected_signal_name="approved",
            memory_patch_builder=builder,
        ),
    )

    # Seed a Run with a dispatch in DISPATCHED state pointing at this binding.
    # ... fixture setup (mirror tests/integration/test_runtime_human_pause.py
    # for the seed shape).

    supervisor = RunSupervisor()
    # Pre-register the dispatch future so deliver_dispatch resolves it.
    supervisor.register_dispatch(run_id, dispatch_id)

    async with session_factory() as session:
        await send_signal(
            session,
            run_id=run_id,
            name="approved",
            task_id="",  # no taskId for this checkpoint
            payload={"title": "Edited!"},
            supervisor=supervisor,
            executor_registry=registry,
        )

    envelope = await supervisor.await_dispatch(dispatch_id)
    assert envelope.outcome.value == "ok"
    assert envelope.result is not None
    assert envelope.result["__memory_patch"] == {
        "lifecycle.v1": {"work_item": {"title": "Edited!"}}
    }
    assert len(builder_calls) == 1
    assert builder_calls[0] == {"title": "Edited!"}
```

### Step 4: Test 3 — raising builder produces failed envelope
**File:** `tests/modules/ai/executors/test_human_executor.py`
**Action:** Modify (append)

```python
async def test_raising_builder_fails_the_dispatch(
    self,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def failing_builder(payload: Mapping[str, Any], mem: Mapping[str, Any]) -> dict:
        raise ValueError("bad payload")

    registry = ExecutorRegistry()
    registry.register(
        "test-agent@1.0.0",
        "checkpoint",
        HumanExecutor(
            ref="human:checkpoint",
            expected_signal_name="approved",
            memory_patch_builder=failing_builder,
        ),
    )

    # ... same seed as Step 3

    await send_signal(
        ...,
        executor_registry=registry,
    )

    envelope = await supervisor.await_dispatch(dispatch_id)
    assert envelope.state.value == "failed"
    assert envelope.outcome.value == "error"
    assert envelope.detail is not None
    assert "ValueError" in envelope.detail
    assert "bad payload" in envelope.detail
```

### Step 5: Run the suite
**File:** N/A
**Action:** Verify

```bash
uv run pytest tests/modules/ai/executors/test_human_executor.py -v
# Expected: all tests pass; existing `test_runtime_human_pause.py` also passes.
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/executors/test_human_executor.py` | Create or Extend | Three test classes covering no-builder, builder-set, and raising-builder paths. |

## Edge Cases & Risks
- **DB / fixture overhead.** Tests 2 and 3 need a seeded `Run` + `Dispatch` row to exercise `_deliver_to_human_dispatch`. The simplest path is to copy the seed helper from `tests/integration/test_runtime_human_pause.py` — that file has working fixtures for this exact shape. Don't reinvent.
- **`send_signal` may dispatch through other side effects.** If the function calls effectors / webhooks, those need stubbing. For unit tests, mock `_deliver_to_human_dispatch` or instantiate it directly with controlled args. If the integration test pattern is simpler, just run these as integration tests (rename the file `tests/integration/test_human_executor_builder.py`).
- **Test isolation.** Each test must clean up its `Run` / `Dispatch` / `RunMemory` rows or use a session-scoped transaction that rolls back. Mirror the pattern in existing integration tests.
- **Schema for `send_signal`.** Verify the signature; it may take a `payload: dict` keyword or a typed Pydantic model. Adjust the test call accordingly.

## Acceptance Verification
- [ ] AC-1 — Test 1 confirms `memory_patch_builder` attribute defaults to `None` and `dispatch()` returns the same envelope shape with or without a builder set.
- [ ] AC-2 — Test 2 confirms the builder is called exactly once per signal delivery, with the signal payload, and its return value appears at `result.__memory_patch`.
- [ ] AC-3 — Test 3 confirms a raising builder produces a `failed` envelope whose `detail` contains the exception type + message.
- [ ] AC-4 — All existing `test_runtime_human_pause.py` tests still pass (regression check for the no-builder path).
- [ ] AC-5 — `pyright` clean.
