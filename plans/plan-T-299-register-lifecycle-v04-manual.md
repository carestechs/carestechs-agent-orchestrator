# Implementation Plan: T-299 — `register_lifecycle_v04_manual` bootstrap + lifespan wiring

## Task Reference
- **Task ID:** T-299
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** FEAT-015 §4.1 — single source of truth for the manual variant's bindings. Combines T-295 (rebound v0.3.0 helper) + T-296 (memory_patch_builder on HumanExecutor) + T-297 (the four builders) + T-298 (the YAML) into a bootable agent.

## Overview
Add `register_lifecycle_v04_manual` to `src/app/modules/ai/executors/bootstrap.py`. The function: (a) calls the refactored `register_lifecycle_v03(..., agent_ref="lifecycle-agent@0.4.0-manual", skip_review_implementation=True)` to install every shared binding under the new ref; (b) registers four `HumanExecutor` bindings — one per new checkpoint — each carrying its `memory_patch_builder` from T-297. Then wire the helper from `src/app/lifespan.py::_bootstrap_executor_registry` alongside the existing `register_lifecycle_v03` call. After this lands, the orchestrator boots with both agent refs registered; `validate_executor_coverage` passes for both.

## Implementation Steps

### Step 1: Add the new bootstrap function
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Append the new function near `register_lifecycle_v03` (same module-level placement):

```python
def register_lifecycle_v04_manual(
    registry: ExecutorRegistry,
    *,
    lifecycle_client: FlowEngineLifecycleClient,
    session_factory: async_sessionmaker[AsyncSession],
    work_item_workflow_id: uuid.UUID,
    task_workflow_id: uuid.UUID,
    actor: str = "lifecycle-agent",
) -> None:
    """Bootstrap lifecycle-agent@0.4.0-manual (FEAT-015).

    Reuses every v0.3.0 binding under the new agent_ref except the LLM
    reviewer, then registers four HumanExecutor checkpoints plus the
    human reviewer in place of the LLM one.
    """
    from app.modules.ai.executors.human import HumanExecutor
    from app.modules.ai.executors.lifecycle_manual_patches import (
        _apply_brief_correction,
        _apply_plan_correction,
        _apply_review_verdict,
        _apply_tasks_correction,
    )

    agent_ref = "lifecycle-agent@0.4.0-manual"

    # 1. Reuse v0.3.0 bindings under the new ref, skipping the LLM reviewer.
    register_lifecycle_v03(
        registry,
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        work_item_workflow_id=work_item_workflow_id,
        task_workflow_id=task_workflow_id,
        actor=actor,
        agent_ref=agent_ref,
        skip_review_implementation=True,
    )

    # 2. Four new human checkpoints.
    registry.register(
        agent_ref,
        "confirm_brief",
        HumanExecutor(
            ref="human:confirm_brief",
            expected_signal_name="brief-confirmed",
            memory_patch_builder=_apply_brief_correction,
        ),
    )
    registry.register(
        agent_ref,
        "confirm_tasks",
        HumanExecutor(
            ref="human:confirm_tasks",
            expected_signal_name="tasks-confirmed",
            memory_patch_builder=_apply_tasks_correction,
        ),
    )
    registry.register(
        agent_ref,
        "confirm_plan",
        HumanExecutor(
            ref="human:confirm_plan",
            expected_signal_name="plan-confirmed",
            memory_patch_builder=_apply_plan_correction,
        ),
    )

    # 3. Human reviewer replacing the LLM `review_implementation`.
    registry.register(
        agent_ref,
        "human_review_implementation",
        HumanExecutor(
            ref="human:review_implementation",
            expected_signal_name="review-completed",
            memory_patch_builder=_apply_review_verdict,
        ),
    )
```

### Step 2: Update `__all__` if the module exports it
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

If `bootstrap.py` has an `__all__` list, append `"register_lifecycle_v04_manual"`. Otherwise no change.

### Step 3: Wire from lifespan
**File:** `src/app/lifespan.py`
**Action:** Modify

Locate `_bootstrap_executor_registry` (around `lifespan.py:155` based on the grep earlier). Find the existing `register_lifecycle_v03(...)` call and add the v0.4.0-manual call immediately after, sharing the same arguments:

```python
register_lifecycle_v03(
    registry,
    lifecycle_client=lifecycle_engine_client,
    session_factory=session_factory,
    work_item_workflow_id=work_item_workflow_id,
    task_workflow_id=task_workflow_id,
)

# FEAT-015: register the manual variant alongside v0.3.0.
register_lifecycle_v04_manual(
    registry,
    lifecycle_client=lifecycle_engine_client,
    session_factory=session_factory,
    work_item_workflow_id=work_item_workflow_id,
    task_workflow_id=task_workflow_id,
)
```

Import the new function at the top of `lifespan.py`:

```python
from app.modules.ai.executors.bootstrap import (
    register_lifecycle_v03,
    register_lifecycle_v04_manual,
)
```

### Step 4: Sanity boot
**File:** N/A
**Action:** Verify

```bash
uv run uvicorn app.main:app --port 8001 &
sleep 3
curl -s http://localhost:8001/api/v1/health  # or whatever the health endpoint is
# Expected: 200 OK
# In logs, expected: "executor registry: validate_executor_coverage OK"
# (or an equivalent INFO line confirming both agent refs registered)
kill %1
```

If `validate_executor_coverage` raises at boot, the cause is one of: (a) a node in the v0.4.0-manual YAML without a binding in this function; (b) a binding under a different agent_ref. Grep is the fastest debug — `grep -c 'lifecycle-agent@0.4.0-manual' src/app/modules/ai/executors/bootstrap.py` should show one occurrence per registration site.

### Step 5: Smoke-start a run
**File:** N/A
**Action:** Verify

```bash
uv run orchestrator run lifecycle-agent@0.4.0-manual --work-item docs/work-items/FEAT-015-lifecycle-manual-variant.md
# Expected: 202 from POST /api/v1/runs; run starts; first dispatches:
#   load_work_item (LLM) → confirm_brief (parks; Run.status=paused)
```

Stop the run with `uv run orchestrator cancel <run_id>`. Full end-to-end exercise is T-302.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/bootstrap.py` | Modify | New `register_lifecycle_v04_manual` function (~50 lines including imports). |
| `src/app/lifespan.py` | Modify | One new import + one new function call in `_bootstrap_executor_registry`. |

## Edge Cases & Risks
- **`agent_ref` mismatch.** If T-295 was applied incompletely (some `registry.register` sites still hard-code `"lifecycle-agent@0.3.0"`), this function will register a partial set under `"lifecycle-agent@0.4.0-manual"` and boot fails at `validate_executor_coverage` with a clear "node X has no binding" message. The fix is in T-295's diff, not here.
- **Duplicate registration.** `ExecutorRegistry.register` raises on duplicate `(agent_ref, node_name)`. If this function is somehow called twice (e.g., a test fixture registers it then lifespan does too), boot fails fast. Document in the function docstring: "single source of truth; called once per process at lifespan."
- **Workflow IDs shared with v0.3.0.** Both variants drive the same engine workflows. If the user later configures separate workflow IDs per variant (unlikely but possible), this function needs a `*_workflow_id` rename or per-variant config. Out of scope; flag in T-304's architecture doc.
- **`work_item_workflow_id` is `None`.** `register_lifecycle_v03` already handles this (the engine create executor falls back to a no_executor exemption per the BUG-003 lifespan logic). This function inherits that behavior unchanged.
- **`HumanExecutor` import circular.** `bootstrap.py` imports `human.py` and `lifecycle_manual_patches.py`. Neither imports `bootstrap.py` back. No cycle expected; verify with `python -c "import app.modules.ai.executors.bootstrap"`.

## Acceptance Verification
- [ ] AC-1 — `register_lifecycle_v04_manual(...)` registers 19 bindings for `agent_ref="lifecycle-agent@0.4.0-manual"` (15 shared + 4 new). Count via `len([k for k in registry._bindings if k[0] == "lifecycle-agent@0.4.0-manual"])`.
- [ ] AC-2 — Each of the four new bindings has `expected_signal_name` and `memory_patch_builder` set as documented.
- [ ] AC-3 — Lifespan invokes the new function after `register_lifecycle_v03`; both refs register cleanly.
- [ ] AC-4 — `validate_executor_coverage()` passes at lifespan startup for both agent refs.
- [ ] AC-5 — Smoke run starts and parks at `confirm_brief`.
- [ ] AC-6 — Existing `tests/test_runtime_deterministic_is_pure.py` continues to pass — no new `core.llm` import introduced.
- [ ] AC-7 — `pyright` clean.
