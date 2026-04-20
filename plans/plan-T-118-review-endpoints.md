# Implementation Plan: T-118 — Implementation + review endpoints (S11-S13)

## Task Reference
- **Task ID:** T-118
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-113, T-114, T-117

## Overview
Three endpoints: S11 agent-path implementation submit, S12 review approve, S13 review reject. Adds `TaskImplementation` audit table. Review endpoints call into the GitHub Checks client (T-121) — injected; noop if not configured.

## Steps

### 1. Modify `src/app/modules/ai/models.py`
- New model:
  ```python
  class TaskImplementation(Base):
      __tablename__ = "task_implementations"
      id, task_id (FK), pr_url (nullable), commit_sha, summary, submitted_by, submitted_at
  ```

### 2. Create `src/app/migrations/versions/<ts>_add_task_implementations.py`
- Autogenerate + rename.

### 3. Modify `src/app/modules/ai/schemas.py`
- DTOs:
  - `ImplementationSubmitRequest(prUrl: str | None = None, commitSha: str, summary: str)`.
  - `ReviewApproveRequest()` empty.
  - `ReviewRejectRequest(feedback: str = Field(min_length=1))`.

### 4. Modify `src/app/modules/ai/service.py`
- Adapters:
  - `submit_implementation_signal(session, task_id, req, *, actor)` — admin-only (agent path). Writes `TaskImplementation`, transitions `implementing → impl_review`. If PR URL present + GitHub client configured, create `orchestrator/impl-review` check in `pending` (delegate to T-121 client).
  - `approve_review_signal(...)` — matrix check for `ApprovalStage.IMPL` (returns `ADMIN` in solo-dev; `DEV` otherwise). Transition to `done`. Fire `maybe_advance_to_ready`. If GitHub client configured, update check → `success`.
  - `reject_review_signal(...)` — matrix check. Transition back to `implementing`. GitHub check → `failure`.

### 5. Modify `src/app/modules/ai/dependencies.py`
- Add `get_github_checks_client` placeholder dependency returning a noop for v1 (real wiring lands in T-121). Keep the seam here so T-118 can depend on it now.

### 6. Modify `src/app/modules/ai/router.py`
- 3 routes: `/implementation`, `/review/approve`, `/review/reject`.

### 7. Create `tests/modules/ai/test_router_tasks_review.py`
- Matrix tests parallel to T-117.
- With `NoopGitHubChecksClient` overridden via `app.dependency_overrides`: submit → approve → done + W5 fires if last task.
- With a recording double: assert one `create_check` call on `/implementation`, one `update_check(success)` on approve, `update_check(failure)` on reject.
- Idempotent replay doesn't double-call GitHub.
- Reject without feedback → `422`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | `TaskImplementation`. |
| `src/app/migrations/versions/<ts>_add_task_implementations.py` | Create | Migration. |
| `src/app/modules/ai/schemas.py` | Modify | DTOs. |
| `src/app/modules/ai/service.py` | Modify | Three service adapters. |
| `src/app/modules/ai/dependencies.py` | Modify | GitHub client dep placeholder. |
| `src/app/modules/ai/router.py` | Modify | 3 routes. |
| `tests/modules/ai/test_router_tasks_review.py` | Create | Route tests. |

## Edge Cases & Risks
- **GitHub unavailable** — if the Checks call fails transiently (5xx / timeout), log + continue. The orchestrator state is authoritative. Don't fail the review on a network hiccup.
- **Double-call under idempotent replay** — the idempotency key short-circuits before the service runs, so GitHub calls fire once. Test it explicitly.
- **W5 after last `done`** — when the last non-terminal task flips to done, W5 advances the work item. Test with a 2-task fixture.

## Acceptance Verification
- [ ] 3 endpoints + `TaskImplementation` table.
- [ ] Matrix enforced; wrong role → `403`.
- [ ] Reject requires feedback.
- [ ] GitHub client invoked correctly (tested via double).
- [ ] Noop client path works (AC-9 composition integrity).
- [ ] Idempotent replay doesn't re-call GitHub.
- [ ] W5 fires after last task done.
- [ ] `uv run pyright`, `ruff`, route tests green.
