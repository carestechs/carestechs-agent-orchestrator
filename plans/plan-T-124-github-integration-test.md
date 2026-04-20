# Implementation Plan: T-124 — GitHub integration test (mocked Checks + recorded webhook)

## Task Reference
- **Task ID:** T-124
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-120, T-121

## Overview
End-to-end test exercising the full GitHub integration: PR webhook → S11 transition → check created; review approve → check `success`; review reject → check `failure`. Uses `respx` to mock the GitHub API and recorded webhook payloads.

## Steps

### 1. Create `tests/fixtures/github/pr_closed_merged.json` and `pr_closed_unmerged.json`
- Sibling fixtures to `pr_opened.json` (from T-120).

### 2. Create `tests/integration/test_github_integration.py`
- Helper: `sign_github_payload(body: bytes, secret: str) -> str` emitting `sha256=<hex>`.
- Configure app with `GITHUB_WEBHOOK_SECRET="test"` and a real (non-noop) `HttpxGitHubChecksClient` backed by `respx` mocks.
- Scenarios:
  - **Happy path:** webhook (pr_opened) → expect `POST /repos/{repo}/check-runs` called once with `status=in_progress`; call `/review/approve` → expect `PATCH /check-runs/{id}` with `conclusion=success`.
  - **Reject path:** webhook → `/review/reject` → `PATCH` with `conclusion=failure`.
  - **Bad signature:** post pr_opened with wrong signature → `401`; `WebhookEvent` persisted with `signature_ok=false`; no GitHub API calls.
  - **Unmatched PR:** webhook body without `T-NNN` → `202`, `matchedTaskId=null`; no GitHub API calls; no task transition.
  - **Merge before approval:** webhook `pr_closed_merged` without prior approval → `202`; trace contains "merged before approval" entry.
  - **Replay:** same delivery id twice → `202` both times; Check call fires once.
- Use `respx` `assert_all_called=True` to catch extra calls.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/fixtures/github/pr_closed_merged.json` | Create | Fixture. |
| `tests/fixtures/github/pr_closed_unmerged.json` | Create | Fixture. |
| `tests/integration/test_github_integration.py` | Create | Scenarios. |

## Edge Cases & Risks
- **Fixture sanitization** — recorded webhook payloads may contain real repo names, user ids. Replace with `"owner/repo"`, `"test-user"` before committing.
- **Signature helper** — don't copy it from production code; write a minimal test-local helper so a bug in the production helper doesn't hide behind an identical bug in the test helper.
- **respx strict mode** — enable `assert_all_called=True` and `assert_all_mocked=True` to prevent hidden dependency on unmocked routes.

## Acceptance Verification
- [ ] Three PR fixtures present and sanitized.
- [ ] Happy path: 1 create + 1 update(success).
- [ ] Reject path: 1 create + 1 update(failure).
- [ ] Bad signature: `401`, no API calls.
- [ ] Unmatched PR: `202`, no transition, no API calls.
- [ ] Merge-before-approval: audit trace recorded.
- [ ] Replay: 1 API call across 2 deliveries.
- [ ] `uv run pytest tests/integration/test_github_integration.py` green.
