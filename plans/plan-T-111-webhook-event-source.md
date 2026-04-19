# Implementation Plan: T-111 — Extend `WebhookEvent` with `source` + GitHub event types

## Task Reference
- **Task ID:** T-111
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Add a `source` column (`engine` default; `github` for GitHub PR webhooks) to `webhook_events`. Extend the `event_type` check constraint with `github_pr_opened` / `github_pr_closed`. Extend the dedupe-key helper to emit a source-aware shape.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Add enum:
  ```python
  class WebhookSource(StrEnum): ENGINE="engine"; GITHUB="github"
  ```
- Extend existing `WebhookEventType` (likely a `StrEnum`) with `GITHUB_PR_OPENED`, `GITHUB_PR_CLOSED`.
- Add `source: WebhookSource` to `WebhookEventDto`.

### 2. Modify `src/app/modules/ai/models.py`
- Add column: `source: Mapped[str] = mapped_column(String, nullable=False, server_default="engine")`.
- Drop the existing check constraint on `event_type` and recreate with the two new values included.
- No new indexes required.

### 3. Create `src/app/migrations/versions/<ts>_webhook_event_source.py`
- Manual migration (autogenerate may miss the constraint swap):
  ```python
  op.add_column("webhook_events", sa.Column("source", sa.String(), nullable=False, server_default="engine"))
  op.drop_constraint("ck_webhook_events_event_type", "webhook_events", type_="check")
  op.create_check_constraint("ck_webhook_events_event_type", "webhook_events",
      "event_type IN ('node_started','node_finished','node_failed','flow_terminated','github_pr_opened','github_pr_closed')")
  op.create_check_constraint("ck_webhook_events_source", "webhook_events",
      "source IN ('engine','github')")
  ```
- `downgrade` reverses both.

### 4. Modify `src/app/modules/ai/repository.py`
- Extend `compute_webhook_dedupe_key(...)` (or equivalent) to branch on a new `source` argument:
  - `engine` (existing shape): `f"{engine_run_id}:{event_type}:{engine_event_id}"`
  - `github`: `f"github:pr:{pr_number}:{delivery_id}"`

### 5. Modify `tests/modules/ai/test_repository.py`
- Dedupe-key shape tests for both sources.

### 6. Modify `tests/modules/ai/test_webhooks.py` (if exists)
- Confirm existing engine-path tests pass unchanged (default `source='engine'`).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | `WebhookSource` + extended `WebhookEventType`. |
| `src/app/modules/ai/models.py` | Modify | New `source` column. |
| `src/app/modules/ai/repository.py` | Modify | Source-aware dedupe. |
| `src/app/migrations/versions/<ts>_webhook_event_source.py` | Create | Manual migration. |
| `tests/modules/ai/test_repository.py` | Modify | Dedupe-key shape tests. |

## Edge Cases & Risks
- **NOT NULL with default on existing rows** — Postgres fills in the default on `ADD COLUMN`. Verify existing rows have `source='engine'` post-upgrade.
- **Check-constraint name collision** — confirm the existing constraint name by inspecting the first engine migration; adjust `drop_constraint` if needed.

## Acceptance Verification
- [ ] Column added with NOT NULL + default `engine`.
- [ ] Event-type constraint includes the two new values.
- [ ] Dedupe-key helper branches correctly per source.
- [ ] Existing webhook-event tests pass unchanged.
- [ ] Migration round-trips.
- [ ] `uv run pyright`, `ruff`, tests green.
