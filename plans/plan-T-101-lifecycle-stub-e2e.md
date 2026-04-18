# Implementation Plan: T-101 — End-to-end integration test — stub policy (AC-2)

## Task Reference
- **Task ID:** T-101
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-100

## Overview
Drive the lifecycle agent end-to-end with `LLM_PROVIDER=stub`. Scripted `StubLLMProvider` walks the 8-stage flow; test delivers the implementation signal via `supervisor.deliver_signal` directly (endpoint path is exercised by T-102). Proves AD-3 composition integrity for the lifecycle agent specifically — "remove the LLM → still runs, produces real artifacts."

## Steps

### 1. Create shared test helpers in `tests/integration/lifecycle_helpers.py`
Extract the rigging that T-101, T-102, T-103, T-104 all share:
```python
def prepare_tmp_repo(tmp_path: Path) -> Path:
    """Copy a minimal fixture work item + empty tasks/ + plans/ into tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs/work-items").mkdir(parents=True)
    (repo / "tasks").mkdir()
    (repo / "plans").mkdir()
    (repo / ".trace").mkdir()
    fixture_src = Path("tests/fixtures/work-items/IMP-fixture.md")
    (repo / "docs/work-items/IMP-fixture.md").write_text(fixture_src.read_text())
    # Also copy the agents dir + prompts dir so the loader resolves.
    shutil.copytree("agents", repo / "agents")
    shutil.copytree(".ai-framework/prompts", repo / ".ai-framework/prompts")
    return repo

async def wait_for_terminal(run_id: UUID, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        run = await repository.get_run_by_id(run_id)
        if run and run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"run {run_id} did not reach terminal in {timeout}s")

async def wait_until_paused(run_id: UUID, *, task_id: str, timeout: float = 3.0) -> None:
    """Wait until the run has an in_progress step at the implementation node."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        steps = await repository.list_steps(run_id)
        if any(s.node_name == "implementation" and s.status == StepStatus.IN_PROGRESS
               and s.engine_run_id is None for s in steps):
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"run {run_id} did not pause in {timeout}s")
```

### 2. Create `tests/integration/test_lifecycle_stub.py`
One test:
```python
MINIMAL_TASKS_DOC = """# Task Breakdown: IMP-fixture

### T-FIXTURE: Trivial change
...
"""

MINIMAL_PLAN_DOC = """# Implementation Plan: T-FIXTURE
## Overview
Fixture plan.
"""

async def test_lifecycle_agent_stub_end_to_end(tmp_path, monkeypatch):
    repo = prepare_tmp_repo(tmp_path)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setattr("app.modules.ai.tools.lifecycle.git.get_diff",
                        lambda *a, **k: "diff --git a/x b/x\n+hello\n")

    script = [
        ToolCall("load_work_item", {"path": "docs/work-items/IMP-fixture.md"}),
        ToolCall("generate_tasks", {"work_item_id": "IMP-fixture",
                                     "tasks_markdown": MINIMAL_TASKS_DOC}),
        ToolCall("assign_task", {"task_id": "T-FIXTURE"}),
        ToolCall("generate_plan", {"task_id": "T-FIXTURE",
                                    "plan_markdown": MINIMAL_PLAN_DOC}),
        ToolCall("wait_for_implementation", {"task_id": "T-FIXTURE"}),
        ToolCall("review_implementation", {"task_id": "T-FIXTURE",
                                            "verdict": "pass",
                                            "feedback": "looks good"}),
        ToolCall("close_work_item", {"work_item_id": "IMP-fixture"}),
    ]
    policy = StubLLMProvider(script=script)

    # First, mark the fixture's Status as "In Progress" — close_work_item requires it.
    brief_path = repo / "docs/work-items/IMP-fixture.md"
    brief_path.write_text(brief_path.read_text().replace("Status | Not Started", "Status | In Progress"))

    async def deliver_signal_when_paused():
        await wait_until_paused(run_id, task_id="T-FIXTURE")
        await supervisor.deliver_signal(run_id, "implementation-complete", "T-FIXTURE", {})

    run_id = await service.start_run(
        agent_ref="lifecycle-agent@0.1.0",
        intake={"workItemPath": "docs/work-items/IMP-fixture.md"},
        policy=policy,
    )
    asyncio.create_task(deliver_signal_when_paused())

    await wait_for_terminal(run_id, timeout=2.0)

    run = await repository.get_run_by_id(run_id)
    assert run.status == RunStatus.COMPLETED
    assert run.stop_reason == StopReason.DONE_NODE

    # Artifacts.
    assert (repo / "tasks" / "IMP-fixture-tasks.md").is_file()
    assert list((repo / "plans").glob("plan-T-FIXTURE-*.md")) == [...]  # at least one
    reviews = list((repo / "plans").glob("plan-T-FIXTURE-*-review-1.md"))
    assert len(reviews) == 1

    # Brief Status flipped.
    closed = (repo / "docs/work-items/IMP-fixture.md").read_text()
    assert "Status | Completed" in closed
    assert re.search(r"Completed \| 20\d\d-\d\d-\d\dT", closed)
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/lifecycle_helpers.py` | Create | Shared rigging (tmp repo, terminal-wait, pause-wait). |
| `tests/integration/test_lifecycle_stub.py` | Create | One AC-2 integration test. |
| `tests/fixtures/work-items/IMP-fixture.md` | Reuse | Fixture from T-090. |

## Edge Cases & Risks
- **`close_work_item` precondition**: the fixture's Status must be `In Progress` before the test runs. The test writes this in before starting the run. If the fixture file from T-090 has `Status: Not Started`, flip at the start of the test.
- **Stub script exhaustion**: if the policy's `chat_with_tools` is called more times than the script's length, it raises. That's a correctness indicator — the real runtime should make exactly 7 policy calls (one per scripted tool). Assert this in the test.
- **Signal race**: the test's background task waits for the pause step to appear before delivering. Polling every 20 ms gives ~100 poll iterations in 2 seconds — ample.
- **`get_diff` monkeypatch**: the fixture isn't in a real git repo. Monkeypatching the `get_diff` function in `tools/lifecycle/git.py` (NOT re-importing it elsewhere) works because all callers go through that module.
- **Temp-repo isolation**: `REPO_ROOT` env var override relies on `Settings` re-reading the env on instantiation. If `Settings` is cached as a module-level singleton, the test must explicitly reset it — check `app.config.get_settings.cache_clear()` or equivalent.

## Acceptance Verification
- [ ] Run terminates with `status=completed`, `stop_reason=done_node`.
- [ ] `tasks/IMP-fixture-tasks.md` exists.
- [ ] At least one `plans/plan-T-FIXTURE-*.md` exists.
- [ ] One review file `plans/plan-T-FIXTURE-*-review-1.md` exists.
- [ ] Brief's Status flipped to `Completed`; `Completed` row has ISO-8601 timestamp.
- [ ] Stub policy called exactly 7 times (`chat_with_tools`).
- [ ] Test completes in < 2 s wall-clock.
- [ ] `uv run pytest tests/integration/test_lifecycle_stub.py -v` green.
