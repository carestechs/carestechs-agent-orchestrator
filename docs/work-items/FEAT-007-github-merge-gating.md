# Feature Brief: FEAT-007 — GitHub Merge-Gating + Regression Insurance

> **Purpose**: Close FEAT-006's open acceptance criteria (AC-7 merge gating, AC-2/AC-9/AC-11 formal guards). Ships the GitHub Checks API client that turns review approvals into PR merge signals, plus a consolidated integration test suite that locks in FEAT-006's contract.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-007 |
| **Name** | GitHub Merge-Gating + Regression Insurance |
| **Target Version** | v0.6.0 |
| **Status** | Delivered — v0.6.0 |
| **Priority** | High |
| **Requested By** | Tech Lead |
| **Date Created** | 2026-04-19 |

---

## 2. User Story

**As an** admin driving multi-actor feature delivery, **I want to** have the orchestrator register and flip a required `orchestrator/impl-review` check on every GitHub PR that references a task, **so that** reviewer approval is the merge gate — a reviewer's decision is the action that ships code, not a separate GitHub click.

---

## 3. Goal

When a task transitions to `impl_review` (either by the `/tasks/{id}/implementation` endpoint or the `/hooks/github/pr` ingress), the orchestrator creates an `orchestrator/impl-review` check in `pending` on the PR. On S12 (review approve) the check flips to `success`; on S13 (review reject) it flips to `failure`. GitHub's branch protection does the rest. If no GitHub credentials are configured the path degrades to a no-op (noop client), preserving AC-9 composition integrity.

---

## 4. Feature Scope

### 4.1 Included

- **`GitHubChecksClient` protocol** with `create_check` + `update_check` methods.
- **`HttpxGitHubChecksClient`** (App + PAT auth strategies).
- **`NoopGitHubChecksClient`** default when no credentials configured.
- **Composition-root factory** `get_github_checks_client()` picking App > PAT > Noop.
- **T-120 / T-118 integration**: webhook sets check to pending on S11; review endpoints flip it to success/failure on S12/S13.
- **T-123 composition-integrity test** (formal AD-3 guard with `LLM_PROVIDER=stub` + no GitHub config).
- **T-126 FEAT-005 coexistence test** in a single process.
- **T-124 GitHub integration test** using `respx` to mock the Checks API + recorded webhook fixtures.
- **T-122 per-transition integration suite** (optional; consolidated roll-up of the existing unit/route coverage).

### 4.2 Excluded

- **GitHub App installation UX.** Operators configure via env vars; no web flow.
- **Automatic PR merge after approval.** The orchestrator only flips the check; GitHub's branch protection merges.
- **Retention/cleanup of stale checks.** If a task is deferred after `impl_review`, the check stays as whatever state it was last set to.

---

## 5. Acceptance Criteria

- **AC-1**: `GitHubChecksClient` protocol + three implementations land in `src/app/modules/ai/github/checks.py`.
- **AC-2**: Factory returns `HttpxGitHubChecksClient(AppAuthStrategy)` when `GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY` set; `PatAuthStrategy` when `GITHUB_PAT` set; `NoopGitHubChecksClient` otherwise.
- **AC-3**: `POST /api/v1/tasks/{id}/implementation` creates an `orchestrator/impl-review` check when the payload carries a `prUrl` and a client is configured. The target `owner/repo` is parsed from `prUrl` per task — a single credential fans out to every repo the PAT (or App installation) can access.
- **AC-4**: `POST /.../review/approve` resolves the check to `conclusion=success` within 5 s; `/review/reject` resolves to `conclusion=failure`.
- **AC-5**: With `NoopGitHubChecksClient`, all FEAT-006 endpoints continue to function unchanged.
- **AC-6**: T-123 composition-integrity test runs the full 14-signal flow with stub LLM + noop GitHub and asserts zero outbound HTTP calls.
- **AC-7**: T-126 coexistence test runs a FEAT-005 lifecycle-agent run and a FEAT-006 signal-driven flow in the same process back-to-back.
- **AC-8**: T-124 GitHub integration test with `respx` mocks asserts exact call counts (1 create + 1 update per review cycle, 0 on unmatched PRs).

---

## 6. Key Entities and Business Rules

No new entities. Reads `Task`, `TaskAssignment`, `TaskImplementation`, `WebhookEvent`. Writes trace entries on check create/update (optional).

**New entities required:** None.

---

## 7. API Impact

No new endpoints. Extends the behaviour of existing FEAT-006 routes by injecting the Checks client into their service adapters.

**New endpoints required:** None.

---

## 8. UI Impact

None.

**New screens required:** None.

---

## 9. Edge Cases

- **Force-merge bypass.** Admin force-merges before approval. The orchestrator records the event (FEAT-006 audit) but cannot prevent it.
- **PR reopened after approval.** Current design: orchestrator does not reset the check on reopen. Document as known v1 gap.
- **Transient 5xx from GitHub.** Client logs + continues; state machine is authoritative. Admins see audit entry "github check update failed".
- **Rate limits.** Per-review cycle = 2 API calls; well below GitHub's default limits for single-repo usage.
- **App installation-token expiry.** Cache per-repo for 50 min (tokens last 60); refresh on expiry.

---

## 10. Constraints

- **AD-9 composition integrity (AC-6).** The system must run without GitHub credentials configured.
- **Multi-repo by default.** `owner/repo` is parsed per-task from the PR URL; the configured credential (PAT or App installation) must have `repo` scope on every target repo. No per-repo env vars.
- **No retention logic.** Check-run rows accumulate in GitHub; acceptable for v1.
- **Credentials in env vars only.** No secrets rotation UX; operator restarts the service to roll credentials.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-006 shipped the state machine and signal surface but left AC-7 (merge-gating) open because it requires operational setup (GitHub App or PAT) that we don't want to block the feature merge on. FEAT-007 closes the loop now that the plumbing is in.

**Impact if delayed:** Reviewer approval is still a manual-only signal — admins must click "merge" on GitHub after flipping the orchestrator state. Workable, but loses the main value proposition of "reviewer approval = merge ready."

**Dependencies on this feature:** The multi-actor collaboration story (admin + dev + reviewer handoffs) is only complete once the review gate is enforced at the VCS level.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` |
| **Stakeholder Scope Item** | Extends FEAT-006's multi-actor delivery with a real merge gate |
| **Success Metric** | Turns "orchestrator decides, human clicks" into "orchestrator is the gate" |
| **Related Work Items** | FEAT-006 (prerequisite — state machine + signal surface) |
| **Design Input** | `docs/design/deterministic-flow-transitions.md` §Resolved Decisions |

---

## 13. Usage Notes for AI Task Generation

1. **Reuse the existing T-121 plan** (`plans/plan-T-121-github-checks-client.md`) as the starting point for the client implementation. It is mostly ready.
2. **Also reuse the deferred plans**: T-122, T-123, T-124, T-126 plans already exist under `plans/`.
3. **Credentials prerequisite.** Before implementation can finish, operators must provision either a GitHub App (preferred) or a PAT and configure `GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY` (or `GITHUB_PAT`) plus `GITHUB_REPO`. Flag this in the task list so setup is a named prerequisite task, not a silent assumption.
4. **Do not touch FEAT-006 production code** except to inject the new client dependency into the existing service adapters (wiring only). State-machine logic is stable.
