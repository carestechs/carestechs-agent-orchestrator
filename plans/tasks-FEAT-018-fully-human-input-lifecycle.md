# Task Breakdown — FEAT-018: Fully human-input lifecycle variant

## Foundation

---

### T-001: YAML agent definition — `lifecycle-agent@0.6.0-human`

**Type:** Backend  
**Workflow:** standard  
**Complexity:** S  
**Dependencies:** None

**Description:**  
Create `agents/lifecycle-agent@0.6.0-human.yaml` with `flow.policy: deterministic`. The graph is identical to `@0.5.0-manual` except `load_work_item`, `generate_tasks`, `generate_plan`, and `generate_mockup` are removed. `start` transitions directly to `confirm_brief`; `confirm_tasks` transitions directly to `propose_tasks` (no intermediate generate step); `assign_task` branches directly to `confirm_mockup` (mockup) or `confirm_plan` (other); `confirm_plan` leads to `approve_plan`. All rejection edges, budget-guard nodes (`terminate_correction_budget`, `terminate_rejection_budget`), and predicate names are unchanged.

**Rationale:**  
The YAML is the authoritative flow graph. Without it the agent can't be loaded, listed, or dispatched.

**Acceptance Criteria:**
- [ ] File parses cleanly through `list_agents()` with no validation errors.
- [ ] Nodes list contains no `load_work_item`, `generate_tasks`, `generate_plan`, or `generate_mockup`.
- [ ] `flow.entryNode` is `start`; first transition is `start → confirm_brief`.
- [ ] All terminal nodes declared in `terminalNodes`.
- [ ] `intakeSchema` matches `@0.5.0-manual` (unchanged).

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.6.0-human.yaml` — create

**Technical Notes:**  
Copy `@0.5.0-manual` as a starting point, then drop the four generate nodes and their outgoing transitions. `confirm_brief` takes over as the first non-start node. The transition from `load_work_item` → `confirm_brief` in `@0.5.0-manual` becomes `start → confirm_brief` here (direct, no branch needed since there is no rejection from a generate step before brief).

---

## Backend

---

### T-002: Extend payload schemas for operator-authored content

**Type:** Backend  
**Workflow:** standard  
**Complexity:** M  
**Dependencies:** None

**Description:**  
Extend three existing schemas in `lifecycle_manual_patches.py` to accept operator-supplied content that previously came from LLM steps. `_WorkItemCorrection` gains `summary: str | None` and `acceptance_criteria: list[str] | None`. `_TaskInput` gains `kind: Literal["feature", "mockup", "bug", "chore"] | None = None`. `MockupApprovedPayload` gains `mockup_html: str | None` (present only when `verdict="approve"`). Update the corresponding patch builders: `apply_brief_approval` must write the provided `summary` and `acceptance_criteria` into `LifecycleMemory.work_item`; `apply_mockup_approval` must write `mockup_html` to `RunMemory.mockups[task_id]` when present on an approve payload.

**Rationale:**  
In `@0.6.0-human` every checkpoint is the sole source of content for its artefact. The patch builders must accept and persist the full artefact, not just edits on top of an LLM-generated draft.

**Acceptance Criteria:**
- [ ] `BriefConfirmedPayload` with `payload.workItem.summary = "..."` validates without error.
- [ ] `apply_brief_approval` writes `work_item.title`, `work_item.summary` (if provided) into `LifecycleMemory` using `write_lifecycle_memory`.
- [ ] `_TaskInput` with `kind="mockup"` validates; missing `kind` defaults to `None` (caller's patch builder uses `LifecycleTask` default `"feature"`).
- [ ] `MockupApprovedPayload` with `verdict="approve"` and `mockupHtml="<!DOCTYPE...>"` validates; `apply_mockup_approval` writes it to `RunMemory.mockups[task_id].mockup_html`.
- [ ] `MockupApprovedPayload` with `verdict="reject"` and `mockupHtml` present is accepted (field ignored on reject in patch builder).
- [ ] All existing `@0.5.0-manual` signals remain valid — no breaking changes to existing schemas.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — extend `_WorkItemCorrection`, `_TaskInput`, `MockupApprovedPayload`; update `apply_brief_approval`, `apply_tasks_approval` (for `kind`), `apply_mockup_approval`

**Technical Notes:**  
`_WorkItemCorrection` feeds into `apply_brief_approval`, which today only patches `title` and `type`. The new `summary` field must be mapped to `LifecycleMemory.work_item` — check `WorkItemRef` for available fields and extend if needed. `acceptance_criteria` can be stored as a list of strings on `WorkItemRef` or in a separate top-level memory key; keep it consistent with how `load_work_item`'s patch builder stores it today (inspect `_patch_load_work_item` and `LoadWorkItemResult`). Don't change `extra="forbid"` on any schema — typos in DevHub payloads surface as 400 errors, not silent drops.

---

### T-003: `confirm_brief` intake builder — surface raw work item content

**Type:** Backend  
**Workflow:** standard  
**Complexity:** S  
**Dependencies:** None

**Description:**  
Add an `intake_builder` for the `confirm_brief` `HumanExecutor` binding in `@0.6.0-human`. In `@0.5.0-manual`, `confirm_brief` fires after `load_work_item` has already populated `RunMemory` with a processed brief — the operator reviews LLM output. In `@0.6.0-human`, `RunMemory` is empty at this point; the operator needs the raw work item content to author the brief themselves. The intake builder reads `Run.intake.workItem.content` (the raw markdown body stored at run start via FEAT-014) and surfaces it as `nodeInputs.workItemContent`, alongside `nodeInputs.workItemId` and `nodeInputs.workItemKind`.

**Rationale:**  
Without this, `confirm_brief` shows an empty checkpoint — the operator has no material to work from when composing the brief.

**Acceptance Criteria:**
- [ ] `nodeInputs` at `confirm_brief` includes `workItemId`, `workItemKind`, and `workItemContent` (the raw markdown from `Run.intake.workItem.content`).
- [ ] If `content` is absent from intake (e.g., legacy run started without body), `workItemContent` is `null` — no error.
- [ ] The intake builder is used only for the `@0.6.0-human` registration; the `@0.5.0-manual` `confirm_brief` binding is unchanged.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — add `intake_for_confirm_brief_human` function
- `src/app/modules/ai/executors/bootstrap.py` — wire it into the `@0.6.0-human` `confirm_brief` `HumanExecutor` registration

**Technical Notes:**  
`HumanExecutor` already accepts an `intake_builder: Callable[[DispatchContext], Awaitable[Mapping[str, Any]]] | None`. The builder receives `DispatchContext`; `ctx.intake` is the run-level intake dict. `ctx.intake.get("workItem", {}).get("content")` should give the body. Keep the function async (consistent with other intake builders like `intake_for_confirm_mockup`).

---

### T-004: `register_lifecycle_v06_human` in `bootstrap.py`

**Type:** Backend  
**Workflow:** standard  
**Complexity:** L  
**Dependencies:** T-002, T-003

**Description:**  
Implement `register_lifecycle_v06_human` as a standalone registration function in `bootstrap.py`. It registers all engine executors (W1 via `EngineCreateExecutor`, `propose_tasks`, `assign_task`, `advance_plan`, `approve_plan`, `submit_implementation`, `approve_review`, `mark_task_done`, `close_work_item`, `correct_implementation`), all human executors (`confirm_brief` with the new intake builder from T-003, `confirm_tasks`, `confirm_assignment`, `confirm_mockup`, `confirm_plan`, `request_implementation`, `human_review_implementation`), and explicit `no_executor("human-input variant — content supplied via signal payload")` exemptions for `load_work_item`, `generate_tasks`, `generate_plan`, `generate_mockup`. Add a companion `_exempt_lifecycle_v06_human` for the no-collaborators path.

**Rationale:**  
The registration function is how the runtime discovers what executor to invoke for each node. Without it, `validate_executor_coverage()` fails at startup and the agent can't run.

**Acceptance Criteria:**
- [ ] `validate_executor_coverage()` passes at startup when `lifecycle-agent@0.6.0-human.yaml` is loaded.
- [ ] No `LLMContentExecutor` or `AgentPlatformExecutor` is registered for any node of `@0.6.0-human`.
- [ ] `confirm_brief` binding uses `intake_for_confirm_brief_human` as its `intake_builder`.
- [ ] `confirm_mockup` binding uses `apply_mockup_approval` (with the extended schema from T-002) as its `memory_patch_builder`.
- [ ] `apply_brief_approval` (extended in T-002) is wired as `confirm_brief`'s `memory_patch_builder`.
- [ ] `_exempt_lifecycle_v06_human` is called when `v03_collaborators is None` (keeps coverage validator happy in test mode).

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — add `register_lifecycle_v06_human`, `_exempt_lifecycle_v06_human`; export both in `__all__`

**Technical Notes:**  
Copy the engine executor block and human executor block from `register_lifecycle_v04_manual` as a starting template — it has the same set minus the `generate_*` nodes. Do not call `register_lifecycle_v04_manual` or `register_lifecycle_v05_manual` internally; the function must be standalone to avoid coupling to the prior variants' optional LLM bindings. The `confirm_mockup` `intake_builder` can reuse the existing `intake_for_confirm_mockup` from `@0.5.0-manual` — in `@0.6.0-human` `mockups[task_id]` won't exist yet when the checkpoint fires, so the builder will surface `mockupHtml: null`; that's correct — DevHub shows an empty input area for the operator to fill.

---

### T-005: Route `@0.6.0-human` in `register_all_executors`

**Type:** Backend  
**Workflow:** standard  
**Complexity:** S  
**Dependencies:** T-004

**Description:**  
Add `elif agent.ref.startswith("lifecycle-agent@0.6")` routing in `register_all_executors`, positioned before the `@0.5` check. When `v03_collaborators` is provided, call `register_lifecycle_v06_human`; when absent, call `_exempt_lifecycle_v06_human`. Export `register_lifecycle_v06_human` in `bootstrap.__all__`.

**Rationale:**  
Without this routing, loading `lifecycle-agent@0.6.0-human.yaml` at boot silently leaves all nodes uncovered and the coverage validator halts the process.

**Acceptance Criteria:**
- [ ] Starting the service with `lifecycle-agent@0.6.0-human.yaml` present does not raise a coverage error.
- [ ] The `@0.5`, `@0.4`, and `@0.3` routing branches are untouched.
- [ ] `register_lifecycle_v06_human` appears in `bootstrap.__all__`.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — add routing branch, update `__all__`

---

## Testing

---

### T-006: Unit tests — schemas, patch builders, and coverage

**Type:** Testing  
**Workflow:** standard  
**Complexity:** M  
**Dependencies:** T-001, T-002, T-003, T-004, T-005

**Description:**  
Add a test module `tests/modules/ai/executors/test_lifecycle_v06_human.py` covering: (1) the extended payload schemas from T-002 — valid and invalid inputs for `BriefConfirmedPayload` with `summary`/`acceptanceCriteria`, `_TaskInput` with `kind`, and `MockupApprovedPayload` with `mockupHtml`; (2) patch builder behaviour — `apply_brief_approval` with `summary` writes correct `LifecycleMemory`; `apply_mockup_approval` with `mockupHtml` on approve writes to `mockups[task_id]`; (3) executor coverage — call `register_lifecycle_v06_human` against a real `ExecutorRegistry` + stub collaborators and assert every node in the YAML has a registered executor or `no_executor` exemption; (4) no LLM imports — assert `register_lifecycle_v06_human` does not import `core.llm` or `AnthropicLLMProvider` (consistent with `test_runtime_deterministic_is_pure.py` guard style).

**Rationale:**  
The schemas and patch builders are the contract between DevHub signals and `RunMemory`. A regression here silently corrupts memory and breaks downstream steps.

**Acceptance Criteria:**
- [ ] All new schema validation cases pass (valid payloads accepted, missing required fields and extra fields rejected).
- [ ] `apply_brief_approval` with `summary="My brief"` produces a `LifecycleMemory` with `work_item.title` and the summary persisted.
- [ ] `apply_mockup_approval` approve + `mockupHtml="<html>…</html>"` writes `mockups["T-001"]["mockup_html"]`.
- [ ] Coverage test passes — every node in `lifecycle-agent@0.6.0-human.yaml` is covered.
- [ ] LLM import guard asserts `core.llm` is not imported by the new registration path.
- [ ] `uv run pytest tests/modules/ai/executors/test_lifecycle_v06_human.py` green.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_lifecycle_v06_human.py` — create

---

## Documentation

---

### T-007: Update `docs/api-spec.md` with `@0.6.0-human` signal contracts

**Type:** Documentation  
**Workflow:** standard  
**Complexity:** S  
**Dependencies:** T-002

**Description:**  
Add a subsection under the manual-variant signals section documenting the `@0.6.0-human` specific extensions: the extended `brief-confirmed` payload (`summary`, `acceptanceCriteria`), the `_TaskInput.kind` field accepted in `tasks-confirmed`, and the `mockupHtml` field in `mockup-approved`. Add a changelog entry.

**Rationale:**  
API spec is the DevHub integration contract. Missing documentation means DevHub ships without the fields.

**Acceptance Criteria:**
- [ ] `brief-confirmed` section shows the extended `workItem` block with `summary` and `acceptanceCriteria`.
- [ ] `tasks-confirmed` `_TaskInput` table includes `kind` field.
- [ ] `mockup-approved` section shows `mockupHtml` field on the approve path.
- [ ] Changelog entry dated 2026-07-13 for FEAT-018.

**Files to Modify/Create:**
- `docs/api-spec.md` — extend signal docs, add changelog entry

---

## Summary

| Group | Tasks | Count |
|-------|-------|-------|
| Foundation | T-001 | 1 |
| Backend | T-002, T-003, T-004, T-005 | 4 |
| Testing | T-006 | 1 |
| Documentation | T-007 | 1 |
| **Total** | | **7** |

**Complexity distribution:** 3×S, 2×M, 1×L, 1×XL (none)

**Critical path:** T-002 → T-004 → T-005 → T-006

**Risks / open questions:**
- `WorkItemRef` may not have a `summary` field today. T-002 must check whether to extend the model or store the operator-supplied summary in a separate top-level memory key. Keeping it on `WorkItemRef` is cleaner; adding a field is a `write_lifecycle_memory` / `read_lifecycle_memory` change that must stay backward-compatible (field optional, defaults to empty string).
- `confirm_mockup`'s `intake_for_confirm_mockup` (from `@0.5.0-manual`) will return `mockupHtml: null` in `@0.6.0-human` since no `generate_mockup` ran. DevHub needs to handle the null case (empty paste area rather than a preview). No orchestrator change needed — document in T-007.
