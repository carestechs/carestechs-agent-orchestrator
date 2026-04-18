# Implementation Plan: T-060 — Integration: control-plane real-data tests (AC-5)

## Task Reference
- **Task ID:** T-060
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-041, T-043, T-044, T-054

## Overview
Run a real end-to-end run (reusing T-054's infrastructure), then query each control-plane read endpoint. Assert envelope shapes, pagination `meta`, DTO camelCase, last-step summary correctness.

## Steps

### 1. Create `tests/integration/test_control_plane_real.py`

Shared `seeded_completed_run` fixture:
- Uses `EngineEcho` + scripted stub policy.
- Starts run, polls to completion.
- Returns the completed `run_id` + associated rows (via direct DB session) so tests can compare.

Tests:

```python
async def test_list_runs_returns_completed_run(client, auth_headers, seeded_completed_run):
    resp = await client.get("/api/v1/runs", headers=auth_headers)
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"][0]["id"] == str(seeded_completed_run.run_id)
    assert body["meta"]["totalCount"] == 1
    assert body["data"][0]["status"] == "completed"

async def test_list_runs_filter_by_status(...):
    # Seed a completed + a cancelled run.
    # GET /runs?status=cancelled → returns only the cancelled one.

async def test_list_runs_filter_by_agent_ref(...):
    # Same pattern with two agentRefs.

async def test_get_run_detail_has_last_step(...):
    # Last step's fields populated; step_count matches actual count.

async def test_list_steps_paginated(...):
    # Seed a 3-step run.
    # page_size=2 → 2 pages, total=3.
    # Asserts snake→camel: stepNumber, nodeName etc.

async def test_list_policy_calls_ordered(...):
    # Assert order is ASC by created_at.

async def test_list_agents_returns_yaml_content(...):
    # Seed AGENTS_DIR with sample-linear.yaml.
    # GET /api/v1/agents → 1 entry, fields match.
```

### 2. Assert envelope shape

Factor out a helper:
```python
def assert_envelope(body: dict, *, has_meta: bool, is_collection: bool) -> None:
    assert "data" in body
    if has_meta:
        assert "meta" in body
        assert {"totalCount", "page", "pageSize"}.issubset(body["meta"].keys())
    if is_collection:
        assert isinstance(body["data"], list)
```

Use in every test.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_control_plane_real.py` | Create | 7+ endpoint integration tests. |

## Edge Cases & Risks
- Tests depend on T-054's `EngineEcho`; shared fixtures live in `tests/integration/` helpers.
- Clean state between tests: SAVEPOINT rollback from `db_session` fixture handles Run/Step/etc. rollback. But `.trace/<run_id>.jsonl` files on disk are NOT rolled back — use `tmp_path` for `trace_dir` in integration tests.

## Acceptance Verification
- [ ] Every listed endpoint has ≥1 test.
- [ ] Envelope shape validated uniformly.
- [ ] Pagination meta fields all present + accurate.
- [ ] camelCase aliases appear in JSON payloads.
- [ ] `last_step` correctness asserted explicitly.
