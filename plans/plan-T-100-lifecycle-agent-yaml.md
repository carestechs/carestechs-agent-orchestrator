# Implementation Plan: T-100 — `agents/lifecycle-agent@0.1.0.yaml` + loader round-trip

## Task Reference
- **Task ID:** T-100
- **Type:** Backend
- **Workflow:** investigation-first
- **Complexity:** M
- **Dependencies:** T-087, T-090, T-091, T-092, T-093, T-094, T-095, T-096

## Overview
Author the first concrete lifecycle agent: YAML at `agents/lifecycle-agent@0.1.0.yaml` with all 8 stage nodes, permissive `flow.transitions`, and `policy.system_prompts` mapping four LLM-driven nodes to `.ai-framework/prompts/*.md`. Because this is `investigation-first`, the PR is two-part: investigation plan → YAML + review prompt + loader test.

## Steps

### 0. Investigation deliverable (pre-code)
Write `plans/plan-T-100-lifecycle-agent-yaml-investigation.md` documenting:
- **`flow.transitions` map**: exact per-node edges (see §1 below).
- **`system_prompts` mapping**: which nodes get which `.ai-framework/prompts/` files + the new `lifecycle-review.md`.
- **`max_steps` justification**: 300 — covers ~20 tasks × ~15 steps (intake + task-gen + 3 plan calls + 3 pause/review/correction cycles).
- **Per-node `input_schema` shapes**: match each tool's parameters exactly.
- **Prompt-routing by work-item type**: `task_generation` uses `feature-tasks.md` by default; runtime substitutes `bugfix-tasks.md` / `refactor-tasks.md` based on `memory.work_item.type` before the policy call. This is the *one* place where `system_prompts` isn't 1:1.

Get stakeholder approval on the investigation plan before writing the YAML.

### 1. Create `agents/lifecycle-agent@0.1.0.yaml`
```yaml
ref: lifecycle-agent@0.1.0
version: "0.1.0"
description: >
  Drives the ia-framework's 8-stage lifecycle loop against a single work item.
nodes:
  - name: intake
    description: Parse the work-item brief and populate memory.work_item.
    inputSchema: {type: object, properties: {path: {type: string}}, required: [path]}
  - name: task_generation
    description: Generate the task list for the work item.
    inputSchema: {type: object, properties: {work_item_id: {type: string}, tasks_markdown: {type: string}}, required: [work_item_id, tasks_markdown]}
  - name: task_assignment
    description: Assign an executor to a task.
    inputSchema: {type: object, properties: {task_id: {type: string}}, required: [task_id]}
  - name: plan_creation
    description: Generate an implementation plan for a task.
    inputSchema: {type: object, properties: {task_id: {type: string}, plan_markdown: {type: string}, slug: {type: string}}, required: [task_id, plan_markdown]}
  - name: implementation
    description: Wait for the operator's implementation-complete signal.
    inputSchema: {type: object, properties: {task_id: {type: string}}, required: [task_id]}
  - name: review
    description: Record a pass/fail verdict on the implementation.
    inputSchema: {type: object, properties: {task_id: {type: string}, verdict: {type: string, enum: [pass, fail]}, feedback: {type: string}}, required: [task_id, verdict, feedback]}
  - name: corrections
    description: Route back to implementation after a failed review.
    inputSchema: {type: object, properties: {task_id: {type: string}}, required: [task_id]}
  - name: closure
    description: Flip the work item's Status to Completed.
    inputSchema: {type: object, properties: {work_item_id: {type: string}}, required: [work_item_id]}
flow:
  entryNode: intake
  transitions:
    intake: [task_generation]
    task_generation: [task_assignment]
    task_assignment: [plan_creation]
    plan_creation: [plan_creation, implementation]
    implementation: [review]
    review: [corrections, closure]
    corrections: [implementation]
    closure: []
intakeSchema:
  type: object
  properties:
    workItemPath: {type: string}
  required: [workItemPath]
terminalNodes: [closure]
defaultBudget:
  maxSteps: 300
policy:
  systemPrompts:
    task_generation: .ai-framework/prompts/feature-tasks.md
    plan_creation: .ai-framework/prompts/plan-generation.md
    review: .ai-framework/prompts/lifecycle-review.md
```

### 2. Create `.ai-framework/prompts/lifecycle-review.md`
Short focused prompt (≤ 40 lines) instructing the model, given a task spec + a git diff, to call `review_implementation` with a structured verdict + feedback. Mirror the shape of existing `.ai-framework/prompts/*.md` (headings, examples).

### 3. Create `tests/modules/ai/test_agents_lifecycle.py`
Three cases:
- `test_lifecycle_agent_yaml_loads` — `load_agent("lifecycle-agent@0.1.0", agents_dir=Path("agents"))` returns a valid `AgentDefinition`; asserts 8 nodes, entry=`intake`, terminal=`[closure]`, 3 prompt paths exist and resolve.
- `test_lifecycle_agent_hash_deterministic` — load twice, assert `agent_definition_hash` matches; load a second copy with reordered top-level keys, assert hash still matches (canonical JSON serialization handles this).
- `test_orchestrator_agents_show_renders` — use `CliRunner` against `orchestrator agents show lifecycle-agent@0.1.0`; assert output contains all 8 node names + 3 prompt paths.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `plans/plan-T-100-lifecycle-agent-yaml-investigation.md` | Create | Investigation deliverable. |
| `agents/lifecycle-agent@0.1.0.yaml` | Create | The agent definition. |
| `.ai-framework/prompts/lifecycle-review.md` | Create | New review-stage prompt. |
| `tests/modules/ai/test_agents_lifecycle.py` | Create | 3 loader + CLI tests. |

## Edge Cases & Risks
- **Prompt-routing by type**: the YAML declares `task_generation → feature-tasks.md` as *default*. Runtime substitution happens in T-101's integration test's prompt-assembly layer. Without that substitution, BUG and IMP work items use the FEAT prompt — acceptable for v1 (the prompts are substantially similar; AC-3 of the feature brief tolerates "coherent" output from a real run).
- **`agent_definition_hash` across OSes**: canonical JSON with sorted keys + UTF-8 should be byte-identical. Verify on Linux/macOS — if Windows is ever a target, normalize path separators in the hash input.
- **Terminal `closure` node has empty transitions**: `closure: []` signals a dead-end; the runtime's done-node check fires when the `closure` tool completes.
- **Max-steps 300 ceiling**: generous for realistic runs. Tighten later if real-world data shows lifecycle runs finishing in <100 steps.
- **Review prompt quality**: draft short, iterate with live testing (T-104). Don't over-engineer before real feedback.

## Acceptance Verification
- [ ] `plans/plan-T-100-lifecycle-agent-yaml-investigation.md` exists and approved.
- [ ] `agents/lifecycle-agent@0.1.0.yaml` loads via `load_agent`.
- [ ] All 8 nodes, 3 prompt paths, `terminalNodes=[closure]`, `entryNode=intake`.
- [ ] `.ai-framework/prompts/lifecycle-review.md` exists and is non-empty.
- [ ] `orchestrator agents show lifecycle-agent@0.1.0` renders the full definition.
- [ ] Hash determinism across invocations.
- [ ] 3 tests pass; `uv run pyright` clean.
