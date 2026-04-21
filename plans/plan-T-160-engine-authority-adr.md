# Implementation Plan: T-160 — Authoring ADR superseding rc2 closeout

## Task Reference
- **Task ID:** T-160
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-2. The rc2 ADR's conclusion is load-bearing in comments + review discussions; inverting it without an explicit supersession is a review-time footgun.

## Overview
Write the authoritative FEAT-008 ADR + banner the rc2 ADR forward to it. Nothing ships without this, because every subsequent task will reference it in comments, PR descriptions, or docstrings.

## Implementation Steps

### Step 1: Author the new ADR
**File:** `docs/design/feat-008-engine-as-authority.md`
**Action:** Create

Lift the "Architectural Position" section and Philosophy additions from `docs/stakeholder-definition.md` verbatim — the ADR is the architectural specification of that vision. Structure:

1. **Status** — "Accepted · 2026-04-XX · Supersedes: feat-006-rc2-architectural-position.md"
2. **Context** — what the rc2 ADR concluded, under which unstated architectural premise, and the conversation that surfaced the drift.
3. **Decision** — the three hard rules from stakeholder-definition's Architectural Position section, reproduced exactly. Any drift between the two documents is a bug to fix before merge.
4. **Consequences** — what changes (aux writes → reactor, status → cache, locked_from/deferred_from dropped, effector registry, outbox), what stays (signal endpoints as ingress, engine-absent fallback, FEAT-007's Checks client behavior).
5. **What would flip this decision** — mirror the rc2 ADR's own closing section: concrete signals that would motivate reopening (e.g., "second consumer needs engine write access," "effector throughput exceeds single-process capacity").

### Step 2: Banner the rc2 ADR
**File:** `docs/design/feat-006-rc2-architectural-position.md`
**Action:** Modify

Prepend a banner immediately under the H1:

```markdown
> **⚠️ SUPERSEDED BY [FEAT-008](feat-008-engine-as-authority.md) · 2026-04-XX**
>
> The conclusion below ("rc2-phase-2 as currently merged is the end state")
> was reasoned under the premise that the flow engine is a passive mirror
> and cross-tool consumers would never need the orchestrator's rich audit
> data.  FEAT-008 inverts the premise: the engine is the authoritative
> private backend, and aux-row writes move to the reactor.  Read this
> document for historical context only — do not cite it in review.
```

### Step 3: Cross-link from CLAUDE.md
**File:** `CLAUDE.md`
**Action:** Modify (if applicable)

CLAUDE.md currently references architectural rules inline rather than linking a master ADR. If a "see docs/design/..." section exists, add the FEAT-008 ADR there. If not, skip — don't invent a section just for the link.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/design/feat-008-engine-as-authority.md` | Create | New ADR. |
| `docs/design/feat-006-rc2-architectural-position.md` | Modify | Add supersession banner. |
| `CLAUDE.md` | Modify (conditional) | Cross-link if a design-index section exists. |

## Edge Cases & Risks
- **Divergence between ADR and stakeholder-definition.** The Architectural Position text exists in two places now. Either (a) keep both, sync manually on future edits; or (b) let stakeholder-definition own the summary and link the ADR from there. Pick one in the PR discussion.
- **The rc2 ADR's body stays unchanged.** Don't edit its reasoning — it's a record. The banner is the only change.

## Acceptance Verification
- [ ] New ADR includes the three hard rules from stakeholder-definition's Architectural Position section.
- [ ] rc2 ADR has the supersession banner with a forward link.
- [ ] A "what would flip this decision" section exists with concrete signals.
- [ ] Markdown renders cleanly (`markdownlint` or visual review).
