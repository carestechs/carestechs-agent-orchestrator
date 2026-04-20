# Implementation Plan: T-135 — Docs + FEAT-006 rc2 closeout

## Task Reference
- **Task ID:** T-135
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-134

## Overview
Reconcile docs with the engine-backed implementation. Flip FEAT-006 status to `Delivered — v0.6.0-rc2`. Update the data-model, api-spec, and architecture docs to reflect the split of responsibility between engine (state) and orchestrator (auxiliary).

## Steps

### 1. Modify `docs/data-model.md`
- `WorkItem`: drop `status`, `locked_from` rows from the field table; add `engine_item_id uuid NOT NULL UNIQUE`.
- `Task`: drop `status`, `deferred_from`; add `engine_item_id`.
- Add new entity sections: `EngineWorkflow`, `PendingSignalContext`.
- Remove the `WorkItemStatus` / `TaskStatus` enum sections as DB constraints (they remain as Python enums for DTO + reactor dispatch).
- Add changelog entry: `2026-XX-XX — FEAT-006 rc2: state moved to flow engine; ...`

### 2. Modify `docs/api-spec.md`
- Add `POST /hooks/engine/lifecycle/item-transitioned` under the webhook section.
- Under the FEAT-006 signal endpoints, add a note: "State is persisted in the flow engine. Replies to the signal endpoint return synchronously once the engine transition succeeds; auxiliary audit rows (`Approval`, `TaskAssignment`, etc.) are written asynchronously by the engine webhook reactor."
- Changelog entry.

### 3. Modify `docs/ARCHITECTURE.md`
- New subsection "Deterministic flow state in the engine (FEAT-006)":
  - Two workflows registered in the engine on startup.
  - Orchestrator signal handlers POST state transitions to the engine.
  - Engine validates + emits webhook.
  - Orchestrator reactor consumes the webhook, fires derivations, writes audit rows.
  - Diagram of the two-phase flow.
- Add to the Module Ownership table: `engine_workflows`, `pending_signal_context` owned by `ai` module.
- Update AD-1 commentary: "FEAT-006 is the first consumer of the engine as shared state. FEAT-005 still uses it only for node dispatch."
- Changelog entry.

### 4. Modify `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md`
- Status: `Delivered — v0.6.0-rc2`.
- Rewrite §14 (Delivery Notes) to describe the rc1 → rc2 delta:
  - rc1 implemented the signal surface with orchestrator-owned state (architectural drift from the design doc).
  - rc2 realigns: engine owns state via `Workflows`/`Items`/`Transitions` API; orchestrator becomes the richer audit + routing layer.
  - AC status after rc2: AC-9 now formally ✅ (engine is demonstrably in the loop).
- Keep AC-7 (merge gating) open — still pending FEAT-007.

### 5. Modify `CLAUDE.md`
- Key Directories: add `modules/ai/lifecycle/` contents — list each submodule (`engine_client.py`, `bootstrap.py`, `reactor.py`, `service.py`, `tasks.py`, `work_items.py`, `approval_matrix.py`, `idempotency.py`, `declarations.py`).
- Patterns & Anti-Patterns: add
  - "Lifecycle state transitions go through the flow engine; the orchestrator does not duplicate state."
  - "Auxiliary rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) are written by the engine-webhook reactor, not the signal endpoint. Rejections are the exception — they don't produce engine transitions, so rejection `Approval` rows are written inline."

### 6. Update `tasks/FEAT-006-rc2-tasks.md`
- Flip all tasks to "Completed" with dates.

### 7. Update `README.md` Self-Hosted section
- Mention both engines: `docker compose up -d postgres flow-engine` is now the dev baseline.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/data-model.md` | Modify | Reconcile + changelog. |
| `docs/api-spec.md` | Modify | Add engine webhook + changelog. |
| `docs/ARCHITECTURE.md` | Modify | New FEAT-006 subsection + changelog. |
| `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md` | Modify | Status + rc2 delta. |
| `CLAUDE.md` | Modify | Directory list + patterns. |
| `tasks/FEAT-006-rc2-tasks.md` | Modify | Status flips. |
| `README.md` | Modify | Dev baseline note. |

## Edge Cases & Risks
- **AC-10 E2E narrative change.** The feature-brief says "the test exercises all 14 signals"; update wording to "exercises all 14 signals + 14 corresponding engine webhooks."
- **Existing FEAT-005 docs.** FEAT-005's use of the engine (for run dispatch) is unchanged; don't conflate with FEAT-006's use (for item state).

## Acceptance Verification
- [ ] All four doc files reflect rc2.
- [ ] FEAT-006 brief explicitly notes the rc1 → rc2 delta.
- [ ] AC-9 status updated to formally ✅.
- [ ] Changelog entries on `data-model.md`, `api-spec.md`, `ARCHITECTURE.md`.
- [ ] `CLAUDE.md` reflects new submodules and patterns.
- [ ] Post-generation checklist from `.ai-framework/prompts/feature-tasks.md` passes.
