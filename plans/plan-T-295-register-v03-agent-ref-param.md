# Implementation Plan: T-295 — Refactor `register_lifecycle_v03` to accept `agent_ref` and `skip_review_implementation`

## Task Reference
- **Task ID:** T-295
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** FEAT-015 §4.1 — the manual variant reuses every v0.3.0 binding under a new agent ref except the reviewer slot. This refactor is the load-bearing first step.

## Overview
Make `register_lifecycle_v03` register bindings under a configurable agent ref (defaulting to `"lifecycle-agent@0.3.0"`) and let a caller skip the LLM `review_implementation` binding so a sibling variant can register a human one in that slot. No behavior change for the existing caller — the v0.3.0 acceptance suite passes unchanged.

## Implementation Steps

### Step 1: Add the two new keyword-only parameters
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Find `def register_lifecycle_v03(...)` and add two keyword-only parameters at the end of the signature:

```python
def register_lifecycle_v03(
    registry: ExecutorRegistry,
    *,
    lifecycle_client: FlowEngineLifecycleClient,
    session_factory: async_sessionmaker[AsyncSession],
    work_item_workflow_id: uuid.UUID,
    task_workflow_id: uuid.UUID,
    actor: str = "lifecycle-agent",
    agent_ref: str = "lifecycle-agent@0.3.0",
    skip_review_implementation: bool = False,
) -> None:
```

Update the function's docstring to document the two new parameters (one line each).

### Step 2: Thread `agent_ref` through every `registry.register` call inside the function
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Currently the function passes a hard-coded `"lifecycle-agent@0.3.0"` (or an `agent_ref` local) to every `registry.register(agent_ref, node_name, ...)` call. Search the function body for `lifecycle-agent@0.3.0` and `"lifecycle-agent@0"` and replace each occurrence of the hard-coded ref with the new `agent_ref` parameter. Each `registry.register(...)` line becomes `registry.register(agent_ref, "<node>", executor, ...)`.

There are roughly 15-20 such call sites in `register_lifecycle_v03` (one per node binding plus the `no_executor` exemptions for `start`). Cover every one.

### Step 3: Gate the `review_implementation` registration on `skip_review_implementation`
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Locate the block that registers the `review_implementation` binding (currently around bootstrap.py:924 where `memory_patch_builder=_patch_review` is passed to `LLMContentExecutor`). Wrap it in:

```python
if not skip_review_implementation:
    registry.register(
        agent_ref,
        "review_implementation",
        LLMContentExecutor(
            ref="llm:review_implementation",
            ...
            memory_patch_builder=_patch_review,
            ...
        ),
    )
```

Do NOT touch the related `approve_review` engine binding — that one stays registered regardless (the human reviewer in the variant uses the same `approve_review` for the T10 transition).

### Step 4: Confirm the v0.3.0 lifespan caller passes nothing
**File:** `src/app/lifespan.py`
**Action:** Modify (verify only — no code change expected)

Find the existing `register_lifecycle_v03(...)` call site. It should pass only the four named arguments (`lifecycle_client`, `session_factory`, `work_item_workflow_id`, `task_workflow_id`). Do not add explicit `agent_ref=...` / `skip_review_implementation=...` here — defaults must kick in unchanged.

### Step 5: Run the v0.3.0 acceptance suite
**File:** N/A
**Action:** Verify

Run:
```bash
uv run pytest tests/integration/test_lifecycle_v03_*.py tests/modules/ai/executors/ -q
uv run pyright src/app/modules/ai/executors/bootstrap.py
```

All tests must pass without modification. This is the existence proof that the refactor is a no-op for the v0.3.0 caller.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/bootstrap.py` | Modify | Add `agent_ref` + `skip_review_implementation` keyword-only params; thread through every `registry.register` call; gate the `review_implementation` registration. |
| `src/app/lifespan.py` | Verify | Existing call site keeps default args — no change. |

## Edge Cases & Risks
- **Missed call site.** If any `registry.register(...)` inside the function still uses the hard-coded `"lifecycle-agent@0.3.0"`, the v0.4.0-manual entry point (T-299) will register *partial* bindings under the new ref — boot will fail at `validate_executor_coverage`. Grep before commit: `grep -n "lifecycle-agent@0" src/app/modules/ai/executors/bootstrap.py` should show zero matches inside `register_lifecycle_v03`.
- **`skip_review_implementation` and downstream nodes.** The `approve_review` (T10) engine binding is unaffected — it does NOT belong to the LLM reviewer; the branch from `review_implementation` (or `human_review_implementation`) routes there on `verdict=pass`. Don't accidentally gate it.
- **Test fixtures.** Any test that instantiates the function with `agent_ref=...` keyword (none today) would need updating — confirmed by grep on `register_lifecycle_v03(`.

## Acceptance Verification
- [ ] AC-1 (default behavior) — running `register_lifecycle_v03(registry, ..., agent_ref="lifecycle-agent@0.3.0")` registers exactly the same `(agent_ref, node_name)` set as before (verified by listing keys post-call in a unit test or by the integration suite passing).
- [ ] AC-2 (rebound) — `register_lifecycle_v03(registry, ..., agent_ref="lifecycle-agent@0.4.0-manual")` registers under the new ref, with `validate_executor_coverage` happy for that ref (gated by T-298 + T-299 also being present at boot time; in isolation, this is verified by a simple unit assertion on `registry._bindings` keys).
- [ ] AC-3 (skip-reviewer) — passing `skip_review_implementation=True` omits the `review_implementation` binding.
- [ ] AC-4 (regression) — full v0.3.0 acceptance suite passes unchanged.
- [ ] AC-5 — `pyright` clean.
