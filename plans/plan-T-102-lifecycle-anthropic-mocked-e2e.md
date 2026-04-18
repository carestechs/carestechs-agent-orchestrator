# Implementation Plan: T-102 — End-to-end integration test — Anthropic-mocked (AC-3, AC-4)

## Task Reference
- **Task ID:** T-102
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-098, T-100, T-101

## Overview
Drive the lifecycle agent end-to-end with `LLM_PROVIDER=anthropic`, `respx`-mocking the Anthropic Messages endpoint to return pre-recorded tool-call responses for each stage. The signal is delivered via the real `POST /runs/{id}/signals` endpoint (not a direct supervisor call) — this exercises the full AC-4 path.

## Steps

### 1. Capture fixtures (one-time)
Run a real Anthropic-backed lifecycle execution against the fixture brief (T-101's `IMP-fixture.md`) with VCR-like recording or by manually capturing the 8 Messages API responses. Save each as JSON under `tests/fixtures/anthropic/lifecycle/stage-<NN>-<name>.json`. Each fixture is a full Anthropic Messages response including the `content` array with a `tool_use` block.

### 2. Create `tests/integration/test_lifecycle_anthropic_mocked.py`
```python
async def test_lifecycle_agent_completes_with_anthropic_mocked(tmp_path, monkeypatch, respx_mock):
    # Set up repo + fixture brief (shared helper from T-101).
    repo = _prepare_tmp_repo(tmp_path)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    # Mock git.get_diff.
    monkeypatch.setattr("app.modules.ai.tools.lifecycle.git.get_diff",
                        lambda *a, **k: "diff --git a/x b/x\n+hello\n")

    # Load 8 stage fixtures in order.
    fixtures_dir = Path("tests/fixtures/anthropic/lifecycle")
    responses = [
        httpx.Response(200, json=json.loads((fixtures_dir / f).read_text()))
        for f in sorted(fixtures_dir.glob("stage-*.json"))
    ]
    respx_mock.post("https://api.anthropic.com/v1/messages").mock(side_effect=responses)

    # Start the run via the real control-plane endpoint.
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {API_KEY}"
        start_resp = await client.post("/api/v1/runs", json={
            "agentRef": "lifecycle-agent@0.1.0",
            "intake": {"workItemPath": "docs/work-items/IMP-fixture.md"},
        })
        assert start_resp.status_code == 202
        run_id = start_resp.json()["data"]["id"]

        # When the run pauses, POST the signal via the real endpoint.
        await _wait_until_paused(UUID(run_id), task_id="T-FIXTURE")
        signal_resp = await client.post(f"/api/v1/runs/{run_id}/signals", json={
            "name": "implementation-complete",
            "task_id": "T-FIXTURE",
            "payload": {"commit_sha": "abc1234"},
        })
        assert signal_resp.status_code == 202

        await _wait_for_terminal(UUID(run_id), timeout=5.0)

    # Assertions.
    run = await repository.get_run_by_id(UUID(run_id))
    assert run.status == RunStatus.COMPLETED

    # All artifact files exist.
    assert (repo / "tasks" / "IMP-fixture-tasks.md").is_file()
    assert list((repo / "plans").glob("plan-T-*.md"))

    # Trace contains at least one operator_signal entry.
    trace_path = repo / ".trace" / f"{run_id}.jsonl"
    lines = [json.loads(l) for l in trace_path.read_text().splitlines()]
    assert any(l["kind"] == "operator_signal" for l in lines)

    # Review-stage policy call's prompt_inputs includes the stubbed diff.
    review_calls = [l for l in lines
                    if l["kind"] == "policy_call"
                    and l["data"]["selectedTool"] == "review_implementation"]
    assert review_calls
    assert "hello" in json.dumps(review_calls[0]["data"]["promptContext"])
```

### 3. Save 8 fixture files
`tests/fixtures/anthropic/lifecycle/`:
- `stage-01-intake.json`
- `stage-02-task-generation.json`
- `stage-03-task-assignment.json`
- `stage-04-plan-creation.json`
- `stage-05-implementation.json`
- `stage-06-review.json`
- `stage-07-closure.json`
- `stage-08-terminate.json`

Each matches the Anthropic Messages API response shape with a single `tool_use` block; the `name` + `input` match the expected tool call for that stage.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_anthropic_mocked.py` | Create | End-to-end test. |
| `tests/fixtures/anthropic/lifecycle/stage-*.json` | Create | 8 fixture responses. |

## Edge Cases & Risks
- **Fixture drift**: if the real Anthropic API response shape changes, fixtures go stale silently — this test will pass but T-104's live test will catch the drift. Run T-104 before shipping.
- **Respx sequential-response ordering**: `side_effect=responses` pops one per call. Make sure exactly one `chat_with_tools` call is made per stage (the runtime does this by contract). If a retry fires (shouldn't — fixtures are 200s), an extra call will consume the wrong fixture. Assertion: `respx_mock.calls.call_count == 8` at the end.
- **`current_task_id` sequencing**: the mocked policy must call `wait_for_implementation` before `review_implementation` for the same task — reflected in the fixture ordering.
- **Trace file location**: `.trace/<run_id>.jsonl` under `repo_root`. Confirm the writer honors `repo_root` via Settings; tests that change repo_root must also influence trace path.
- **Cost-free**: respx intercepts all outbound httpx calls — no real Anthropic usage.

## Acceptance Verification
- [ ] Run completes with `RunStatus.COMPLETED`.
- [ ] All expected artifact files exist in the tmp repo.
- [ ] Trace file contains ≥ 1 `operator_signal` kind entry.
- [ ] Review-stage `policy_call.promptContext` includes the stubbed diff text.
- [ ] Test completes in < 5 s wall-clock.
- [ ] Respx assertion: exactly 8 outbound calls to Anthropic.
- [ ] `uv run pytest tests/integration/test_lifecycle_anthropic_mocked.py -v` green.
