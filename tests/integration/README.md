# Integration tests (FEAT-002)

These tests exercise the full orchestrator loop — scripted policy → real
runtime + supervisor + webhook receiver + reconciliation + JSONL trace —
without any external process.

## Shared infrastructure

* **`engine_echo.py`** — `EngineEcho`: a drop-in for `FlowEngineClient`
  that, on each `dispatch_node`, schedules an in-process webhook POST
  back at the same ASGI app.  Supports `delay_seconds` (T-055),
  `fail_on_step_number` (T-056), and a custom `payload_for` callable to
  shape `node_result` payloads (T-054 uses the default).
* **`env.py`** — `integration_env(...)`: async context manager that
  builds a fresh FastAPI app, enters its lifespan (so
  `app.state.supervisor` is wired), overrides the five production deps
  (`get_settings_dep`, `get_session_factory`, `get_llm_provider_dep`,
  `get_engine_client`, `get_trace_store`), and on exit drains the
  supervisor + cleans up seeded DB rows.
* **`env.poll_until_terminal(env, run_id, ...)`** — polls the run row
  until its status is terminal or the timeout fires; on timeout the
  error message dumps the dispatches/steps/webhook counts for triage.

## Why these tests don't use the `client`/`db_session` fixtures

The runtime loop opens its own `AsyncSession` per iteration via
`get_session_factory`, which is bound to the raw engine — not the
savepoint-wrapped connection the `db_session` fixture uses.  Running the
loop inside a savepoint would hide its writes from subsequent requests,
so integration tests drive the app with a fresh ASGI transport and
clean up by run id at teardown.

## Writing a new test

```python
@pytest.mark.asyncio(loop_scope="function")
async def test_my_scenario(engine, tmp_path, webhook_signer):
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[("analyze_brief", {"brief": "hi"}), ...],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
        resp = await env.client.post("/api/v1/runs", json={...}, headers=env.auth_headers)
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)  # ensures teardown cleans it
        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        ...
```

Put a `run_ids.append(run_id)` right after you learn the id — teardown
uses that list to DELETE the seeded rows.
