# Implementation Plan: T-054 — End-to-end composition integrity (AC-1 + AC-6 headliner)

## Task Reference
- **Task ID:** T-054
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-039, T-040, T-045, all building blocks

## Overview
The AD-3 success-metric test. A scripted `StubLLMProvider` + `respx`-mocked engine + real webhook receiver drives a full run to `stop_reason=done_node` with deterministic step/policy-call sequences and a readable JSONL trace.

## Steps

### 1. Create helper fixture `tests/integration/engine_echo.py`

Shared helper that, when the engine client POSTs to `/nodes/dispatch`, responds 200 + triggers a synthetic webhook back to our app's own `/hooks/engine/events` via an internal `AsyncClient` that shares the ASGI transport.

- Maintain a dict `engine_run_id → (run_id, step_id)` so webhooks carry the right correlation.
- Configurable delays (`delay_seconds` param) so tests can simulate slow engines.
- Configurable failure mode (`fail_on_step_number`) to inject a 500 on a specific dispatch (used by T-056).

### 2. Create `tests/integration/test_run_end_to_end.py`

```python
@pytest.mark.asyncio(loop_scope="function")
async def test_linear_agent_completes_with_done_node(
    app, client, db_session, webhook_signer, stub_policy_factory, tmp_path, monkeypatch
):
    # 1. Copy sample-linear.yaml to a tmp AGENTS_DIR; monkeypatch Settings.agents_dir.
    # 2. Wire stub policy:
    #      [ ("analyze_brief", {"brief": "hi"}),
    #        ("draft_plan", {}),
    #        ("review_plan", {}) ]  # review_plan is terminal
    # 3. Override get_llm_provider to return the stub.
    # 4. Override get_engine_client to return EngineEcho(... auto-webhook ...).
    # 5. POST /api/v1/runs → capture runId.
    # 6. Poll GET /api/v1/runs/{id} until status is terminal (max 5 s).
    # 7. Assertions:
    #      - run.status == completed, stop_reason == done_node
    #      - 3 Step rows with node_name sequence == ["analyze_brief","draft_plan","review_plan"]
    #      - 3 PolicyCall rows
    #      - RunMemory.data contains merged node results
    #      - .trace/<run_id>.jsonl has ≥ 3 + 3 + 3 lines (steps + policy + webhook events)
    #      - JSONL round-trip: reads back into Step/PolicyCall/WebhookEvent DTOs successfully
```

Plus a variant:
```python
async def test_exhausted_script_surfaces_provider_error(...):
    # stub policy with empty script -> ProviderError -> stop_reason=error
```

### 3. Document the pattern in a README inside `tests/integration/`

Short `tests/integration/README.md` explaining how the echo-engine + ASGI webhook round-trip works, so FEAT-003/004 can reuse the pattern without rediscovering it.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/engine_echo.py` | Create | ASGI-aware engine mock that fires webhooks back. |
| `tests/integration/test_run_end_to_end.py` | Create | The AD-3 composition-integrity test. |
| `tests/integration/README.md` | Create | Pattern documentation. |

## Edge Cases & Risks
- Timing: `asyncio.gather` between the engine mock's webhook POST and the main loop's wake can introduce race conditions. Use `asyncio.sleep(0)` yield points inside the echo to cede to the loop.
- The test creates real DB rows via real routes — make sure it cleans up (SAVEPOINT rollback from `db_session` fixture does this automatically, but document the contract).
- 5-second wait is generous for local CI; document it as the upper bound.

## Acceptance Verification
- [ ] Test completes deterministically < 5 s.
- [ ] Re-run produces byte-identical `node_name` sequence.
- [ ] JSONL file parses back to valid DTOs.
- [ ] Exhausted-script variant surfaces `ProviderError`.
- [ ] Documentation added so FEAT-003 can reuse.
