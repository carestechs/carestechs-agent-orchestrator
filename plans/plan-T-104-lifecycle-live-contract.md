# Implementation Plan: T-104 — `@pytest.mark.live` contract test against real Anthropic (AC-11)

## Task Reference
- **Task ID:** T-104
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-102

## Overview
A guarded live test that drives the lifecycle agent against the real Anthropic API against the fixture work item. Skipped unless `--run-live` is passed AND `ANTHROPIC_API_KEY` is set. Structural assertions only — we don't assert on content quality (real-model output varies).

## Steps

### 1. Create `tests/contract/test_lifecycle_agent_live.py`
```python
import asyncio
import pytest

pytestmark = pytest.mark.live  # reuse FEAT-003's gating infrastructure

async def test_lifecycle_agent_live_anthropic(tmp_path, monkeypatch):
    """Drive the lifecycle agent against the real Anthropic API.

    Gated by pytest.mark.live (off by default). Requires ANTHROPIC_API_KEY.
    Cost: ~$0.20 per run (rough estimate across 8 policy calls + small prompts).
    This is the drift detector for T-102's recorded fixtures.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    repo = _prepare_tmp_repo(tmp_path)  # helper from T-101
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    monkeypatch.setattr("app.modules.ai.tools.lifecycle.git.get_diff",
                        lambda *a, **k: "diff --git a/x b/x\n+real world diff\n")

    # Background operator-simulator: poll for pause, POST the signal.
    signal_task_started = asyncio.Event()
    async def simulate_operator(run_id: UUID):
        signal_task_started.set()
        await _wait_until_paused(run_id, task_id="T-FIXTURE", timeout=60.0)
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            client.headers["Authorization"] = f"Bearer {API_KEY}"
            await client.post(f"/api/v1/runs/{run_id}/signals", json={
                "name": "implementation-complete", "task_id": "T-FIXTURE",
            })

    # Start the run.
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {API_KEY}"
        start = await client.post("/api/v1/runs", json={
            "agentRef": "lifecycle-agent@0.1.0",
            "intake": {"workItemPath": "docs/work-items/IMP-fixture.md"},
        })
        assert start.status_code == 202
        run_id = UUID(start.json()["data"]["id"])

    asyncio.create_task(simulate_operator(run_id))
    await signal_task_started.wait()

    await _wait_for_terminal(run_id, timeout=120.0)

    run = await repository.get_run_by_id(run_id)
    assert run.status == RunStatus.COMPLETED
    # Structural asserts only — content varies.
    assert (repo / "tasks" / "IMP-fixture-tasks.md").is_file()
    assert list((repo / "plans").glob("plan-T-*.md"))
    assert (repo / "tasks" / "IMP-fixture-tasks.md").stat().st_size > 100
```

### 2. Verify gating
Check `pyproject.toml` `[tool.pytest.ini_options]` markers include `live` (added in FEAT-003). Check `conftest.py` or equivalent handles `--run-live` to enable `live`-marked tests. If FEAT-003's `tests/contract/` directory doesn't exist or `--run-live` isn't wired, extend `conftest.py` with:
```python
def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="Run live-API contract tests.")

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        skip_live = pytest.mark.skip(reason="live tests disabled; pass --run-live")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)
```

### 3. Document cost + guard in CLAUDE.md
T-106's docs sweep adds a note under Testing Conventions about `--run-live` cost. For now, the docstring in the test is sufficient.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/contract/test_lifecycle_agent_live.py` | Create | One live contract test. |
| `pyproject.toml` | Modify (maybe) | Ensure `live` marker registered. |
| `tests/conftest.py` | Modify (maybe) | Ensure `--run-live` gating. |

## Edge Cases & Risks
- **Timeouts**: a real Anthropic run can take 30+ seconds across 8 policy calls. The 120-second terminal wait is generous; tune down if runs consistently complete faster.
- **Flakiness from model variance**: different Claude responses across runs. Structural asserts (files exist, non-empty) are robust; content assertions would be flaky. Do NOT add content assertions.
- **API key in test env**: test reads `ANTHROPIC_API_KEY`; ensure CI's `--run-live` job has the secret available as an env var, not baked into a fixture.
- **Cost accrual**: each test run consumes tokens. Estimate based on prompt size × 8 stages ≈ ~50k tokens input + 2k output → ~$0.20. Document so operators don't run it casually.
- **Operator simulator race**: the background task starts before the run starts; wait on an `asyncio.Event` to sequence correctly.
- **Review prompt behavior**: if `lifecycle-review.md` (T-100) confuses real Claude, the review may fail, the run may loop on corrections, and hit the bound. The test doesn't assert verdict — only completion. If the bound trips, the test fails with `stop_reason=error`, signaling a prompt-engineering issue to fix iteratively.

## Acceptance Verification
- [ ] Test marker `live` applied; test is skipped by default.
- [ ] With `--run-live` + `ANTHROPIC_API_KEY`: test runs end-to-end against real API.
- [ ] Run reaches `RunStatus.COMPLETED`.
- [ ] Artifact files exist and are non-empty.
- [ ] Operator-simulator POSTs via the real endpoint (not direct supervisor).
- [ ] Docstring explains the cost + guard.
- [ ] `uv run pytest tests/contract/ -v` without `--run-live` → skipped; with `--run-live` + key → green.
