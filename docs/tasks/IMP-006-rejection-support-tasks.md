# Task Breakdown: IMP-006 — Rejection support for human checkpoints

> **Source:** `docs/work-items/IMP-006-rejection-support-for-human-checkpoints.md`
> **Scope:** Three rejectable checkpoints (`confirm_brief`, `confirm_tasks`, `confirm_plan`) + shared infrastructure. `confirm_assignment` and `human_review_implementation` are out of scope — they follow engine state changes and need separate investigation.

---

## Phase 0: Preparation (Safety Net)

### T-306: Add unit tests for existing checkpoint payload schemas and patch builders

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Rationale:**
Before modifying payload schemas to accept `verdict`/`feedback` fields, lock down the current behavior so regressions are caught immediately.

**Tasks:**
1. Add tests for `BriefConfirmedPayload` — verify `extra="forbid"` rejects unknown fields, empty payload is valid, title/type overrides work.
2. Add tests for `TasksConfirmedPayload` — verify empty-list rejection, non-empty list accepted.
3. Add tests for `PlanConfirmedPayload` — verify empty payload and plan override.
4. Verify that `apply_brief_correction`, `apply_tasks_correction`, `apply_plan_correction` return empty dict on empty payload.

**Files to Modify:**
- `tests/modules/ai/executors/test_lifecycle_manual_patches.py` (extend existing)

**Acceptance Criteria:**
- [ ] Every payload schema has at least one approve-path and one validation-error test
- [ ] Every patch builder has an empty-payload → empty-patch test
- [ ] All tests pass before any schema changes

---

## Phase 1: Shared Infrastructure

### T-307: Add `checkpoint_approved` predicate to flow_predicates.py

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Rationale:**
A single generic predicate serves all rejectable checkpoints — it reads `result.verdict` from the last dispatch result and returns `True` for `"approve"`, `False` for `"reject"`. Mirrors the existing `review_passed` pattern but uses `verdict` (consistent with `human_review_implementation`'s naming) with a default of `"approve"` for backward compatibility.

**Description:**
Register a `checkpoint_approved` predicate that reads `last.get("verdict")`. Default to `True` when `verdict` is absent (backward compat with existing signals that carry no verdict). Raise `ValueError` on unexpected values.

**Implementation Approach:**
```python
@register("checkpoint_approved")
def _checkpoint_approved(memory: Mapping[str, Any], last: Mapping[str, Any] | None) -> bool:
    if last is None:
        return True
    verdict = last.get("verdict")
    if verdict is None:
        return True  # backward compat: no verdict = approve
    if verdict == "approve":
        return True
    if verdict == "reject":
        return False
    raise ValueError(f"checkpoint verdict must be 'approve' or 'reject'; got {verdict!r}")
```

**Files to Modify:**
- `src/app/modules/ai/flow_predicates.py` — add predicate
- `tests/modules/ai/test_flow_predicates_lifecycle.py` — add tests for approve/reject/missing/invalid

**Acceptance Criteria:**
- [ ] `checkpoint_approved` returns True when verdict is "approve" or absent
- [ ] Returns False when verdict is "reject"
- [ ] Raises ValueError on unexpected verdict values
- [ ] Existing predicates unchanged

---

### T-308: Extend checkpoint payload schemas with verdict and feedback fields

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-306

**Rationale:**
Each rejectable checkpoint's payload schema must accept `verdict` (default `"approve"`) and `feedback` (optional, for rejection context). The `extra="forbid"` policy stays — we're adding explicit fields, not relaxing validation.

**Description:**
Add `verdict: Literal["approve", "reject"] = "approve"` and `feedback: str | None = None` to `BriefConfirmedPayload`, `TasksConfirmedPayload`, and `PlanConfirmedPayload`.

**Files to Modify:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — extend three schemas
- `tests/modules/ai/executors/test_lifecycle_manual_patches.py` — test new fields: approve explicit, reject with feedback, reject without feedback, empty payload still works

**Acceptance Criteria:**
- [ ] `{"verdict": "approve"}` validates and defaults work_item/tasks/plan to None
- [ ] `{"verdict": "reject", "feedback": "reason"}` validates
- [ ] `{}` (empty) validates with verdict defaulting to "approve" (backward compat)
- [ ] Unknown fields still rejected by `extra="forbid"`

---

### T-309: Update patch builders to handle rejection (persist feedback, skip corrections)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-308

**Rationale:**
On rejection, the patch builder should persist the feedback to a top-level `rejections` sidecar in `RunMemory` (so the producing node's LLM prompt can incorporate it on retry) and skip any correction logic. On approve, behavior is unchanged.

**Description:**
Update `apply_brief_correction`, `apply_tasks_correction`, and `apply_plan_correction` to:
1. Parse `verdict` from the payload.
2. If `"reject"`: write `rejections[checkpoint_name] = {feedback, attempt}` to memory, return the patch (no correction fields applied).
3. If `"approve"` (or absent): existing behavior unchanged.

**Files to Modify:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — update three builders
- `tests/modules/ai/executors/test_lifecycle_manual_patches.py` — test rejection patches write to `rejections` sidecar

**Acceptance Criteria:**
- [ ] Rejection persists feedback under `rejections["confirm_brief"]` (or respective checkpoint name)
- [ ] Rejection does NOT apply title/type/tasks/plan corrections
- [ ] Approval behavior is byte-identical to current behavior
- [ ] Rejection accumulates attempts in the sidecar

---

## Phase 2: Flow Graph Changes

### T-310: Add rejection edges to the v0.4.0-manual flow graph

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-307

**Rationale:**
Wire the `checkpoint_approved` predicate at each rejectable checkpoint so the flow resolver routes rejections back to the producing node.

**Description:**
Update `agents/lifecycle-agent@0.4.0-manual.yaml` transitions:

```yaml
# Before:
confirm_brief: [register_work_item]
confirm_tasks: [propose_tasks]
confirm_plan: [approve_plan]

# After:
confirm_brief:
  branch:
    rule: checkpoint_approved
    "true": register_work_item
    "false": load_work_item
confirm_tasks:
  branch:
    rule: checkpoint_approved
    "true": propose_tasks
    "false": generate_tasks
confirm_plan:
  branch:
    rule: checkpoint_approved
    "true": approve_plan
    "false": generate_plan
```

**Files to Modify:**
- `agents/lifecycle-agent@0.4.0-manual.yaml` — change three transitions from list to branch

**Acceptance Criteria:**
- [ ] Flow resolver routes to producing node on reject, next node on approve
- [ ] Agent definition loads without errors (coverage validation passes at startup)
- [ ] v0.3.0 agent is unaffected

---

### T-311: Add rejection budget predicate and enforcement

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-309, T-310

**Rationale:**
Prevent infinite rejection loops. The existing `correction_attempts_under_bound` pattern (from `correct_implementation`) is the model — a `LIFECYCLE_MAX_CHECKPOINT_REJECTIONS` setting caps how many times an operator can reject before the run terminates.

**Description:**
1. Add `LIFECYCLE_MAX_CHECKPOINT_REJECTIONS` setting (default 3) to `config.py`.
2. Add a `checkpoint_rejections_under_bound` predicate that reads the `rejections` sidecar and checks the attempt count for the current checkpoint against the bound.
3. Wire a `terminate_rejection_budget` terminal node and a branch from each producing node (or from the checkpoint itself) that checks the bound before looping back.

Alternative (simpler): skip the budget predicate for now — just use the existing step budget (`max_steps`) as a natural ceiling. If the operator rejects 3 times, that's 6 extra steps (3 produce + 3 confirm), well within the default step budget. Document this as a known ceiling and add the explicit budget in a follow-up if needed.

**Files to Modify:**
- `src/app/config.py` (if explicit budget)
- `src/app/modules/ai/flow_predicates.py` (if explicit budget)
- `agents/lifecycle-agent@0.4.0-manual.yaml` (if explicit budget adds terminal edges)

**Acceptance Criteria:**
- [ ] Rejection loops are bounded (either by explicit budget or step budget)
- [ ] Budget exhaustion produces a clear stop reason

---

## Phase 3: Prompt Integration

### T-312: Include rejection feedback in producing node prompts

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-309, T-310

**Rationale:**
Without feeding rejection feedback into the LLM prompt, the producing node regenerates the same artefact. The feedback must appear in the system or user prompt so the LLM corrects its output.

**Description:**
1. Update the `prompt_context_loader` for `load_work_item`, `generate_tasks`, and `generate_plan` to read `rejections[checkpoint_name]` from memory and include it as a template binding (e.g. `{rejectionFeedback}`).
2. Update the system prompts in `src/app/modules/ai/executors/prompts/lifecycle/` to include a conditional section: "The operator previously rejected this artefact with the following feedback: {rejectionFeedback}. Address their concerns."
3. When no rejection exists (first attempt), the binding is empty and the prompt section is omitted.

**Files to Modify:**
- `src/app/modules/ai/executors/bootstrap.py` — update context loaders for three nodes
- `src/app/modules/ai/executors/prompts/lifecycle/load_work_item.md` — add rejection section
- `src/app/modules/ai/executors/prompts/lifecycle/generate_tasks.md` — add rejection section
- `src/app/modules/ai/executors/prompts/lifecycle/generate_plan.md` — add rejection section

**Acceptance Criteria:**
- [ ] Rejection feedback appears in the LLM prompt on retry
- [ ] First-attempt prompts (no rejection) are unchanged
- [ ] LLM produces a different/improved artefact when given feedback

---

## Phase 4: Verification

### T-313: End-to-end test for rejection flow

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-310, T-312

**Rationale:**
Verify the full rejection loop: operator rejects → flow loops back → LLM regenerates with feedback → operator approves → flow advances.

**Description:**
Add test cases to `tests/integration/test_lifecycle_v04_manual.py` (or a new sibling file):
1. **Reject-then-approve at confirm_brief**: LLM generates brief → operator rejects with feedback → `load_work_item` re-runs → operator approves → flow advances to `register_work_item`.
2. **Reject-then-approve at confirm_tasks**: similar pattern.
3. **Reject-then-approve at confirm_plan**: similar pattern.
4. **Backward compat**: existing happy-path test (no verdict field) still passes unchanged.

**Files to Modify:**
- `tests/integration/test_lifecycle_v04_manual.py` (or new `test_lifecycle_v04_rejection.py`)

**Acceptance Criteria:**
- [ ] Rejection causes the flow to loop back to the producing node
- [ ] The producing node's LLM call includes rejection feedback
- [ ] Approval after rejection advances the flow normally
- [ ] Existing approve-only tests are unchanged
- [ ] Run completes successfully after reject-then-approve cycle

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| Phase 0 | T-306 | Lock down current behavior with tests |
| Phase 1 | T-307, T-308, T-309 | Predicate + schemas + patch builders |
| Phase 2 | T-310, T-311 | Flow graph edges + rejection budget |
| Phase 3 | T-312 | Feed rejection feedback into LLM prompts |
| Phase 4 | T-313 | End-to-end verification |

**Critical path:** T-306 → T-308 → T-309 → T-310 → T-312 → T-313

**Parallel work:** T-307 (predicate) can be done in parallel with T-306/T-308.

**Risk assessment:** Low. Changes are additive (new fields, new edges, new predicate). Backward compatibility is preserved by defaulting `verdict` to `"approve"`. The v0.3.0 agent is completely unaffected.

**Recommended review points:**
1. After T-309 — verify rejection patches write correctly before wiring the flow graph.
2. After T-310 — verify flow routing before integrating with LLM prompts.

**Rollback strategy:** Remove branch blocks from YAML → reverts to linear (approve-only) transitions. No data migration needed.
