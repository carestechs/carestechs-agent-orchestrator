# Implementation Plan: T-149 — Opt-in live smoke test

## Task Reference
- **Task ID:** T-149
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Catches real-GitHub auth-header drift, API-shape changes, and PAT scope misconfiguration that mocked tests cannot.

## Overview
A single `@pytest.mark.live` test that hits the real GitHub API. Guarded by `--run-live` (already in conftest) and two env vars. Uses a throwaway check name so it doesn't register as the production gate.

## Implementation Steps

### Step 1: Test module
**File:** `tests/contract/test_github_checks_live.py`
**Action:** Create

```python
import os
import pytest
import httpx
from app.modules.ai.github.auth import PatAuthStrategy
from app.modules.ai.github.checks import HttpxGitHubChecksClient
from app.modules.ai.github.pr_urls import parse_pr_url

pytestmark = pytest.mark.live

@pytest.mark.asyncio
async def test_pat_create_and_update_check():
    pat = os.getenv("GITHUB_PAT")
    pr_url = os.getenv("GITHUB_SMOKE_PR_URL")
    if not pat or not pr_url:
        pytest.skip("set GITHUB_PAT + GITHUB_SMOKE_PR_URL to enable")
    ref = parse_pr_url(pr_url)
    async with httpx.AsyncClient(timeout=30.0) as http:
        # Look up the PR's head sha — required for check-run registration.
        headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
        pr = (await http.get(
            f"https://api.github.com/repos/{ref.owner}/{ref.repo}/pulls/{ref.pull_number}",
            headers=headers,
        )).json()
        head_sha = pr["head"]["sha"]

        client = HttpxGitHubChecksClient(auth=PatAuthStrategy(pat), http=http)
        check_id = await client.create_check(
            owner=ref.owner, repo=ref.repo, head_sha=head_sha,
            name="orchestrator/smoke-test",  # distinct from the prod gate name
        )
        assert check_id
        await client.update_check(
            owner=ref.owner, repo=ref.repo, check_id=check_id, conclusion="success",
        )
```

Uses a distinct `name="orchestrator/smoke-test"` so live runs never interfere with branch protection gating on `orchestrator/impl-review`.

### Step 2: Document in README
**File:** `README.md`
**Action:** Modify

Under "Tests" (or the new FEAT-007 docs section T-150 will add), note:

```bash
export GITHUB_PAT=ghp_...
export GITHUB_SMOKE_PR_URL=https://github.com/you/scratch/pull/1
uv run pytest --run-live tests/contract/test_github_checks_live.py
```

### Step 3: CI guard (belt-and-suspenders)
**File:** `tests/contract/test_github_checks_live.py`
**Action:** Modify

Add a conftest-level check that refuses to run unless `--run-live` is set AND the env vars exist — so `--run-live` alone (which enables the FEAT-006 engine smoke) doesn't accidentally post to GitHub.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/contract/test_github_checks_live.py` | Create | Live smoke. |
| `README.md` | Modify | Invocation docs. |

## Edge Cases & Risks
- **PR must be open.** Check-runs can only be attached to open PRs with a resolvable head sha. If closed → GitHub returns 422. Document the prereq.
- **Rate limits.** One run = 3 API calls (PR lookup + create + update). Trivial.
- **Check-run accumulation.** Each live run creates a new check-run on the scratch PR. Expected; no cleanup.
- **Leaking the PAT.** Never echo the token in assertions or logs. Don't pass `-v` when demonstrating the test in docs.

## Acceptance Verification
- [ ] Test skips cleanly without `--run-live` or without env vars.
- [ ] With `--run-live` + env vars, creates + updates a check-run on the configured PR.
- [ ] Name used is `orchestrator/smoke-test` (not the prod gate).
- [ ] `uv run pytest tests/contract/test_github_checks_live.py` — skipped by default.
