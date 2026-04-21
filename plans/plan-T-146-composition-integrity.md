# Implementation Plan: T-146 — Composition-integrity regression test

## Task Reference
- **Task ID:** T-146
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-6 — AD-9 composition integrity formalized for FEAT-007 (system must run without GitHub credentials).

## Overview
End-to-end test that drives all 14 FEAT-006 signals with `LLM_PROVIDER=stub` and no GitHub credentials. Proves the Noop path is selected and zero outbound GitHub HTTP fires.

## Implementation Steps

### Step 1: New test module
**File:** `tests/integration/test_feat007_composition_integrity.py`
**Action:** Create

Structure:

```python
@pytest.mark.asyncio
async def test_full_lifecycle_without_github_credentials(
    client: AsyncClient, db_session: AsyncSession, webhook_signer, respx_mock
):
    respx_mock.route(host="api.github.com").mock(
        side_effect=AssertionError("GitHub must not be called in composition-integrity mode")
    )
    # Drive the 14 signals in order, exactly as test_feat006_end_to_end does.
    ...
    # After: assert no respx calls on api.github.com.
    assert all(c.request.url.host != "api.github.com" for c in respx_mock.calls)
```

The 14-signal sequence:
1. `POST /api/v1/items` (create work item)
2. `POST /work-items/{id}/brief-approved` (W1)
3. *(reactor runs task-generation dispatch; stub returns fixed tasks)*
4. For each task: `assign` (T1), `assign-approved` (T2), `plan-submitted` (T5), `plan-approved` (T6), `implementation-submitted` (T7), `impl-review-approved` (T12).
5. `work-item-ready` derivation (W5) fires automatically.

Reuse helpers from `tests/integration/test_feat006_lifecycle.py` if present; otherwise factor shared scaffolding into `tests/integration/_lifecycle_helpers.py` first.

### Step 2: Assert Noop client is picked
**File:** `tests/integration/test_feat007_composition_integrity.py`
**Action:** Modify

```python
from app.core.github import get_github_checks_client
from app.modules.ai.github.checks import NoopGitHubChecksClient
settings = get_settings()
assert isinstance(get_github_checks_client(settings), NoopGitHubChecksClient)
```

### Step 3: Document in README
**File:** `README.md`
**Action:** Modify

Add a sentence under the existing "Tests" section noting the composition-integrity guarantee ("the suite runs with no GitHub credentials; adding them is opt-in").

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_feat007_composition_integrity.py` | Create | Full 14-signal no-credentials flow. |
| `tests/integration/_lifecycle_helpers.py` | Create (if absent) | Shared signal-driving helpers. |
| `README.md` | Modify | Note the composition-integrity guarantee. |

## Edge Cases & Risks
- **Env leak.** Some dev `.env` files set `GITHUB_PAT`. Test must force Settings override via the existing `_test_env` fixture pattern — explicitly set all three GitHub env vars to `""` / `None`.
- **Slow test.** Driving 14 signals in one test can balloon to ~seconds. Keep it in the integration suite; don't mark as `fast`.
- **Fixture reuse.** If `test_feat006_lifecycle.py` already drives all 14 signals, this test can call that helper + add the GitHub-call assertion. Prefer extraction over duplication.

## Acceptance Verification
- [ ] Test passes with zero `api.github.com` calls asserted.
- [ ] `NoopGitHubChecksClient` is the resolved implementation.
- [ ] Work item reaches `ready`; all tasks reach terminal states.
- [ ] `uv run pytest tests/integration/test_feat007_composition_integrity.py` green.
