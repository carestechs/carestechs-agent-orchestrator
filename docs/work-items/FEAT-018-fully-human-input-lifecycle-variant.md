# Feature Brief: FEAT-018 — Fully human-input lifecycle variant

> **Purpose**: Add a new `lifecycle-agent@0.6.0-human` agent variant where every content-generating step is removed and the operator supplies all artefacts directly through signal payloads. The orchestrator becomes a pure state-machine coordinator — it manages engine transitions, signals, human checkpoints, and memory, but makes zero LLM or platform calls of its own. The operator produces content externally (e.g., by running Claude Code locally) and feeds it in through DevHub.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-018 |
| **Name** | Fully human-input lifecycle variant |
| **Type** | Feature |
| **Status** | Proposed |
| **Priority** | Medium |
| **Proposed By** | Engineering (2026-07-13) |
| **Date Created** | 2026-07-13 |

---

## 2. Motivation

### Cost

`lifecycle-agent@0.5.0-manual` still calls the LLM (or agent-platform) for `load_work_item`, `generate_tasks`, `generate_plan`, and `generate_mockup`. Running a small 3-task work item cost ~$2 in a single session, driven primarily by using `claude-opus-4-8` for every step. For day-to-day testing and iteration this is prohibitive — a developer who runs 10 work items per week spends $20 just on scaffolding content that they then review and often rewrite anyway.

### Operator control

The current manual variant gives the operator veto power (rejection) but not authoring power. An operator who already knows the right task breakdown cannot skip the LLM step — they must wait for it, then edit its output. This is wasteful when the operator's own judgment is faster and more accurate.

### Tool flexibility

Operators running Claude Code locally (via subscription, not API tokens) can produce brief summaries, task lists, and implementation plans at effectively zero marginal cost. The orchestrator should be able to accept that content directly rather than re-generating it.

---

## 3. Design

### Flow graph — `lifecycle-agent@0.6.0-human`

Remove `load_work_item`, `generate_tasks`, `generate_plan`, and `generate_mockup`. Every former "generate" step is replaced by the immediately-following human checkpoint, which now carries the full artefact in its signal payload.

```
start
  └─→ confirm_brief          ← operator supplies brief (title, summary, type)
        └─→ register_work_item
              └─→ confirm_tasks      ← operator supplies full task list
                    └─→ propose_tasks
                          └─→ [per task loop]
                                confirm_assignment   ← operator supplies assignee
                                  └─→ assign_task
                                        ├─(kind=mockup)→ confirm_mockup   ← operator supplies mockup HTML
                                        │                    └─→ confirm_plan
                                        └─(other)──────────→ confirm_plan  ← operator supplies plan markdown
                                                                 └─→ approve_plan
                                                                       └─→ request_implementation
                                                                             └─→ human_review_implementation
                                                                                   ├─(pass)→ approve_review → mark_task_done → [next task or close]
                                                                                   └─(fail)→ correct_implementation → [loop or terminate]
```

All rejection edges and the correction/rejection budget guards from `@0.5.0-manual` are preserved unchanged.

### Payload changes

Most signal schemas already support operator-supplied content. Three need extension:

**`brief-confirmed`** — must now accept the full work-item summary since there is no prior `load_work_item` step to produce it. Extend `BriefConfirmedPayload.work_item` to include `summary` and `acceptanceCriteria` fields (in addition to the existing `title` and `type`). On approve the patch builder writes these into `RunMemory` as the canonical brief; the existing `register_work_item` engine step reads from memory as today.

**`confirm_mockup` / `mockup-approved`** — in `@0.5.0-manual` the LLM generates `mockupHtml`; in `@0.6.0-human` the operator supplies it. Extend `MockupApprovedPayload` to include an optional `mockupHtml: str` field (present only on approve). The patch builder writes it to `RunMemory.mockups[task_id]` so downstream steps (plan, implementation) have the same memory shape as the automated path.

**`confirm_tasks`** — already supports full task list replacement via `payload.tasks`. The `_TaskInput` schema currently has `id`, `title`, `summary`, `description`, `complexity`. Add `kind: Literal["feature", "mockup", "bug", "chore"] | None = None` to align with `LifecycleTask.kind` introduced in FEAT-017.

All other signal contracts (`assignment-confirmed`, `plan-confirmed`, `implementation-complete`, `review-completed`) are unchanged.

### `confirm_brief` as the entry point

With no `load_work_item` preceding it, `confirm_brief` fires immediately after `start`. `RunMemory` will have no pre-populated brief at that point. The checkpoint's `nodeInputs` surfaces the raw work item content (from `Run.intake.workItem.content`) so the operator has the source material when composing the payload.

### No LLM, no platform, no `generate_*` nodes

`register_lifecycle_v06_human` in `bootstrap.py` does not register any `LLMContentExecutor` or `AgentPlatformExecutor` bindings. Every node either maps to an existing engine or human executor, or carries a `no_executor("human-input variant — content supplied via signal payload")` exemption. The import-quarantine tests for `runtime_deterministic.py` are unaffected (no new LLM imports).

### Bootstrap wiring

`register_lifecycle_v06_human` is a standalone function — it does not modify or call `register_lifecycle_v05_manual` or any prior variant's helper. The four removed nodes (`load_work_item`, `generate_tasks`, `generate_plan`, `generate_mockup`) get explicit `no_executor("human-input variant — content supplied via signal payload")` exemptions so `validate_executor_coverage()` passes at startup. All engine executors (W1, W2, T5, T6, T7, T9, T10, W4+W6) and human executors (confirm_brief, confirm_tasks, confirm_assignment, confirm_mockup, confirm_plan, request_implementation, human_review_implementation) are registered directly in `register_lifecycle_v06_human`, mirroring the relevant bindings from the prior variants without inheriting their generate-node registrations.

`register_all_executors` routes `lifecycle-agent@0.6.0-human` to `register_lifecycle_v06_human` before the `@0.5.0` and `@0.4.0` checks. Existing variant registrations are untouched.

---

## 4. Operator workflow (intended use)

1. **Open DevHub**, start a run with `lifecycle-agent@0.6.0-human` and the work item.
2. **`confirm_brief`** fires immediately. DevHub shows the raw work item content. The operator composes a brief (or pastes one they generated with Claude Code) and submits it in the `brief-confirmed` payload.
3. **`confirm_tasks`** fires. The operator supplies the task list — titles, complexities, kinds. Tasks can be authored by running the work item through Claude Code locally and formatting the output as `_TaskInput` objects, then pasting into DevHub.
4. **Per task**: `confirm_assignment` (pick assignee) → `confirm_plan` (paste implementation plan, optionally from Claude Code) → `request_implementation` (operator implements or delegates to Claude Code, signals when done with PR URL) → `human_review_implementation` (operator reviews, pass/fail).
5. For mockup tasks: `confirm_mockup` fires with the mockup HTML in the approve payload (operator generates it externally and pastes it in).

The orchestrator handles all engine transitions, memory updates, and state machine progression. The operator owns all content decisions and can use any tool they want externally.

---

## 5. Success Criteria

- [ ] `agents/lifecycle-agent@0.6.0-human.yaml` declared with no `generate_*` nodes; all flows branch correctly via existing predicates.
- [ ] `register_lifecycle_v06_human` registered in `bootstrap.py`; routes before `@0.5.0` check in `register_all_executors`.
- [ ] `validate_executor_coverage()` passes at startup with no uncovered nodes.
- [ ] `BriefConfirmedPayload.work_item` extended to accept `summary` and `acceptanceCriteria`; patch builder writes them to memory.
- [ ] `MockupApprovedPayload` extended to accept `mockupHtml` on approve; patch builder writes it to `RunMemory.mockups[task_id]`.
- [ ] `_TaskInput` gains `kind` field with `LifecycleTask.kind` values.
- [ ] `confirm_brief` `nodeInputs` surfaces `workItem.content` from `Run.intake` so the operator has the source material.
- [ ] End-to-end run with `lifecycle-agent@0.6.0-human` completes through a 1-task work item with all content supplied via signal payloads and zero LLM/platform calls.
- [ ] `docs/api-spec.md` updated with `@0.6.0-human` signal contracts and extended payload schemas.
- [ ] Unit tests for extended payload schemas and patch builders.

---

## 6. Non-Goals

- No new UI in DevHub (format guidance for payload fields is a DevHub concern).
- No pricing or cost comparison tracking (that is IMP-007).
- No agentic executor changes.
- No changes to `@0.3.0`, `@0.4.0-manual`, or `@0.5.0-manual` — they are unaffected.
