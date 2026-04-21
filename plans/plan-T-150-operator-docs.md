# Implementation Plan: T-150 — Operator docs (provisioning + branch-protection checklist)

## Task Reference
- **Task ID:** T-150
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** The gate only works when branch protection requires `orchestrator/impl-review`. Operators need the exact checklist.

## Overview
Add a "GitHub merge-gating" section to the README that walks through PAT provisioning, env var configuration, ruleset toggles, and the "create a PR first" gotcha. Cross-link to the FEAT-007 brief.

## Implementation Steps

### Step 1: Draft the README section
**File:** `README.md`
**Action:** Modify

Insert after "Self-Hosted Feature Delivery" and before "Tests":

Sections to cover, in order:
1. **What it does** — one paragraph: reviewer approval becomes PR merge signal via the `orchestrator/impl-review` check.
2. **Provisioning a PAT (recommended for single-maintainer setups).**
   - Create a classic PAT with `repo` scope at `https://github.com/settings/tokens`.
   - Scope covers every repo the token owner can access; no `GITHUB_REPO` pin.
3. **Provisioning a GitHub App (multi-user orgs).**
   - Create the App with `Checks: write` + `Pull requests: read` permissions.
   - Install on target repos.
   - Set `GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY` (or `@file:/path/to/key.pem`).
4. **Env vars.**
   - `GITHUB_PAT` OR (`GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY`) — not both.
   - `GITHUB_WEBHOOK_SECRET` (separate — that's for inbound PR webhooks, not the Checks API).
   - `orchestrator doctor` reports the resolved strategy.
5. **Branch protection (ruleset UI).**
   - Required toggles: *Require a pull request*, *Require status checks → `orchestrator/impl-review`*, *Block force pushes*, *Restrict deletions*.
   - Skip: *Require linear history*, *Require merge queue*, *Require deployments*, *Require signed commits*, code scanning / quality / Copilot review (orthogonal).
   - Gotcha: the `orchestrator/impl-review` check name only appears in GitHub's ruleset picker **after the orchestrator has posted at least one check**. Flow: open a throwaway PR → run through `submit_implementation` + `approve_review` once → the name now appears → add it as required.
6. **Known limitations.**
   - Force-merge bypass — admins with bypass permission skip the gate; FEAT-007 records the audit via FEAT-006 but cannot prevent it.
   - PR reopened after approval — orchestrator does not reset the check.
   - Transient 5xx from GitHub — state machine advances regardless; check may be stale on GitHub. Trace entry records the miss.

### Step 2: Cross-link
**File:** `README.md`
**Action:** Modify

Link to `docs/work-items/FEAT-007-github-merge-gating.md` at the end of the section.

### Step 3: Update status
**File:** `docs/work-items/FEAT-007-github-merge-gating.md`
**Action:** Modify

Flip `Status` from "Not Started" to "Delivered — v0.6.0" (once T-140..T-149 are merged).

### Step 4: Changelog note
**File:** `docs/ARCHITECTURE.md`
**Action:** Modify (if needed)

If the factory adds a new composition-root module (`src/app/core/github.py`), add one line to the module-map section + changelog entry per CLAUDE.md maintenance discipline.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `README.md` | Modify | "GitHub merge-gating" section. |
| `docs/work-items/FEAT-007-github-merge-gating.md` | Modify | Flip status on merge. |
| `docs/ARCHITECTURE.md` | Modify (if applicable) | Module + changelog entry. |

## Edge Cases & Risks
- **Docs drift.** If T-142/T-143 change headers or behaviour, this doc will lag. Include a short "verify against T-148 integration test" pointer so operators can sanity-check.
- **Screenshots.** Avoid them — GitHub's UI changes frequently. Describe toggles by name.

## Acceptance Verification
- [ ] README section renders correctly and lists every required + skippable ruleset toggle.
- [ ] `orchestrator doctor` output documented.
- [ ] FEAT-007 brief status flipped to "Delivered".
- [ ] Changelog entries in `ARCHITECTURE.md` (if module added) per maintenance discipline.
