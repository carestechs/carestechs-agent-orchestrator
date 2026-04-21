# FEAT-007 — GitHub Merge-Gating + Regression Insurance

> **Source:** `docs/work-items/FEAT-007-github-merge-gating.md`
> **Status:** Not Started
> **Target version:** v0.6.0
> **Supersedes plans:** `plans/plan-T-121-github-checks-client.md`, `plans/plan-T-122-*`, `plans/plan-T-123-*`, `plans/plan-T-124-*`, `plans/plan-T-126-*` — those were drafted for FEAT-006 and reference the now-removed `GITHUB_REPO` pin.

FEAT-007 ships the GitHub Checks client that turns an impl-review approval into a PR merge signal. State machine + signal surface were delivered by FEAT-006; this feature wires the VCS gate. Multi-repo by default — `owner/repo` is parsed from each task's PR URL, so one PAT (or App installation) fans out to every repo it has `repo` scope on.

---

## Foundation

### T-140: GitHub credential config

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add PAT and App credential fields to `Settings`. No `GITHUB_REPO` — target repo is parsed per-task from the PR URL.

**Rationale:**
AC-2 requires the factory to pick `App > PAT > Noop` based on config.

**Acceptance Criteria:**
- [ ] `github_pat: SecretStr | None`, `github_app_id: str | None`, `github_private_key: SecretStr | None` added to `Settings`.
- [ ] A validator rejects configs that set both PAT and App credentials simultaneously (fail-fast at startup).
- [ ] `.env.example` FEAT-007 block matches the fields (already stubbed).
- [ ] `uv run orchestrator doctor` surfaces the configured strategy (App / PAT / noop).

**Files to Modify/Create:**
- `src/app/config.py` — add fields + cross-field validator.
- `src/app/cli.py` (doctor command) — report selected strategy.
- `tests/test_config.py` — validator + defaults.

**Technical Notes:**
Use `Field(alias="GITHUB_PAT")` etc. Don't reuse `ENGINE_API_KEY`-style patterns — auth here is repo-scoped, not tenant-scoped.

---

### T-141: PR URL parser

**Type:** Backend
**Complexity:** S
**Workflow:** standard
**Dependencies:** None

**Description:**
Pure helper that extracts `(owner, repo, pull_number)` from a GitHub PR URL. Raises a typed error on invalid input.

**Rationale:**
AC-3 derives the target repo per task from `prUrl`. Centralize the regex so the service layer and tests share it.

**Acceptance Criteria:**
- [ ] `parse_pr_url(url: str) -> PullRequestRef` accepts `https://github.com/{owner}/{repo}/pull/{n}` (with and without trailing slash; ignores query + fragment).
- [ ] Rejects non-github.com hosts, non-HTTPS, and malformed paths with `ValidationError` (kebab-case code `invalid-pr-url`).
- [ ] Unit tests cover happy path + 5 edge cases.

**Files to Modify/Create:**
- `src/app/modules/ai/github/pr_urls.py` — `PullRequestRef` dataclass + `parse_pr_url`.
- `tests/modules/ai/github/test_pr_urls.py`.

**Technical Notes:**
Keep it strict — a bad URL should fail the signal, not post to the wrong repo. The `head_sha` is not in the URL; it comes from the payload (`commit_sha`) or from a later `GET /repos/{owner}/{repo}/pulls/{n}` lookup if not supplied.

---

## Backend

### T-142: `GitHubChecksClient` protocol + auth strategies

**Type:** Backend
**Complexity:** M
**Workflow:** standard
**Dependencies:** T-140

**Description:**
Protocol + `AppAuthStrategy` (JWT → installation token, cached per repo 50 min) + `PatAuthStrategy` (static Bearer). No client implementation yet.

**Rationale:**
AC-1 / AC-2 — protocol + auth strategies are the foundation on which Httpx and Noop clients build.

**Acceptance Criteria:**
- [ ] `GitHubChecksClient` Protocol with `create_check(repo, head_sha, name) -> str` and `update_check(repo, check_id, conclusion) -> None`.
- [ ] `AppAuthStrategy` signs RS256 JWT, exchanges for installation token, caches per `(owner/repo)` with 50-min TTL and `asyncio.Lock` to serialize refresh.
- [ ] `PatAuthStrategy` returns static `Authorization: Bearer <pat>` header.
- [ ] Unit tests cover JWT claims, cache hit/miss, concurrent-refresh race.

**Files to Modify/Create:**
- `src/app/modules/ai/github/__init__.py` — package init.
- `src/app/modules/ai/github/auth.py` — `AuthStrategy` protocol, `AppAuthStrategy`, `PatAuthStrategy`.
- `src/app/modules/ai/github/checks.py` — `GitHubChecksClient` protocol only (clients come in T-143).
- `tests/modules/ai/github/test_auth.py`.

**Technical Notes:**
Use `PyJWT[crypto]` (already a common dep; add to `pyproject.toml` if missing). PEM private key in env var is awkward — validator should accept both raw PEM and a file path (`@file:/path/to/key.pem`).

---

### T-143: `HttpxGitHubChecksClient` + `NoopGitHubChecksClient`

**Type:** Backend
**Complexity:** M
**Workflow:** standard
**Dependencies:** T-142

**Description:**
Two implementations of `GitHubChecksClient`. Httpx one calls `POST /repos/{owner}/{repo}/check-runs` and `PATCH /repos/{owner}/{repo}/check-runs/{id}`. Noop logs once and returns.

**Rationale:**
AC-1 (protocol + 3 impls land) and AC-5 (Noop keeps FEAT-006 functional without credentials).

**Acceptance Criteria:**
- [ ] `HttpxGitHubChecksClient`:
  - `create_check` POSTs `{name, head_sha, status: "in_progress"}`, returns check id.
  - `update_check` PATCHes `{status: "completed", conclusion}`.
  - 5xx / 429 / timeout → `ProviderError` with `http_status` + response body attached.
- [ ] `NoopGitHubChecksClient` logs at WARNING exactly once per process on first call (module-level guard), `create_check` returns `"noop"`, `update_check` is a no-op.
- [ ] Tests use `respx` to assert request bodies, headers, and call counts; Noop test asserts single log line.

**Files to Modify/Create:**
- `src/app/modules/ai/github/checks.py` — add both implementations.
- `tests/modules/ai/github/test_checks.py`.

**Technical Notes:**
Share a module-level `httpx.AsyncClient` via DI; follow the existing `lifecycle/engine_client.py` shape for retry/error wrapping. Do **not** auto-retry in the client — the service layer decides whether a failed check update is fatal.

---

### T-144: Composition-root factory + FastAPI dependency

**Type:** Backend
**Complexity:** S
**Workflow:** standard
**Dependencies:** T-143

**Description:**
`get_github_checks_client(settings)` returns App > PAT > Noop. Expose via `modules/ai/dependencies.py`.

**Rationale:**
AC-2 — deterministic priority + a single injection point for the service layer.

**Acceptance Criteria:**
- [ ] Factory lives in `src/app/core/github.py`.
- [ ] Priority: `(github_app_id AND github_private_key)` → App; else `github_pat` → PAT; else Noop.
- [ ] FastAPI dependency `get_github_checks_client_dep` added alongside `get_lifecycle_engine_client`.
- [ ] Unit tests for all three branches + the "App+PAT both set" rejection (delegated to T-140 validator).

**Files to Modify/Create:**
- `src/app/core/github.py`.
- `src/app/modules/ai/dependencies.py`.
- `tests/core/test_github_factory.py`.

---

### T-145: Wire Checks client into lifecycle service adapters

**Type:** Backend
**Complexity:** M
**Workflow:** standard
**Dependencies:** T-141, T-144

**Description:**
Inject `GitHubChecksClient` into the three signal adapters that own the check lifecycle. On `submit_implementation_signal` (S11), parse `prUrl`, call `create_check`, store the returned `check_id` on `TaskImplementation`. On `approve_review_signal` (S12) / `reject_review_signal` (S13), call `update_check` with `success`/`failure`.

**Rationale:**
AC-3 / AC-4 — approval/rejection becomes a merge signal.

**Acceptance Criteria:**
- [ ] `TaskImplementation` gains `github_check_id: str | None` (nullable — unset in Noop path).
- [ ] `submit_implementation_signal`: parses `prUrl` → `(owner, repo)`, creates the check with the payload's `commit_sha` as `head_sha`, persists `check_id`.
- [ ] `approve_review_signal` / `reject_review_signal`: look up `check_id` on the latest `TaskImplementation` for the task; call `update_check`; on missing check_id (Noop path, or impl without `prUrl`) log + continue.
- [ ] Transient 5xx from GitHub → log structured warning, do not fail the signal. State machine advances regardless (AD-1: orchestrator is authoritative).
- [ ] Route integration tests prove the call sequence with `respx`.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — add `github_check_id` column.
- `src/app/migrations/versions/YYYY_MM_DD_add_task_impl_github_check.py` — migration.
- `src/app/modules/ai/lifecycle/service.py` — wire client into 3 adapters.
- `src/app/modules/ai/router.py` — DI hook-up.
- `tests/integration/test_feat007_merge_gating.py` — happy paths + noop path.

**Technical Notes:**
Failures here are **non-fatal** — the signal already persisted the `Approval` row and mirrored the transition. A missed check update produces a stale GitHub UI, not a stuck state machine. Add a trace entry (`trace_kind="github_check_update"`) so operators can spot misses.

---

## Testing

### T-146: Composition-integrity regression test

**Type:** Testing
**Complexity:** S
**Workflow:** standard
**Dependencies:** T-144

**Description:**
Full 14-signal FEAT-006 flow with `LLM_PROVIDER=stub` and no GitHub config. Assert zero outbound HTTP calls and the Noop client picked.

**Rationale:**
AC-6 — formalizes AD-9 (composition integrity) for FEAT-007.

**Acceptance Criteria:**
- [ ] `tests/integration/test_feat007_composition_integrity.py` runs all 14 lifecycle signals end-to-end against the test DB.
- [ ] Uses `respx.mock(assert_all_called=False)` globally; asserts `respx.calls` is empty for any `api.github.com` route.
- [ ] `get_github_checks_client(settings)` in the test app yields `NoopGitHubChecksClient`.

**Files to Modify/Create:**
- `tests/integration/test_feat007_composition_integrity.py`.

---

### T-147: FEAT-005 ↔ FEAT-006 coexistence test

**Type:** Testing
**Complexity:** M
**Workflow:** standard
**Dependencies:** T-144

**Description:**
Single-process test that runs (a) a FEAT-005 lifecycle-agent run to completion with the stub LLM, and (b) a FEAT-006 14-signal flow back-to-back, in the same FastAPI app.

**Rationale:**
AC-7 — FEAT-007 mustn't regress the pre-FEAT-006 runtime. Also catches DI/startup order bugs between `get_run_supervisor` and the new GitHub dep.

**Acceptance Criteria:**
- [ ] Both runs complete in the same test without app restart.
- [ ] Asserts `Run.status="completed"` for the agent run and `WorkItem.status="ready"` after the signal flow.
- [ ] Covers the case where the PAT is configured but the test uses `respx` so no real HTTP fires.

**Files to Modify/Create:**
- `tests/integration/test_feat005_feat006_coexistence.py`.

---

### T-148: GitHub integration test (respx)

**Type:** Testing
**Complexity:** M
**Workflow:** standard
**Dependencies:** T-145

**Description:**
Full FEAT-006 review cycle for one task, with GitHub mocked via `respx`. Asserts exact call counts and request bodies.

**Rationale:**
AC-8 — locks in the Checks API contract + call counts (1 create + 1 update per review cycle).

**Acceptance Criteria:**
- [ ] `respx` mocks `POST /repos/{owner}/{repo}/check-runs` (201) and `PATCH /repos/{owner}/{repo}/check-runs/{id}` (200).
- [ ] After `submit_implementation` + `approve_review`: exactly 1 POST + 1 PATCH with `conclusion=success`.
- [ ] After `submit_implementation` + `reject_review`: exactly 1 POST + 1 PATCH with `conclusion=failure`.
- [ ] After `submit_implementation` without a `prUrl` (Noop path): 0 calls.
- [ ] Auth header format asserted for both PAT and App strategies.

**Files to Modify/Create:**
- `tests/integration/test_feat007_github_integration.py`.

**Technical Notes:**
For the App path, stub `POST /app/installations/{id}/access_tokens` and assert the installation token is cached (second review cycle does not re-fetch).

---

### T-149: Opt-in live smoke test

**Type:** Testing
**Complexity:** S
**Workflow:** standard
**Dependencies:** T-148

**Description:**
Marked `@pytest.mark.live` + guarded behind `--run-live` (already configured in `conftest.py`). Hits the real GitHub API with a PAT against a scratch repo, creates + updates a single check on a real PR, asserts the API round-trips cleanly.

**Rationale:**
Catches auth-header drift, API-shape changes, and PAT-scope misconfiguration that mocked tests can't. Opt-in so CI stays offline.

**Acceptance Criteria:**
- [ ] Test skips unless `--run-live` is passed.
- [ ] Requires `GITHUB_PAT` + `GITHUB_SMOKE_PR_URL` env vars; skips cleanly if absent.
- [ ] Creates a check named `orchestrator/smoke-test` (not the real gate name), flips it to `success`, then to `neutral`.

**Files to Modify/Create:**
- `tests/contract/test_github_checks_live.py`.

---

## Polish

### T-150: Operator docs — provisioning + branch-protection checklist

**Type:** Documentation
**Complexity:** S
**Workflow:** standard
**Dependencies:** T-145

**Description:**
README section covering PAT provisioning, required env vars, and the GitHub ruleset options to enable/skip for FEAT-007 to do its job.

**Rationale:**
The gate only works when branch protection requires `orchestrator/impl-review`. Operators need the exact checklist.

**Acceptance Criteria:**
- [ ] README "GitHub merge-gating" section lists: PAT scope (`repo`), required env vars, the exact ruleset toggles to enable, and the "create a PR first so GitHub registers the check name" gotcha.
- [ ] Cross-links `docs/work-items/FEAT-007-github-merge-gating.md`.
- [ ] `orchestrator doctor` output documented in the section.

**Files to Modify/Create:**
- `README.md` — new section after "Self-Hosted Feature Delivery".

---

## Summary

| Type | Count |
|------|-------|
| Backend | 6 (T-140..T-145) |
| Testing | 4 (T-146..T-149) |
| Documentation | 1 (T-150) |
| **Total** | **11** |

**Complexity:** 4 × S · 6 × M · 0 × L · 0 × XL

**Critical path:** T-140 → T-142 → T-143 → T-144 → T-145 → T-148 → T-150

**Risks & open questions:**
- **PEM in env var** — awkward for App auth; T-142 adds `@file:` prefix support. Verify ops tooling accepts that shape.
- **Installation-token cache eviction** — per-process only (AD-4 single-worker). If multi-worker ever lands, cache must move to Postgres.
- **Check-run name immutability** — once `orchestrator/impl-review` is registered on a repo, renaming breaks branch protection. Lock the constant in one place (`src/app/modules/ai/github/checks.py`) and flag it in T-150.
- **Force-merge bypass** — AC-unrelated but the brief flags it; no code change possible, only documentation.
- **Superseded plans** — T-121/T-122/T-123/T-124/T-126 plans in `plans/` reference `GITHUB_REPO` and the old numbering; they should be deleted or moved to `plans/archive/` as part of T-140. Left explicit here so nobody regenerates the wrong plan by accident.
