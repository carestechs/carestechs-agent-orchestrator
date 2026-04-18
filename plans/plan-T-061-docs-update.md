# Implementation Plan: T-061 — Documentation updates

## Task Reference
- **Task ID:** T-061
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-046, T-060

## Overview
Per CLAUDE.md's Documentation Maintenance Discipline table: FEAT-002 changes runtime behavior, status transitions, response shapes, and adds architectural components. Update the affected docs in the same PR as the feature.

## Steps

### 1. Modify `docs/data-model.md`
- **Run entity section**: expand "Business Rules" to document status transitions: `pending → running → (completed|failed|cancelled)`. Note: `COMPLETED` iff `stop_reason ∈ {DONE_NODE, POLICY_TERMINATED}`; `FAILED` iff `stop_reason ∈ {BUDGET_EXCEEDED, ERROR}`.
- **Step entity section**: expand "Business Rules" to document status transitions: `pending → dispatched → in_progress → (completed|failed)`. Monotonic (reconciliation enforces).
- **PolicyCall**: clarify "append-only once inserted; fields never mutated".
- **Changelog entry**:
  ```
  ## Changelog
  - 2026-04-17 — FEAT-002 — Documented Run and Step status transitions;
    clarified mapping StopReason → RunStatus; no schema changes.
  ```

### 2. Modify `docs/api-spec.md`
- `POST /api/v1/runs`: confirm 202 is the success code; update response sample to show a populated `RunSummary` (not a stub 501).
- `GET /runs/*`: update response samples to show real data (remove any placeholder "501 stub" callouts).
- `POST /runs/{id}/cancel`: confirm 200 on cancel; document idempotency.
- `/agents`: update response sample to show real agent entries with `availableNodes`.
- **Changelog entry**: "2026-04-17 — FEAT-002 — Control-plane stubs replaced with real responses; no contract changes."

### 3. Modify `docs/ARCHITECTURE.md`
- Add section "Runtime Loop Components":
  - `runtime.run_loop` — the AD-3 seam.
  - `RunSupervisor` — in-process task registry; single-worker constraint.
  - `JsonlTraceStore` — AD-5 v1 implementation.
  - `reconciliation.next_step_state` — pure state-machine helper.
- Update AD-5 section to mention the JSONL impl is live; v2 Postgres migration remains a future concern.
- **Changelog entry** at bottom.

### 4. Modify `CLAUDE.md`
- Add pattern under "Patterns to Follow": "Each runtime-loop iteration opens its own `AsyncSession`; never share with request handlers."
- Add anti-pattern under "Anti-Patterns to Avoid": "Don't run multiple uvicorn workers in v1 — the supervisor is process-local; concurrent workers duplicate spawns."
- Add the StopReason → RunStatus mapping table under a new "Runtime loop" subsection.
- Add the webhook-reconciliation priority (persist first, reconcile second, wake third).

### 5. Modify `docs/ui-specification.md`
- Update `orchestrator run` entry to document `--wait` and exit-code table (`0 completed`, `1 failed/error`, `2 cancelled`, `3 timeout/unknown`).
- Update `runs` subcommand entries to reflect real behavior (no more "not implemented yet" notes).
- Leave `runs trace` as deferred with a forward-reference to FEAT-004.

### 6. Verify

- Every file has a dated changelog entry.
- No doc claims behavior the shipped code doesn't have.
- Cross-check with `tests/integration/test_control_plane_real.py`: every documented response shape is asserted.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/data-model.md` | Modify | Transition tables, changelog. |
| `docs/api-spec.md` | Modify | Real response samples, changelog. |
| `docs/ARCHITECTURE.md` | Modify | Runtime Loop section, AD-5 live note, changelog. |
| `CLAUDE.md` | Modify | New pattern + anti-pattern + runtime subsection. |
| `docs/ui-specification.md` | Modify | CLI exit-code table, changelog. |

## Edge Cases & Risks
- Docs drift is inevitable unless verified against code. Add a small CI step (future IMP) that diffs documented response shapes vs a golden `/openapi.json` export. Out of scope for FEAT-002.
- Keep changelog entries terse — one line each, dated, FEAT-ref.

## Acceptance Verification
- [ ] Each of the 5 docs has a 2026-04-17 FEAT-002 changelog entry.
- [ ] CLAUDE.md's new entries are reflected by actual code constraints.
- [ ] No contradictions between docs and the merged implementation.
- [ ] README (T-062) does not duplicate the content — it links.
