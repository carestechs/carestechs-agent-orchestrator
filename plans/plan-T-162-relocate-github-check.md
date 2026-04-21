# Implementation Plan: T-162 — Relocate GitHub check create/update into effectors

## Task Reference
- **Task ID:** T-162
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-4. Proves the registry against a working feature. Behavior preservation is strict — every FEAT-007 test passes unchanged.

## Overview
Extract the inline `_post_create_check` and `_post_update_check` helpers from `lifecycle/service.py` into two effectors (`GitHubCheckCreateEffector`, `GitHubCheckUpdateEffector`). Register them against the right transition keys. Wire the registry into lifespan. All FEAT-007 tests must pass unchanged.

## Implementation Steps

### Step 1: Author `GitHubCheckCreateEffector`
**File:** `src/app/modules/ai/lifecycle/effectors/github.py`
**Action:** Create

Logic matches `_post_create_check` in `service.py`:

1. If the client is `NoopGitHubChecksClient`: store `NOOP_CHECK_ID` on the latest `TaskImplementation`, commit, return `status="skipped"` with a note.
2. Look up the latest `TaskImplementation` for the task (via `EffectorContext.db`). If none found or `pr_url` is None, return `status="skipped"` with reason.
3. Parse `pr_url` → `PullRequestRef`. On `ValidationError`: return `status="error"` with `error_code="invalid-pr-url"` (the signal already returned 202 — the effector can't rollback; this is a data-quality issue, surfaced in the trace).
4. Call `github.create_check(owner, repo, head_sha=commit_sha)`. On `ProviderError`: return `status="error"` with the provider's `error_code`.
5. On success: persist `check_id` on the `TaskImplementation`, commit, return `status="ok"` with `metadata={"check_id": check_id, "repo": ref.slug}`.

Pull the `GitHubChecksClient` from `ctx.db.info` (if we stash it) or from a DI lookup — **don't** widen `EffectorContext`. Simplest: read `github_checks_client` off `request.app.state` via a small helper that the effector constructor captures at registration time.

Cleanest: effector constructor takes `github: GitHubChecksClient` — registered during bootstrap (T-161's registry + T-162's wiring) when the app-state client is already resolved. Context stays narrow.

### Step 2: Author `GitHubCheckUpdateEffector`
**File:** `src/app/modules/ai/lifecycle/effectors/github.py`
**Action:** Modify

Same shape. `ctx.to_state` tells us whether to use `conclusion="success"` (on `done`) or `conclusion="failure"` (on rejection/stays in `impl_review`). Register two instances — one per transition:

- `task:implementing->impl_review` → `GitHubCheckCreateEffector`
- `task:impl_review->done` → `GitHubCheckUpdateEffector(conclusion="success")`
- `task:impl_review->rejected` → `GitHubCheckUpdateEffector(conclusion="failure")` — if that's the transition shape; otherwise the rejection stays-at-impl_review path needs a different key.

**Check the transition declarations first** (`declarations.py`) before locking in the key shape. If rejection keeps the task at `impl_review`, the registry key is `task:impl_review->impl_review` (idempotent entry), which won't work — we'll need an "event" key rather than a state transition key. In that case, extend T-161's scheme with `"task:event:review-rejected"` keys, registered by the signal adapter when it fires the event.

If rejection events don't cleanly map to state transitions, accept the limitation for v1: reject-effector fires from the signal adapter (pre-effector-dispatch) as a named event. Document this gap in the ADR + T-161's registry docstring.

### Step 3: Bootstrap module — register all effectors at startup
**File:** `src/app/modules/ai/lifecycle/effectors/bootstrap.py`
**Action:** Create

```python
def register_all_effectors(
    registry: EffectorRegistry,
    settings: Settings,
    github: GitHubChecksClient,
) -> None:
    registry.register(
        "task:implementing->impl_review",
        GitHubCheckCreateEffector(github=github),
    )
    registry.register(
        "task:impl_review->done",
        GitHubCheckUpdateEffector(github=github, conclusion="success"),
    )
    # + rejection path per step 2 decision
```

Single entry point — every effector registration happens here. T-163, T-164, T-171 all extend this function.

### Step 4: Wire lifespan
**File:** `src/app/lifespan.py`
**Action:** Modify

After the existing `_bootstrap_github_checks_client(app)` call, add:

```python
registry = EffectorRegistry(trace_store=get_trace_store())
register_all_effectors(registry, get_settings(), app.state.github_checks_client)
app.state.effector_registry = registry
```

Expose via a FastAPI dep `get_effector_registry_dep(request)` in `modules/ai/dependencies.py` so the reactor (T-167) can pull it.

### Step 5: Remove inline GitHub calls from service.py
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Delete:
- `_post_create_check`
- `_post_update_check`
- The calls to both from `submit_implementation_signal`, `approve_review_signal`, `reject_review_signal`
- The `github: GitHubChecksClient | None = None` param from those three adapters (the effectors hold it now)

Signal adapters no longer know GitHub exists. The reactor fires effectors after the state transition — that's the new path. **For this task, we need a transitional path:** the reactor doesn't do this yet (T-167 lands it). So for T-162, fire effectors *from the signal adapter itself* as a stopgap — directly after the state transition, before returning. That's ugly but reversible:

```python
# In service.py, after the state transition commits:
registry = ...  # pulled from DI
ctx = EffectorContext(entity_type="task", entity_id=task_id, ...)
await registry.fire_all(ctx)
```

When T-167 moves aux writes to the reactor, this firing point moves with it. **Call this out explicitly in a comment so the next task knows where to pull the wire.**

### Step 6: Remove ad-hoc trace kinds
**File:** `src/app/modules/ai/lifecycle/service.py`, `src/app/modules/ai/trace.py`
**Action:** Modify

FEAT-007 emits `trace_kind="github_check_create"` / `"github_check_create_failed"` / `"github_check_update"` / `"github_check_update_failed"` from inline code. Those trace kinds disappear — everything flows through `effector_call` now. Update any existing tests that assert on those kinds.

### Step 7: Tests — FEAT-007 suite passes unchanged
**File:** `tests/integration/test_feat007_merge_gating.py`, `tests/integration/test_feat007_github_integration.py`
**Action:** Modify (assertions only, if any broke)

The tests were written against HTTP-level behavior (respx call counts, body shapes). They should pass as-is. If any asserted on the ad-hoc trace kinds (step 6), swap to `effector_call` with `effector_name="github_check_create"`.

### Step 8: New unit tests — effector behavior
**File:** `tests/modules/ai/lifecycle/effectors/test_github.py`
**Action:** Create

Narrow unit tests (separate from the integration-level FEAT-007 tests):

- `create_effector` fires `create_check`, persists `check_id`, returns `status="ok"`.
- With noop client: stores `NOOP_CHECK_ID`, returns `status="skipped"`.
- With no `TaskImplementation` row: returns `status="skipped"` with reason.
- With `pr_url=None`: returns `status="skipped"`.
- Invalid `pr_url`: `status="error"`, `error_code="invalid-pr-url"`.
- `update_effector` fires `update_check` with correct conclusion, returns `status="ok"`.
- `check_id=NOOP_CHECK_ID`: `skipped`, no HTTP.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/effectors/github.py` | Create | Two effectors. |
| `src/app/modules/ai/lifecycle/effectors/bootstrap.py` | Create | Registration entry point. |
| `src/app/lifespan.py` | Modify | Instantiate + wire registry. |
| `src/app/modules/ai/dependencies.py` | Modify | `get_effector_registry_dep`. |
| `src/app/modules/ai/lifecycle/service.py` | Modify | Remove inline helpers, add transitional dispatch call. |
| `src/app/modules/ai/trace.py` | Modify | Remove ad-hoc github check trace kinds. |
| `tests/integration/test_feat007_merge_gating.py` | Modify | Trace-kind assertions (if any). |
| `tests/integration/test_feat007_github_integration.py` | Modify | Trace-kind assertions (if any). |
| `tests/modules/ai/lifecycle/effectors/test_github.py` | Create | Unit tests. |

## Edge Cases & Risks
- **Rejection transition key.** See Step 2. Decide the scheme (state-transition vs named event) before writing the registration line. Drives T-161's key model.
- **Transitional dispatch point.** Step 5 puts the `fire_all` call in the signal adapter; T-167 moves it. Leave a comment (`# FIXME: moves to reactor in T-167`) so the handoff is obvious in diff review.
- **Noop + `NOOP_CHECK_ID` semantics.** The sentinel must still be stored so later `update_check` can short-circuit. Effector returning `skipped` doesn't mean "do nothing" — it means "the outcome is recorded but no HTTP fired."
- **respx assertions.** Some FEAT-007 tests use `respx.mock()` with implicit "all routes called" semantics. If the effector-dispatch path changes call counts by one (extra commit, for example), the tests break. Audit before expecting behavior preservation.

## Acceptance Verification
- [ ] Two effectors exist and are registered at startup.
- [ ] Signal adapters in `service.py` no longer reference `GitHubChecksClient` directly.
- [ ] All FEAT-007 tests pass unchanged (`tests/integration/test_feat007_*`).
- [ ] Every GitHub call produces one `effector_call` trace entry.
- [ ] `uv run pyright`, `ruff`, full FEAT-007 + new unit test suite green.
