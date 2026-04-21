# Implementation Plan: T-145 — Wire Checks client into lifecycle service adapters

## Task Reference
- **Task ID:** T-145
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-3 / AC-4 — on S11 create the check; on S12/S13 flip to success/failure. This is the feature's load-bearing wiring.

## Overview
Add `github_check_id` to `TaskImplementation`, inject the Checks client into three signal adapters, and make GitHub failures non-fatal (log + continue). The state machine remains authoritative per AD-1.

## Implementation Steps

### Step 1: Add `github_check_id` column
**File:** `src/app/modules/ai/models.py`
**Action:** Modify

```python
github_check_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

On `TaskImplementation`, alongside the existing columns.

### Step 2: Migration
**File:** `src/app/migrations/versions/YYYY_MM_DD_add_task_impl_github_check.py`
**Action:** Create

`uv run alembic revision --autogenerate -m "add github_check_id to task_implementations"`. Verify the generated op is a single `op.add_column` with `server_default=None`. Hand-edit the file slug to include the date prefix per `CLAUDE.md` Alembic convention.

### Step 3: Wire client into `submit_implementation_signal`
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Change the adapter's signature to accept `github: GitHubChecksClient`. After the T11 transition commits (current behaviour), if `payload.pr_url` is present:

```python
try:
    ref = parse_pr_url(payload.pr_url)
    check_id = await github.create_check(
        owner=ref.owner, repo=ref.repo, head_sha=payload.commit_sha,
    )
    task_impl.github_check_id = check_id
    await db.flush()
    _emit_trace(run_id, "github_check_create", {"check_id": check_id, "repo": ref.slug})
except ValidationError:
    raise  # bad URL → 400
except ProviderError as exc:
    logger.warning("github check create failed", extra={"task_id": task_id, "error": str(exc)})
    _emit_trace(run_id, "github_check_create_failed", {"error": exc.code})
```

Trace kind `github_check_create` goes through the existing JSONL writer. Do **not** rollback the T11 transition — a missed check is non-fatal.

### Step 4: Wire client into `approve_review_signal` / `reject_review_signal`
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Both adapters accept the same `github` parameter. After the T12/T8 transition commits:

```python
impl = await _latest_task_implementation(db, task_id)
if impl and impl.github_check_id and impl.pr_url:
    ref = parse_pr_url(impl.pr_url)
    conclusion = "success" if decision == ApprovalDecision.approve else "failure"
    try:
        await github.update_check(
            owner=ref.owner, repo=ref.repo, check_id=impl.github_check_id, conclusion=conclusion,
        )
        _emit_trace(run_id, "github_check_update", {"check_id": impl.github_check_id, "conclusion": conclusion})
    except ProviderError as exc:
        logger.warning("github check update failed", extra={"task_id": task_id, "error": str(exc)})
        _emit_trace(run_id, "github_check_update_failed", {"error": exc.code})
```

Noop path (no `check_id` stored) → skip silently; the earlier `create_check` returning `"noop"` DID store `"noop"` so the presence check + later Noop `update_check` no-op together handle it correctly.

### Step 5: Route DI
**File:** `src/app/modules/ai/router.py`
**Action:** Modify

The three endpoints (`POST /tasks/{id}/implementation`, `POST /tasks/{id}/review/approve`, `POST /tasks/{id}/review/reject`) add a `github: Annotated[GitHubChecksClient, Depends(get_github_checks_client_dep)]` parameter and forward to the service adapter.

### Step 6: Integration tests
**File:** `tests/integration/test_feat007_merge_gating.py`
**Action:** Create

Using `respx` to stub `api.github.com`:

- **Happy path — approve.** Drive `submit_implementation` with `prUrl="https://github.com/foo/bar/pull/7"` → respx sees `POST /repos/foo/bar/check-runs`. Then `approve_review` → respx sees `PATCH /repos/foo/bar/check-runs/{id}` with `conclusion=success`.
- **Happy path — reject.** Same, `conclusion=failure`.
- **Noop path.** Configure `Settings()` with no GitHub creds; same two signals → zero GitHub HTTP calls; `task_impl.github_check_id == "noop"`.
- **Missing `prUrl`.** `submit_implementation` without a `prUrl` → zero calls; `github_check_id is None`.
- **Transient 500 on create.** respx returns 500; signal still returns 202; trace contains `github_check_create_failed`; `github_check_id is None`.
- **Transient 500 on update.** Same shape: approval still lands; check_run row stays pending on GitHub; trace shows `github_check_update_failed`.
- **Invalid `prUrl`.** `submit_implementation` returns 400 with Problem Details `code=invalid-pr-url`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | Add `github_check_id`. |
| `src/app/migrations/versions/YYYY_MM_DD_add_task_impl_github_check.py` | Create | Alembic migration. |
| `src/app/modules/ai/lifecycle/service.py` | Modify | Wire client into 3 adapters. |
| `src/app/modules/ai/router.py` | Modify | DI for 3 routes. |
| `docs/data-model.md` | Modify | Add `github_check_id` to `TaskImplementation` + changelog. |
| `tests/integration/test_feat007_merge_gating.py` | Create | End-to-end flow tests. |

## Edge Cases & Risks
- **Check already exists (duplicate `create_check`).** If `submit_implementation` fires twice (idempotency key mismatch), the second POST creates a *second* check-run on GitHub. The check name is the same but IDs differ — branch protection only knows the name, so the most recent one wins. Acceptable; document in T-150.
- **Missing `commit_sha`.** The submit payload has `commit_sha` required already (FEAT-006); if ever nullable, the `create_check` path must skip with a trace entry, not error.
- **Post-rejection re-submission.** After a rejection (T8 → `rejected`), the task can be re-implemented (new `TaskImplementation` row). The next `submit_implementation` creates a *new* check. The old check stays at `failure` on GitHub; the new one becomes the gate. GitHub branch protection respects the most-recent check-run with the required name.
- **Data-model doc drift.** `docs/data-model.md` must gain the new field + changelog entry this task — required per CLAUDE.md discipline.

## Acceptance Verification
- [ ] `github_check_id` column lands + migration reversible (`alembic downgrade -1` drops the column).
- [ ] Happy paths (approve + reject) produce exactly 1 POST + 1 PATCH.
- [ ] Noop path produces 0 GitHub calls and doesn't break the signal flow.
- [ ] Transient GitHub failures do **not** fail the signal; trace records the miss.
- [ ] Invalid `prUrl` → 400 Problem Details.
- [ ] `docs/data-model.md` updated with field + changelog entry.
- [ ] `uv run pyright`, `ruff`, full test suite green.
