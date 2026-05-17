# Implementation Plan: T-309 — Integration test for `confirm_assignment` pause/resume + multi-task loop-back

## Task Reference
- **Task ID:** T-309
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** The closure test — T-308 proves the builder works in isolation, this test proves the runtime wiring + YAML + binding + signal route all line up end-to-end. AC items 1-5 map 1:1 to IMP-004 §9 success criteria.

## Overview
Extend the existing FEAT-015 / T-302 manual-variant integration test with assertions that the run pauses at `confirm_assignment`, resumes on signal, fires T5, and loops back through the checkpoint for a second task. Reuses the engine stub, LLM stub, and CLI/HTTP driver T-302 already wires.

## Implementation Steps

### Step 1: Locate the FEAT-015 integration test
**File:** `tests/integration/test_lifecycle_v04_manual.py` (or whatever filename T-302 produced)
**Action:** Read

Inspect the existing test to understand:
- How the test starts a run (HTTP `POST /api/v1/runs` or `CliRunner` against `orchestrator run`).
- How it polls for the run to enter `paused` status.
- How it delivers signals (HTTP `POST /api/v1/runs/{id}/signals`).
- What stubs are in place for engine + LLM.
- Whether the test is single-task or already multi-task.

If T-302 is single-task only, add a new sibling test for multi-task rather than overload the original. The brief (IMP-004 §10) calls out keeping T-302's regression surface untouched.

### Step 2: Confirm the test fixture supplies a multi-task work item
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify

The LLM stub for `generate_tasks` must produce ≥2 tasks for assertion (4) to exercise the loop-back. If T-302's stub returns a single task, parameterize or add a new fixture variant that returns two tasks. Each task must have `id` + `title` at minimum.

### Step 3: Add the new integration test
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify

Outline (adapt to existing helper signatures):

```python
@pytest.mark.asyncio
async def test_confirm_assignment_pause_resume_and_multitask_loop(
    api_client: AsyncClient,
    engine_stub: EngineStub,
    llm_stub: LLMStub,
    db_session: AsyncSession,
) -> None:
    # 1. Configure stubs for a two-task work item.
    llm_stub.script_generate_tasks([
        {"id": "task-1", "title": "First task", "summary": "..."},
        {"id": "task-2", "title": "Second task", "summary": "..."},
    ])
    # ... script plans, reviews for both tasks ...

    # 2. Start the run.
    response = await api_client.post(
        "/api/v1/runs",
        json={
            "agentRef": "lifecycle-agent@0.4.0-manual",
            "intake": {"workItem": {"id": "IMP-999", "kind": "IMP", "content": "..."}},
        },
    )
    run_id = response.json()["data"]["id"]

    # 3. Drive past brief + tasks checkpoints.
    await _deliver(api_client, run_id, "brief-confirmed", payload=None)
    await _deliver(api_client, run_id, "tasks-confirmed", payload=None)

    # 4. ASSERT: run pauses at confirm_assignment (for task-1).
    await _wait_for_status(api_client, run_id, "paused", timeout=5.0)
    run = await _get_run(api_client, run_id)
    current_node = await _current_dispatch_node(db_session, run_id)
    assert current_node == "confirm_assignment", \
        f"expected pause at confirm_assignment, got {current_node}"

    # 5. Deliver assignment-confirmed for task-1.
    await _deliver(
        api_client, run_id, "assignment-confirmed",
        payload={"assignee": "alice"},
    )

    # 6. ASSERT: run resumes to running, T5 fires for task-1.
    await _wait_for_status(api_client, run_id, "running", timeout=2.0)
    # Eventually the engine stub records task.T5 against task-1's engine id.
    await _wait_until(
        lambda: engine_stub.transitions_for("task-1", "task.T5"),
        timeout=5.0,
    )

    # 7. Drive task-1 through plan / impl / review checkpoints.
    await _deliver(api_client, run_id, "plan-confirmed", payload=None)
    # (implementation-complete signal then review-completed pass)
    await _deliver(api_client, run_id, "implementation-complete", payload=None)
    await _deliver(
        api_client, run_id, "review-completed",
        payload={"verdict": "pass"},
    )

    # 8. ASSERT: after mark_task_done, run re-pauses at confirm_assignment
    #    (now for task-2).
    await _wait_for_status(api_client, run_id, "paused", timeout=5.0)
    current_node = await _current_dispatch_node(db_session, run_id)
    assert current_node == "confirm_assignment"

    # 9. Deliver assignment-confirmed for task-2 with explicit taskId override.
    await _deliver(
        api_client, run_id, "assignment-confirmed",
        payload={"assignee": "bob", "taskId": "task-2"},
    )

    # 10. ASSERT: both assignments preserved in memory.
    memory = await _get_run_memory(db_session, run_id)
    assert memory["assignments"] == {"task-1": "alice", "task-2": "bob"}

    # 11. Drive task-2 through to completion, then assert run completed.
    await _deliver(api_client, run_id, "plan-confirmed", payload=None)
    await _deliver(api_client, run_id, "implementation-complete", payload=None)
    await _deliver(
        api_client, run_id, "review-completed",
        payload={"verdict": "pass"},
    )
    await _wait_for_status(api_client, run_id, "completed", timeout=10.0)
    # 12. Both engine tasks at done.
    assert engine_stub.final_state("task-1") == "done"
    assert engine_stub.final_state("task-2") == "done"
```

The helper signatures (`_deliver`, `_wait_for_status`, `_wait_until`, `_current_dispatch_node`, `_get_run_memory`) likely exist in the T-302 test or a sibling `tests/integration/_helpers.py`. Reuse, don't reimplement.

### Step 4: Add a regression test asserting `mark_task_done` routes through `confirm_assignment`
**File:** `tests/integration/test_lifecycle_v04_manual.py`
**Action:** Modify (or include inline in Step 3)

The critical assertion is step 8 above — the loop-back from `mark_task_done` must hit `confirm_assignment` again, not jump directly to `assign_task`. Without the YAML edit in T-307 Step 3 (`mark_task_done.branch.true: confirm_assignment`), this assertion fails — protecting against silent regression of the multi-task semantics.

### Step 5: Run the test
**File:** N/A
**Action:** Run

```bash
uv run pytest tests/integration/test_lifecycle_v04_manual.py -v
```

Must pass. Then run the broader integration suite:

```bash
uv run pytest tests/integration/
```

Must pass (FEAT-015 / T-302 regression check).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_v04_manual.py` | Modify | Add new integration test for multi-task `confirm_assignment` flow. |
| `tests/integration/_helpers.py` (if exists) | Modify (maybe) | Extend helpers only if existing ones don't cover memory readback. |

## Edge Cases & Risks
- **Polling timeouts:** the pause→running flip from a signal arrival is sub-second, but database polling for status may take a tick. The existing T-302 helpers handle this — reuse, don't roll new timing logic.
- **Memory readback in tests:** the assignments sidecar lives in `RunMemory.data` (JSONB). Read via `select(RunMemory).where(...)` and inspect `.data`, not via the `LifecycleMemory` parser (which strips top-level keys).
- **LLM stub scripting:** the stub must script three LLM calls per task (load_work_item or generate_tasks once, generate_plan twice, the human reviewer replaces the LLM review so no LLM review call is needed). Adjust scripting based on existing T-302 conventions.
- **Engine stub bookkeeping:** the test asserts engine T5 fires *after* the assignment signal. The stub must track call order or timestamps to verify ordering. If the existing stub doesn't, `engine_stub.call_log` or equivalent.
- **`Run.status='paused'` assertion (per IMP-002):** the integration test asserts the live status flag — this catches both IMP-002 regression and IMP-004's hook into it.
- **Test parallelization:** if `tests/integration/` runs serially with a shared Postgres, no concern. If parallel, ensure the test uses an isolated run id (it does — the API generates the id).

## Acceptance Verification
- [ ] Test passes end-to-end against real Postgres + stubbed LLM + stubbed engine.
- [ ] All five behavioral assertions (IMP-004 §9) pass.
- [ ] Removing `confirm_assignment` from the YAML causes the test to fail (manual regression check during review).
- [ ] Removing the `mark_task_done.branch."true": confirm_assignment` edit causes the second-task assertion to fail (manual regression check).
- [ ] Replacing the merge-preserve logic in T-305 with a naive overwrite causes assertion 10 (`memory["assignments"] == {"task-1": "alice", "task-2": "bob"}`) to fail.
- [ ] Full `tests/integration/` suite passes — no FEAT-015 regression.
- [ ] No explicit `sleep()` calls or timeout tolerances padded beyond what existing T-302 helpers use.
