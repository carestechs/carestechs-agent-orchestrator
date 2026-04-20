# Implementation Plan: T-127 — Docs sweep + changelogs (AC-12)

## Task Reference
- **Task ID:** T-127
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-125, T-126

## Overview
Final documentation reconciliation. The specs were scaffolded before task generation; this task catches drift between scaffolded content and what actually shipped, updates architecture/CLAUDE.md references, flips FEAT-006 status, and updates the stakeholder metric.

## Steps

### 1. Verify `docs/data-model.md` drift
- For each shipped entity (`WorkItem`, `Task`, `TaskAssignment`, `Approval`, `TaskPlan`, `TaskImplementation`, `LifecycleSignal`), confirm column list matches the migrations.
- Add `TaskPlan`, `TaskImplementation`, `LifecycleSignal` sections if missing (these were introduced during implementation in T-117, T-118, T-114 and may not be in the scaffolded doc).
- Update the Module Ownership table to include all new entities.
- Add/update the Changelog entry with a note on any drift.

### 2. Verify `docs/api-spec.md` drift
- Confirm every shipped endpoint matches the spec (path, request body, response shape, status codes).
- Update the Endpoint Summary if any route moved or renamed.
- Update the Changelog if anything drifted.

### 3. Modify `docs/ARCHITECTURE.md`
- Add a new section "Deterministic Lifecycle Flow (FEAT-006)" describing:
  - Engine-as-passive / orchestrator-owns-intake model.
  - Work-item and task state machines.
  - 14-signal intake surface + GitHub webhook.
  - Approval matrix.
  - Coexistence with FEAT-005.
- Append a Changelog entry.

### 4. Modify `CLAUDE.md`
- Key Directories: add `modules/ai/lifecycle/`, `modules/ai/github/`, `modules/ai/webhooks/github.py`.
- Patterns & Anti-Patterns: add bullets for
  - "Derived transitions fire in the orchestrator, not the engine."
  - "Approval matrix is a pure function; routes consult it inside `SELECT FOR UPDATE`."
  - "Signal idempotency via `lifecycle_signals` hash-key — short-circuit before side effects."

### 5. Modify `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md`
- Status: `Not Started` → `Completed`.
- Add `Completed: 2026-XX-XX` below the status row.

### 6. Modify `docs/stakeholder-definition.md`
- Review Success Metric #1. If the multi-actor capability is now demonstrated (T-125 E2E green), update wording (e.g., "≥1 feature shipped end-to-end via orchestrator across admin + dev + agent actors").

### 7. Modify `README.md`
- Self-Hosted section: brief paragraph pointing at FEAT-006 as the collaborative path, FEAT-005 as the solo-operator path. Link both feature briefs.

### 8. Re-run the post-generation checklist from `.ai-framework/prompts/feature-tasks.md`
- All acceptance criteria covered by tasks? ✓
- Dependencies form a DAG? ✓
- Scope-lock respected? ✓
- Document outcome in the commit message or a short note in FEAT-006.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/data-model.md` | Modify | Drift reconciliation + new tables. |
| `docs/api-spec.md` | Modify | Drift reconciliation. |
| `docs/ARCHITECTURE.md` | Modify | New section + changelog. |
| `CLAUDE.md` | Modify | Directory list + patterns. |
| `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md` | Modify | Status flip. |
| `docs/stakeholder-definition.md` | Modify | Metric wording. |
| `README.md` | Modify | Self-Hosted paragraph. |

## Edge Cases & Risks
- **Scope creep** — this task closes loops; resist the urge to re-architect anything. If substantive changes are needed, file a follow-up.
- **Drift catches** — if the scaffolded data-model/api-spec sections lied about a field, fix the doc, don't silently update the code. The code is authoritative.

## Acceptance Verification
- [ ] Every shipped entity + endpoint reflected in the specs.
- [ ] Changelog entries present and accurate.
- [ ] `ARCHITECTURE.md` has FEAT-006 section.
- [ ] `CLAUDE.md` updated.
- [ ] FEAT-006 brief Status = Completed.
- [ ] Stakeholder metric updated if appropriate.
- [ ] README self-hosted section updated.
- [ ] Post-generation checklist passes.
