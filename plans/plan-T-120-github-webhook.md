# Implementation Plan: T-120 — GitHub PR webhook ingress

## Task Reference
- **Task ID:** T-120
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-111, T-118

## Overview
New webhook endpoint at `/hooks/github/pr`. Verifies `X-Hub-Signature-256`, persists the event (even on bad signature), and — when a PR body matches `closes T-NNN` / `orchestrator: T-NNN` — invokes the S11 transition for the referenced task.

## Steps

### 1. Modify `src/app/config.py`
- Add `github_webhook_secret: SecretStr | None = Field(default=None, alias="GITHUB_WEBHOOK_SECRET")`.

### 2. Create `src/app/modules/ai/webhooks/__init__.py`
- Empty.

### 3. Create `src/app/modules/ai/webhooks/github.py`
- Helpers:
  - `def verify_github_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool` using `hmac.compare_digest`.
  - `def extract_task_reference(title: str, body: str) -> str | None` regex `r"(?:closes|orchestrator:)\s+(T-\d+)"` case-insensitive; returns first match or `None`.
  - Pydantic model `GitHubPrEvent` mirroring the subset of fields used (action, number, title, body, head.sha, merged).

### 4. Modify `src/app/modules/ai/service.py`
- `async def handle_pr_webhook(session, raw_body, signature, delivery_id, event_type, parsed) -> tuple[WebhookEvent, UUID | None]`:
  - Verify signature → `signature_ok`.
  - Persist `WebhookEvent(source='github', signature_ok=..., dedupe_key=f"github:pr:{parsed.number}:{delivery_id}", ...)`.
  - If `signature_ok=False` → return `(event, None)` (caller returns `401`).
  - If event_type != `pull_request` → return `(event, None)` with `202`.
  - Look up task by `extract_task_reference`; if not found, return `(event, None)` with `202`.
  - If `action in ('opened','reopened')` → call `submit_implementation_signal` (T-118) service adapter with the PR URL; transition task.
  - If `action == 'closed'` + `merged=True` and task not yet approved → append a trace entry "merged before approval" (audit-only).
  - Return `(event, matched_task_id)`.

### 5. Modify `src/app/modules/ai/router.py`
- `POST /hooks/github/pr`:
  - Read raw body, headers `X-Hub-Signature-256`, `X-GitHub-Event`, `X-GitHub-Delivery`.
  - Call `handle_pr_webhook`.
  - Return `202 { data: { received: true, eventId, matchedTaskId } }` on success; `401` on bad signature.

### 6. Create `tests/fixtures/github/pr_opened.json`
- Minimal recorded payload (sanitized).

### 7. Create `tests/modules/ai/test_webhooks_github.py`
- Signature verification: valid → `202`; invalid → `401`, event persisted with `signature_ok=false`.
- Non-pull_request event → `202`, no transition.
- PR body with `closes T-042` → task transitions `implementing → impl_review`.
- PR body without T-NNN → `202`, `matchedTaskId=null`, no transition.
- Dedupe: replay same delivery → no duplicate `WebhookEvent`.
- Merge before approval → audit-trace entry; task state unchanged.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | `github_webhook_secret` field. |
| `src/app/modules/ai/webhooks/__init__.py` | Create | Package init. |
| `src/app/modules/ai/webhooks/github.py` | Create | Signature + regex + DTO. |
| `src/app/modules/ai/service.py` | Modify | `handle_pr_webhook`. |
| `src/app/modules/ai/router.py` | Modify | New webhook route. |
| `tests/fixtures/github/pr_opened.json` | Create | Payload fixture. |
| `tests/modules/ai/test_webhooks_github.py` | Create | Webhook tests. |

## Edge Cases & Risks
- **Constant-time comparison** — use `hmac.compare_digest`, never `==`.
- **Persist before processing** — always insert `WebhookEvent` first; processing errors don't lose the event.
- **Missing `GITHUB_WEBHOOK_SECRET` in config** — endpoint returns `503` with a clear message ("GitHub webhook not configured"); don't silently accept.
- **Delivery replay** — GitHub retries webhooks on non-2xx responses. Dedupe by `delivery_id` handles it.

## Acceptance Verification
- [ ] Endpoint verifies signature; bad signature → `401` with event persisted.
- [ ] `closes T-NNN` / `orchestrator: T-NNN` regex matches both forms.
- [ ] Dedupe by `github:pr:<number>:<delivery_id>`.
- [ ] On match + `opened`, S11 transition fires.
- [ ] On merge-before-approval, audit trace recorded.
- [ ] `uv run pyright`, `ruff`, webhook tests green.
