# Implementation Plan: T-174 — Documentation sweep for engine-as-authority

## Task Reference
- **Task ID:** T-174
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-160 (ADR), T-167, T-169, T-173
- **Rationale:** FEAT-008 inverted the FEAT-006 rc2 model from "orchestrator drives, engine mirrors" to "engine is authoritative private backend, orchestrator is gateway+reactor". The implementation shipped (T-161 through T-173) but the project-level docs still describe pieces of the old model. Closing this gap is the standing CLAUDE.md "doc maintenance discipline" obligation for an architectural shift.

## Overview

Pure documentation. No code, no tests, no migrations. The work is a coordinated walk through six files, with a grep-driven check at the end to verify no contradictory live claims survive. Each file gets the smallest edit that makes the FEAT-008 model the documented model.

## Implementation Steps

### Step 1: Pre-flight grep for stale terms

**Action:** Run

```bash
rg -nF --type md \
  -e "rc2" \
  -e "dead mirror" \
  -e "engine is a mirror" \
  -e "mirror only" \
  -e "inline aux" \
  -e "synchronous aux" \
  -e "orchestrator drives" \
  -e "engine mirrors" \
  docs/ CLAUDE.md README.md
```

Capture the hit list. Each hit is a candidate for either correction (live claim no longer true) or annotation (historical context — FEAT-008 brief itself uses "rc2" by design). The follow-up edits target only the live claims.

### Step 2: `docs/ARCHITECTURE.md`

**File:** `docs/ARCHITECTURE.md`
**Action:** Modify

Targets:
- The component diagram or section that names "Reactor" — extend to mention the effector registry as a sibling subsystem dispatched on every webhook.
- The state-ownership paragraph — switch from "orchestrator owns lifecycle state, engine mirrors" to "engine is the authoritative state owner; orchestrator caches the latest known state for read-time convenience". Aux rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) are written by the reactor on correlation-matched webhook arrival, not by the signal adapter.
- The webhook-flow paragraph — explicit ordering: persist → outbox materialize → status cache → consume correlation → dispatch effectors → derivations.
- Add a one-paragraph "Engine-absent fallback" callout: dev mode preserves the pre-FEAT-008 inline path; production targets the engine-present path.

Changelog entry at the bottom (per `.ai-framework/guides/maintenance.md`):

```
### 2026-04-25 — FEAT-008 (engine-as-authority is live)
- Engine is the authoritative state owner; status columns demoted to reactor-managed cache.
- Aux rows are written by the reactor on correlation-matched webhook arrival.
- Effector registry is a first-class subsystem; every transition fires registered effectors or carries an explicit `no_effector` exemption.
- Outbox (`pending_aux_writes`) backstops orphan aux rows; `orchestrator reconcile-aux` drains.
```

### Step 3: `CLAUDE.md`

**File:** `CLAUDE.md`
**Action:** Modify

Targets:
- **Patterns to Follow** — add three entries:
  1. *Effector registry is the outbound surface.* Every transition either fires a registered effector or carries an `@no_effector("reason")` exemption. New external integrations (Slack, email, audit trails) land as effectors registered at lifespan, not as inline calls in signal handlers.
  2. *Reactor owns status cache + aux rows.* Signal adapters forward to the engine, enqueue the outbox, return 202. Status columns and `Approval` / `TaskAssignment` / `TaskPlan` / `TaskImplementation` rows are written by the reactor on the matching `item.transitioned` webhook.
  3. *Per-request effector dispatch is the narrow exception.* When an effector needs DI-bound state (e.g. `GitHubChecksClient` from per-request DI), it is dispatched via `dispatch_effector(...)` from the signal adapter and its registry slot carries a `no_effector` exemption pointing at the dispatch site. Default to permanent registration; reach for per-request only when DI demands it.
- **Anti-Patterns to Avoid** — add two entries:
  1. *Don't write `status` from a signal adapter under engine-present mode.* The reactor is the only writer. Stale-read window is by design.
  2. *Don't add inline calls for new external integrations.* The effector registry is the seam; bypass it only with explicit justification (and an exemption entry).
- The existing "Don't modify carestechs-flow-engine to simplify agent behavior" entry stays as-is — it remains correct.

### Step 4: `docs/data-model.md`

**File:** `docs/data-model.md`
**Action:** Modify

Verify (and correct if drifted):
- `pending_aux_writes` table description includes `correlation_id` UNIQUE, `signal_name`, `payload JSONB`, `entity_id`, `enqueued_at`, `resolved_at` (nullable).
- `work_items.status` and `tasks.status` are described as reactor-managed caches, not authoritative state. The previous "set by signal handlers" framing must be removed.
- `work_items.locked_from` and `tasks.deferred_from` are not present in the model. T-168 already dropped them; double-check there's no orphan reference in tables, indexes, or example queries.

Changelog entry if any of the above required edits:

```
### 2026-04-25 — FEAT-008 (data-model alignment)
- `work_items.status` / `tasks.status` reframed as reactor-managed caches.
- (Confirms T-168 column drop is reflected throughout — no live references to `locked_from` / `deferred_from`.)
```

### Step 5: `docs/api-spec.md`

**File:** `docs/api-spec.md`
**Action:** Modify

Verify near the lifecycle signal endpoints (S5, S7, S8, S9, S11, S12) that there's a callout matching the FEAT-008 brief §7:

> Behavioral change under engine-present mode: 202 returns *before* aux rows land. Callers that assert on aux-row state must poll the entity back or rely on the engine's webhook ordering.

If the callout is absent, add it once at the top of the lifecycle-signals section (one paragraph) rather than per-endpoint. Add a changelog entry if edits land.

### Step 6: `docs/stakeholder-definition.md`

**File:** `docs/stakeholder-definition.md`
**Action:** Modify

Find the "Architectural Position" section (added 2026-04-21 per the FEAT-008 brief). Update the framing from prospective ("this is the target") to present tense ("this is the live model as of FEAT-008"). One- or two-sentence edit; do not rewrite the section.

### Step 7: `README.md`

**File:** `README.md`
**Action:** Modify

In the Operations section (already mentions `reconcile-aux` from T-170), add:
- Link to the FEAT-008 ADR (`docs/design/feat-008-engine-as-authority.md`) for architectural context.
- One sentence on effector observability: "Every transition emits an `effector_call` trace entry — see `<trace_dir>/effectors/<entity_id>.jsonl` for the per-entity stream."

### Step 8: Verify with the same grep

Re-run the Step-1 grep. Each remaining hit must be either:
- An intentional historical reference (FEAT-008 brief, ADR, changelog entries — these legitimately discuss the old model).
- A correction the sweep missed — fix it.

If the grep returns zero non-historical hits, the task is done.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `docs/ARCHITECTURE.md` | Modify | Engine-as-authority narrative + effector registry callout + changelog. |
| `CLAUDE.md` | Modify | New patterns (registry, reactor, per-request exception); new anti-patterns. |
| `docs/data-model.md` | Modify | Status-as-cache framing; verify dropped columns absent. |
| `docs/api-spec.md` | Modify | Aux-rows-not-synchronous callout near lifecycle signals. |
| `docs/stakeholder-definition.md` | Modify | "Architectural Position" → present tense. |
| `README.md` | Modify | Link FEAT-008 ADR + effector trace path. |

## Edge Cases & Risks

- **Risk: doc drift creeps back via reflexive copy-paste.** A future PR that touches lifecycle code and re-quotes the old framing reintroduces stale claims. The grep in Step 1 / Step 8 is the cheapest catch; consider adding it to a future docs-lint task.
- **Risk: changelog format inconsistency.** Keep the date format `### 2026-04-25 — <topic>` so existing reviewers' grep recipes still work. `.ai-framework/guides/maintenance.md` is the canonical reference.
- **Risk: scope creep into a rewrite.** The brief is "make existing docs accurate", not "rewrite from scratch". If a section needs more than a paragraph of edits, leave a `TODO(FEAT-009): full rewrite` marker and ship the surgical edit.
- **Risk: ADR file path drift.** T-160 created the ADR. Confirm its path before linking from README — open the file by name (`docs/design/feat-008-engine-as-authority.md` per the brief §4.1) before referencing it.

## Acceptance Verification

- [ ] All six files updated with surgical edits; no rewrites.
- [ ] Each updated spec doc carries a dated changelog entry.
- [ ] Step-1 / Step-8 grep returns zero non-historical hits.
- [ ] `git diff` shows only doc files touched — no `.py`, no migrations, no test changes.
- [ ] PR reviewer can read `ARCHITECTURE.md` + `CLAUDE.md` cold and end up with the same mental model that the reactor + effector code in `src/app/modules/ai/lifecycle/` actually implements.
