# Implementation Plan: T-084 — End-to-end follow-mode streaming test

## Task Reference
- **Task ID:** T-084
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-080

## Overview
Single high-value integration test that proves AC-2 end-to-end: start a multi-step run, open `?follow=true` **before the run terminates**, and verify the stream sees every trace line in real time and closes cleanly once the run reaches terminal state.

## Steps

### 1. Create `tests/integration/test_trace_stream_follow.py`
Test body:

```python
@pytest.mark.asyncio(loop_scope="function")
async def test_follow_stream_captures_live_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    fast_tail_poll: None,   # from tests/integration/conftest.py (T-083)
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
            ("review_plan", {}),
        ],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        engine_delay_seconds=0.3,  # run takes ~1 s overall
    ) as env:
        # Start the run.
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        # Open the follow stream IMMEDIATELY — before the run terminates.
        collected: list[dict[str, Any]] = []

        async def _collect_stream() -> None:
            async with env.client.stream(
                "GET",
                f"/api/v1/runs/{run_id}/trace",
                params={"follow": "true"},
                headers=env.auth_headers,
                timeout=10.0,
            ) as resp:
                assert resp.status_code == 200
                async for raw in resp.aiter_lines():
                    if not raw:
                        continue
                    record = json.loads(raw)
                    collected.append(record)

        # Run the collector concurrently with the run's own lifecycle.
        await asyncio.wait_for(_collect_stream(), timeout=5.0)

        # Stream has closed — run should be terminal.
        run = await poll_until_terminal(env, run_id, timeout_seconds=1.0)
        assert run.status == RunStatus.COMPLETED

        # Every record kind observed; at least 3 steps + 3 policy calls + 3 webhooks.
        kinds = [r["kind"] for r in collected]
        assert kinds.count("step") >= 3
        assert kinds.count("policy_call") >= 3
        assert kinds.count("webhook_event") >= 3
```

Key points:
- `_collect_stream()` runs to completion (it blocks on the stream until the server closes it).  The outer `asyncio.wait_for(..., timeout=5.0)` is the test's safety bound.
- We don't spawn the collector as a `create_task` — the stream naturally blocks on the runtime's pace, and the runtime runs concurrently on the same event loop because `integration_env` spawned the `run_loop` as a supervised task at `POST /api/v1/runs`.
- The collector's `async for` completes when the server closes the response (service.stream_trace's terminal-state close detection).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_trace_stream_follow.py` | Create | One end-to-end follow test. |

## Edge Cases & Risks
- Timing: the `engine_delay_seconds=0.3` makes a 3-step run take ~1 s wall-clock.  The stream's `asyncio.wait_for(..., timeout=5.0)` is generous.
- `fast_tail_poll` (from T-083's conftest addition) keeps service + trace-store poll cadences at 10 ms, so the terminal-close detection fires within ~20 ms of the run ending.
- The `env.client` is `AsyncClient(transport=ASGITransport(app=app))`.  Each request + the supervised loop task all share the same event loop — there is no real wall-clock concurrency, but cooperatively they interleave.  Start the POST to kick off the supervisor task, then open the stream; the runtime's `await supervisor.await_wake(...)` yields and the stream gets its share of turns.
- If the test flakes, raise `engine_delay_seconds` to `1.0` and the outer timeout to `10.0`.  Don't remove the bound — a hung stream test is worse than a mildly slow one.

## Acceptance Verification
- [ ] Test passes deterministically within 5 s wall-clock.
- [ ] Every trace kind appears at least 3 times in the collected stream.
- [ ] Stream iterator completes (the `async for` exits) once the run is terminal.
- [ ] No unraisable exception warnings in the test output.
