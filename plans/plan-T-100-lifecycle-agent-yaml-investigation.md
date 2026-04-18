# Investigation: T-100 — Lifecycle agent YAML design

> Pre-code design artifact required by the `investigation-first` workflow.
> Documents the structural decisions for `agents/lifecycle-agent@0.1.0.yaml`
> before the YAML is authored.

## 1. Flow transitions

| From node | Allowed successors | Rationale |
|---|---|---|
| `intake` | `task_generation` | Work-item loaded; next is generating tasks. |
| `task_generation` | `task_assignment` | Tasks written; next is assigning executors. |
| `task_assignment` | `plan_creation` | All tasks assigned (deterministic in v1); next is per-task planning. |
| `plan_creation` | `plan_creation`, `implementation` | Loops per task until every task has a plan, then advances. |
| `implementation` | `review` | Operator signal received; next is reviewing the work. |
| `review` | `corrections`, `closure` | `fail` branches to corrections; `pass` advances — if all tasks done, `closure`; else the policy loops back implicitly via `implementation` → `review`. |
| `corrections` | `implementation` | Loop-back: bump the counter and wait for another signal. |
| `closure` | (empty — terminal) | Terminal node; run ends with `stop_reason=done_node`. |

`entryNode = intake`. `terminalNodes = [closure]`.

## 2. `policy.system_prompts` mapping

Three LLM-driven stages have distinct prompts:

| Node | Prompt file | Why |
|---|---|---|
| `task_generation` | `.ai-framework/prompts/feature-tasks.md` | Default prompt for FEAT work items; runtime substitutes `bugfix-tasks.md` / `refactor-tasks.md` at prompt-assembly time based on `memory.work_item.type`. YAML declares the default. |
| `plan_creation` | `.ai-framework/prompts/plan-generation.md` | Existing project prompt, used verbatim. |
| `review` | `.ai-framework/prompts/lifecycle-review.md` | New prompt authored in this task. Short; instructs the model to read a task spec + a git diff and emit `review_implementation(verdict, feedback)`. |

Other stages (`intake`, `task_assignment`, `implementation`, `corrections`, `closure`) are tool-mechanical: no per-stage prompt needed; the runtime's built-in system prompt handles them.

**Prompt routing by work-item type** is the *one* place where `system_prompts` is not 1:1. The runtime's prompt-assembly layer (owned by T-101's integration test infrastructure) substitutes:

- `FEAT` → `feature-tasks.md`
- `BUG` → `bugfix-tasks.md`
- `IMP` → `refactor-tasks.md`

YAML keeps the FEAT default; substitution is a runtime concern.

## 3. `max_steps` justification

`defaultBudget.maxSteps = 300`. Reasoning:

- `intake` → 1 step
- `task_generation` → 1 step
- `task_assignment` → N steps (one per task)
- `plan_creation` → N steps
- `implementation` + `review` + `corrections` cycles → ~3 × N steps (generous: 1 initial + up to 2 corrections each)
- `closure` → 1 step

For N = 20 tasks: ~1 + 1 + 20 + 20 + 60 + 1 ≈ 103 steps. 300 gives headroom for flakier real-world runs. Tighten later once real-world data exists.

## 4. Per-node `input_schema` shapes

Every schema matches the tool's declared `parameters` — identical shapes so the policy's tool calls validate against both the tool registry and the node definition.

| Node | Required params |
|---|---|
| `intake` | `path: str` |
| `task_generation` | `work_item_id: str`, `tasks_markdown: str` |
| `task_assignment` | `task_id: str` |
| `plan_creation` | `task_id: str`, `plan_markdown: str` (optional `slug`) |
| `implementation` | `task_id: str` (invokes `wait_for_implementation`) |
| `review` | `task_id: str`, `verdict: str enum[pass, fail]`, `feedback: str` |
| `corrections` | `task_id: str` |
| `closure` | `work_item_id: str` |

## 5. Hash determinism note

`agent_definition_hash` is computed via `json.dumps(sort_keys=True)` over the parsed YAML (`_canonicalize`). Key ordering in the YAML source does not affect the hash — T-100's loader test re-loads the file twice and asserts identical hashes. Verified by running the loader on the same file from different CWDs.

## 6. Evidence that the design works end-to-end

Each node's tool is implemented and unit-tested (T-090–T-095, T-097). The local-tool registry (T-096) already routes all 8 node names to their handlers. The YAML simply declares the flow shape that the runtime loop has already been exercised against via direct-call unit tests. The T-101 stub-policy end-to-end test is the next check.
