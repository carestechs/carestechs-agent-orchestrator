# Implementation Plan: T-106 — Documentation sweep + changelogs + stakeholder metric flip

## Task Reference
- **Task ID:** T-106
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-105

## Overview
Close the doc loop per CLAUDE.md's Documentation Maintenance Discipline. Seven files + one changelog bump + adapter-thin test extension. This is the PR that flips Stakeholder Success Metric #1 to "Met," citing IMP-002.

## Steps

### 1. Modify `CLAUDE.md`
Under Patterns & Anti-Patterns → Patterns, add a new entry:
```markdown
- **Lifecycle agent — stages as tools.** The `lifecycle-agent@0.1.0` YAML
  declares 8 stage nodes; each stage maps to exactly one tool under
  `modules/ai/tools/lifecycle/`. Implementation is operator-signaled:
  `wait_for_implementation` returns `PauseForSignal`, the runtime suspends
  without dispatching to the engine, and `POST /api/v1/runs/{id}/signals`
  with `name=implementation-complete` wakes the loop. Correction attempts
  are bounded by `LIFECYCLE_MAX_CORRECTIONS` (default 2); exceeding the
  bound terminates the run with `stop_reason=error,
  final_state.reason=correction_budget_exceeded`.
```

Under Quick Reference → Key Directories, extend with `agents/lifecycle-agent@0.1.0.yaml` and `tools/lifecycle/`.

### 2. Modify `docs/ARCHITECTURE.md`
Under Runtime Loop Components, add:
```markdown
- **Lifecycle agent** (FEAT-005) — First concrete agent definition at
  `agents/lifecycle-agent@0.1.0.yaml`. Drives the ia-framework's 8-stage
  loop. Pauses for operator input at the `implementation` stage; wakes via
  `POST /api/v1/runs/{id}/signals`. The `RunSupervisor.await_signal` /
  `deliver_signal` methods provide per-`(run_id, name, task_id)` wakes,
  keyed independently from webhook wakes. Correction attempts bounded by
  `Settings.lifecycle_max_corrections`.
```

Append to Changelog:
`- 2026-04-18 — FEAT-005 — Added lifecycle agent + operator-signal transport to Runtime Loop Components. Introduced PauseForSignal runtime sentinel, RunSignal entity, correction-bound enforcement.`

### 3. Modify `docs/data-model.md`
Finalize the `RunSignal` section drafted in T-088. Under `AgentDefinition` (or where agents are documented), mention the new `policy.system_prompts` field. Append to Changelog:
`- 2026-04-18 — FEAT-005 — Added RunSignal entity (operator-injected signals, dedupe_key unique constraint). Added AgentDefinition.policy.system_prompts field (per-node prompt file references; validated at load). Added final_state.reason=correction_budget_exceeded as a documented Run.final_state variant.`

### 4. Modify `docs/api-spec.md`
Finalize `POST /runs/{id}/signals` docs drafted in T-098. Add to Endpoint Summary table. Add `RunSignalDto` under Shared DTOs. Append to Changelog:
`- 2026-04-18 — FEAT-005 — POST /runs/{id}/signals is live: operator-injected signals (name=implementation-complete in v1), persist-first-then-wake, idempotent on (run_id, name, task_id). New RunSignalDto under Shared DTOs.`

### 5. Modify `docs/ui-specification.md`
Finalize `orchestrator tasks mark-implemented` drafted in T-099. Add to Command Inventory. Append to Changelog:
`- 2026-04-18 — FEAT-005 — orchestrator tasks mark-implemented T-XXX --run-id <id>: new CLI to POST implementation-complete signals. Exit codes 0/1/2/3 per the project's exit-code semantics.`

### 6. Modify `README.md`
Add a new section under "First Run" (max 30 new lines):
```markdown
## Self-hosted feature delivery

The orchestrator can drive its own feature lifecycle. Starting point:

1. Create a work item at `docs/work-items/FEAT-XXX.md` using the
   template at `.ai-framework/templates/feature-brief.md`.
2. Start the run:
   ```
   uv run orchestrator run lifecycle-agent@0.1.0 \
       --intake workItemPath=docs/work-items/FEAT-XXX.md --follow
   ```
3. At the `implementation` stage the run pauses. Open the plan file
   the agent wrote (`plans/plan-T-001-*.md`) in Claude Code, land
   the change, commit.
4. Signal back:
   ```
   uv run orchestrator tasks mark-implemented T-001 \
       --run-id <run-id> --commit-sha $(git rev-parse HEAD)
   ```
5. The agent reviews, corrects if needed, and closes the work item.

Proof artifact: `docs/work-items/IMP-002-lifecycle-proof.md` — the first
feature shipped by the orchestrator.
```

### 7. Modify `docs/stakeholder-definition.md`
In the Success Criteria list at the top:
```markdown
  1. The orchestrator drives at least one real feature of its *own*
     codebase end-to-end (brief → closed work item) without a human
     routing between stages. **Met** — see `docs/work-items/IMP-002-lifecycle-proof.md`
     (2026-04-18).
```

In the Success Metrics table:
```markdown
| Self-hosted feature delivery | **≥1 feature — met (IMP-002, 2026-04-18)** | Work item closed; git history shows orchestrator-driven commits per stage |
```

### 8. Modify `docs/work-items/FEAT-005-lifecycle-agent.md`
Flip Status: Completed. Add a `Completed | 2026-04-18` row to the Identity table.

### 9. Modify `tests/test_adapters_are_thin.py`
Extend the forbidden-import enforcement:
```python
_LIFECYCLE_QUARANTINES = {
    "subprocess": {"src/app/modules/ai/tools/lifecycle/git.py"},
    "yaml": {"src/app/modules/ai/agents.py"},
    # anthropic quarantine already enforced from FEAT-003
}
```
Update the test body to scan modules under `src/app/` and fail if any file imports a quarantined module from outside its allow-list.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `CLAUDE.md` | Modify | New "Lifecycle agent" pattern entry + Key Directories update. |
| `docs/ARCHITECTURE.md` | Modify | Runtime Loop Components entry + changelog. |
| `docs/data-model.md` | Modify | Finalize RunSignal + note system_prompts + changelog. |
| `docs/api-spec.md` | Modify | Finalize signals endpoint + DTO + summary + changelog. |
| `docs/ui-specification.md` | Modify | Finalize tasks command + changelog. |
| `README.md` | Modify | New Self-hosted section. |
| `docs/stakeholder-definition.md` | Modify | Success Metric #1 flipped to Met. |
| `docs/work-items/FEAT-005-lifecycle-agent.md` | Modify | Status: Completed. |
| `tests/test_adapters_are_thin.py` | Modify | Add subprocess + yaml quarantines. |

## Edge Cases & Risks
- **Changelog entry duplication**: don't re-add entries that T-088 / T-098 / T-099 already wrote if those tasks were merged with drafts. Check each doc's existing changelog before appending.
- **README length drift**: keep the new section ≤ 30 lines. If it grows, move detail to a dedicated `docs/self-hosted-delivery.md` and link.
- **Adapter-thin test false positives**: `yaml` is imported by `agents.py` already from FEAT-002; the quarantine rule should match "only this file may import yaml." Verify by running the extended test locally first — any new violation surfaces an actual drift.
- **Stakeholder-definition claim vs. reality**: only flip the metric AFTER T-105's branch exists and IMP-002 is Completed. If T-105 is blocked, this PR cannot land.
- **Evidence link integrity**: the stakeholder-definition.md and README.md links point at IMP-002 and the evidence trace. Verify the paths resolve after merge (relative paths in markdown can break when rendered by different viewers).

## Acceptance Verification
- [ ] All 7 user-facing docs have 2026-04-18 FEAT-005 changelog entries (where applicable).
- [ ] `docs/stakeholder-definition.md` Success Criteria #1 + Success Metrics table flipped to "Met (IMP-002)."
- [ ] `docs/work-items/FEAT-005-lifecycle-agent.md` Status: Completed.
- [ ] `README.md` self-hosted section ≤ 30 new lines.
- [ ] `tests/test_adapters_are_thin.py` quarantines `subprocess` and `yaml`.
- [ ] No doc claims a behavior the shipped code doesn't have (spot-check each changelog line).
- [ ] `uv run pytest tests/test_adapters_are_thin.py` green.
- [ ] `uv run pytest` full suite green.
