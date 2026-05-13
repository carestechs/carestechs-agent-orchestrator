# Implementation Plan: T-296 — Add `memory_patch_builder` to `HumanExecutor`

## Task Reference
- **Task ID:** T-296
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** FEAT-015 §4.1 — the four manual-variant checkpoints need to let the operator inject corrections via the signal payload. `LLMContentExecutor` already takes a `memory_patch_builder`; mirror the same contract on `HumanExecutor`.

## Overview
`HumanExecutor` currently returns a `dispatched` envelope and the *terminal* envelope is built by `_deliver_to_human_dispatch` in `service.py` when the operator's signal arrives. To support payload-driven memory edits, the executor needs to (a) expose the builder as an inspectable attribute on the binding and (b) `_deliver_to_human_dispatch` reads the binding from the registry, calls the builder if present, and embeds the result at `result.__memory_patch` on the envelope it constructs — the same hook the runtime already merges into `RunMemory.data` (per `runtime_deterministic.py` line ~420).

## Implementation Steps

### Step 1: Add `memory_patch_builder` to `HumanExecutor.__init__`
**File:** `src/app/modules/ai/executors/human.py`
**Action:** Modify

Add the import and the parameter:

```python
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

# Reuse the type alias from llm_content for shape parity — same shape,
# different writer.
from app.modules.ai.executors.llm_content import MemoryPatchBuilder


class HumanExecutor:
    mode: ClassVar[ExecutorMode] = "human"

    def __init__(
        self,
        ref: str,
        *,
        expected_signal_name: str,
        memory_patch_builder: MemoryPatchBuilder | None = None,
    ) -> None:
        self.name = ref
        self._ref = ref
        self.expected_signal_name = expected_signal_name
        self.memory_patch_builder = memory_patch_builder  # public — read by signal-create adapter
```

Note: `memory_patch_builder` is a public attribute (no leading underscore), unlike `LLMContentExecutor` where it's private. This is deliberate — `_deliver_to_human_dispatch` lives outside the executor module and reads the binding's executor to discover the builder.

### Step 2: Update `_deliver_to_human_dispatch` to call the builder
**File:** `src/app/modules/ai/service.py`
**Action:** Modify

Find the function (currently around `service.py:469`). Change its signature to accept the executor registry:

```python
async def _deliver_to_human_dispatch(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    signal_name: str,
    task_id: str,
    payload: dict[str, Any],
    supervisor: RunSupervisor,
    executor_registry: ExecutorRegistry,
) -> None:
```

After the function looks up the in-flight `Dispatch` row but before constructing the envelope, resolve the binding and call the builder if present:

```python
dispatch = rows[0]
now = datetime.now(UTC)

# Look up the binding to discover the memory_patch_builder, if any.
result: dict[str, Any] = {"signal_name": signal_name, "task_id": task_id, "payload": payload}
try:
    binding = executor_registry.resolve(<agent_ref>, dispatch.intake["nodeName"])
    builder = getattr(binding.executor, "memory_patch_builder", None)
    if builder is not None:
        # Load current lifecycle memory for the builder's read side.
        mem_row = await db.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
        current_memory: Mapping[str, Any] = (mem_row.data if mem_row is not None else {}) or {}
        patch = builder(payload, current_memory)
        result["__memory_patch"] = patch
except Exception as exc:
    # Builder raised — fail the dispatch with the exception in `detail`.
    dispatch.mark_failed(at=now, detail=f"memory_patch_builder_failed: {type(exc).__name__}: {exc}")
    await db.commit()
    envelope = DispatchEnvelope.model_validate(dispatch, from_attributes=True)
    supervisor.deliver_dispatch(dispatch.dispatch_id, envelope)
    return

dispatch.mark_completed(at=now, result=result)
await db.commit()
envelope = DispatchEnvelope.model_validate(dispatch, from_attributes=True)
supervisor.deliver_dispatch(dispatch.dispatch_id, envelope)
```

`<agent_ref>` comes from the `Run` row — fetch it via `await db.scalar(select(Run.agent_ref).where(Run.id == run_id))` once at the top of the existing function.

### Step 3: Plumb the registry to the call site
**File:** `src/app/modules/ai/service.py`
**Action:** Modify

The caller of `_deliver_to_human_dispatch` is `create_run_signal` (the signal-create service entry point). Update its signature to accept `executor_registry: ExecutorRegistry` and forward it. The route handler in `router.py` already has access to `app.state.executor_registry` via the existing dependency injection — extend the dependency wiring if needed.

If `create_run_signal` doesn't already take the registry, add an `executor_registry: ExecutorRegistry` parameter to its signature with no default. The route handler gets `app.state.executor_registry` via a `Depends(...)` helper or directly from `request.app.state`. Use the same access pattern existing services use (grep for `app.state.executor_registry` to find the convention).

### Step 4: Update tests' fixtures if needed
**File:** `tests/integration/test_runtime_human_pause.py` (and any other test exercising `_deliver_to_human_dispatch` directly)
**Action:** Modify (only if tests call the function directly)

If any test calls `_deliver_to_human_dispatch` or `create_run_signal` directly, pass an empty `ExecutorRegistry()` or the test's registry fixture. Otherwise no change — the existing tests use `supervisor.deliver_dispatch` directly and bypass this path.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/human.py` | Modify | New optional `memory_patch_builder` constructor arg; exposed as public attribute. |
| `src/app/modules/ai/service.py` | Modify | `_deliver_to_human_dispatch` resolves the binding, calls the builder if present, embeds patch in envelope. |
| `tests/integration/test_runtime_human_pause.py` | Modify (likely no-op) | Verify fixtures still work; pass registry if any direct callers exist. |

## Edge Cases & Risks
- **Builder raises.** Catch broadly inside `_deliver_to_human_dispatch`; mark dispatch `failed` with the exception in detail; deliver the failed envelope. The runtime then raises `_ExecutorFailure` and terminates the run with `stop_reason=error`. Strictness is intentional — bad payloads must not silently corrupt memory.
- **Builder returns a key starting with `_`.** `_write_state` already filters those out (`runtime_deterministic.py` line 184). Document this constraint in the `MemoryPatchBuilder` type alias docstring.
- **Two dispatches with the same `(run_id, name)`.** `_deliver_to_human_dispatch` already guards against this with a `len(rows) > 1` warning. No additional concern from the builder hook.
- **Dispatch row's `intake` missing `nodeName`.** The deterministic runtime always sets it (`runtime_deterministic.py::_build_node_intake`); defensive `.get("nodeName")` returns `None` and the binding lookup raises `ExecutorRegistryError` — caught by the outer `try` and surfaced as a failed dispatch with a clear detail.
- **Backward-compat.** The existing `request_implementation` binding does NOT set `memory_patch_builder`. The hot path is byte-identical — `builder is None` skips the lookup. Smoke-test by running the existing `test_runtime_human_pause.py` suite.

## Acceptance Verification
- [ ] AC-1 (no builder) — running an existing `HumanExecutor` binding without `memory_patch_builder` produces an envelope whose `result` shape matches the current production behavior (the existing `request_implementation` test path).
- [ ] AC-2 (builder set) — when a binding has `memory_patch_builder=fn`, the signal-create path calls `fn(payload, current_memory)` once and the envelope's `result["__memory_patch"]` equals the return value.
- [ ] AC-3 (builder raises) — when `fn` raises, the envelope has `state="failed"`, `outcome="error"`, and `detail` contains the exception's type + message.
- [ ] AC-4 (regression) — `tests/integration/test_runtime_human_pause.py` (5 tests, including the unbounded-wait one from PR #86) all pass without modification.
- [ ] AC-5 — `pyright` clean.
