# Implementation Plan: T-103 — Integration test — correction-bound trip (AC-6)

## Task Reference
- **Task ID:** T-103
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-097, T-101

## Overview
End-to-end integration test for AC-6. Scripted stub policy yields three consecutive `review_implementation(fail)` verdicts; the run terminates with `stop_reason=error`, `final_state.reason="correction_budget_exceeded"`, `final_state.attempts=3`.

## Steps

### 1. Create `tests/integration/test_lifecycle_corrections.py`
One test, building on T-101's infrastructure:
```python
async def test_correction_budget_exceeded_terminates_run(tmp_path, monkeypatch):
    repo = _prepare_tmp_repo(tmp_path)  # helper from T-101
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LIFECYCLE_MAX_CORRECTIONS", "2")

    monkeypatch.setattr("app.modules.ai.tools.lifecycle.git.get_diff",
                        lambda *a, **k: "diff --git a/x b/x\n+hello\n")

    # Scripted policy: intake → task-gen → assign → plan → pause → review(fail)
    #                  → corrections → pause → review(fail)
    #                  → corrections → pause → review(fail)
    #                  → [termination; no more calls]
    script = [
        ToolCall("load_work_item", {"path": str(repo / "docs/work-items/IMP-fixture.md")}),
        ToolCall("generate_tasks", {"work_item_id": "IMP-fixture", "tasks_markdown": MINIMAL_TASKS_DOC}),
        ToolCall("assign_task", {"task_id": "T-FIXTURE"}),
        ToolCall("generate_plan", {"task_id": "T-FIXTURE", "plan_markdown": MINIMAL_PLAN_DOC}),
        ToolCall("wait_for_implementation", {"task_id": "T-FIXTURE"}),
        ToolCall("review_implementation", {"task_id": "T-FIXTURE", "verdict": "fail", "feedback": "no"}),
        ToolCall("wait_for_implementation", {"task_id": "T-FIXTURE"}),  # after corrections
        ToolCall("review_implementation", {"task_id": "T-FIXTURE", "verdict": "fail", "feedback": "still no"}),
        ToolCall("wait_for_implementation", {"task_id": "T-FIXTURE"}),
        ToolCall("review_implementation", {"task_id": "T-FIXTURE", "verdict": "fail", "feedback": "still no"}),
        # runtime should terminate here — no more calls needed.
    ]
    policy = StubLLMProvider(script=script)

    # Background task: deliver signal on every pause.
    async def deliver_signals():
        for _ in range(3):
            await _wait_until_paused(run_id, task_id="T-FIXTURE")
            await supervisor.deliver_signal(run_id, "implementation-complete", "T-FIXTURE", {})
            await asyncio.sleep(0.05)  # let runtime move past pause
    asyncio.create_task(deliver_signals())

    run_id = await service.start_run(agent_ref="lifecycle-agent@0.1.0",
                                      intake={"workItemPath": "docs/work-items/IMP-fixture.md"},
                                      policy=policy)

    await _wait_for_terminal(run_id, timeout=3.0)

    run = await repository.get_run_by_id(run_id)
    assert run.status == RunStatus.FAILED
    assert run.stop_reason == StopReason.ERROR
    assert run.final_state["reason"] == "correction_budget_exceeded"
    assert run.final_state["task_id"] == "T-FIXTURE"
    assert run.final_state["attempts"] == 3

    # Three review files exist.
    reviews = list((repo / "plans").glob("plan-T-FIXTURE-*-review-*.md"))
    assert len(reviews) == 3

    # close_work_item never ran.
    brief_body = (repo / "docs/work-items/IMP-fixture.md").read_text()
    assert "Status | In Progress" in brief_body
    assert "Status | Completed" not in brief_body
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_corrections.py` | Create | One end-to-end correction-bound test. |

## Edge Cases & Risks
- **When does the bound trip, exactly?** After the third `corrections` entry's increment pushes the counter to 3, the `correction_budget_exceeded` check returns `StopReason.ERROR`. The third `review_implementation` itself runs first (the review is what triggers the subsequent `corrections` entry).
- **Signal-deliver race**: the test's background task delivers three signals in sequence. `_wait_until_paused` polls for the pause step; if the runtime moves past the pause before the test sees `in_progress`, the test races. The `asyncio.sleep(0.05)` after each delivery gives the runtime time to transition the step out of `in_progress`. Tune if flaky.
- **Counter semantics**: increment on *entering* corrections node, NOT on failing review. Counter goes 1 → 2 → 3 across three corrections entries; threshold comparison is `> max_corrections` (= > 2), so attempt 3 trips.
- **Script length**: 10 calls total. If the stub policy exhausts its script before termination, it throws — verify the last scripted call is the one that triggers the bound, not a subsequent call.
- **`final_state` shape**: the runtime's `_terminate` must write these three keys. Verify T-097 wires them correctly; if not, this test will surface the gap.

## Acceptance Verification
- [ ] Run terminates with `stop_reason=error`.
- [ ] `final_state.reason == "correction_budget_exceeded"`.
- [ ] `final_state.task_id == "T-FIXTURE"`, `final_state.attempts == 3`.
- [ ] Three review markdown files exist.
- [ ] Work item Status NOT flipped.
- [ ] Test completes in < 2 s.
- [ ] `uv run pytest tests/integration/test_lifecycle_corrections.py -v` green.
