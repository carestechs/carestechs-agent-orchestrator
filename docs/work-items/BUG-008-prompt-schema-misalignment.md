# Bug Report: BUG-008 — system prompts contradict tool schemas

> **Purpose**: Capture the prompt/schema mismatch surfaced by the live `lifecycle-agent@0.3.0` run after BUG-007 fixed the row-ordering race. Filed and resolved in the same PR (operator diagnosis).

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-008 |
| **Summary** | The system prompts at `src/app/modules/ai/executors/prompts/lifecycle/generate_tasks.md` and `generate_plan.md` are the full ai-framework authoring templates (200–300 line documents asking for Markdown task definitions with Type/Workflow/Complexity/Acceptance Criteria sections). The `LLMContentExecutor` wraps those prompts around an Anthropic tool call expecting a structured JSON payload (`{tasks: [{id, title, executor}]}` or `{task_id, plan_markdown}`). The model sees two contradictory instructions and emits an empty-but-valid `{tasks: []}` (or hallucinates field names like `parameter`, `query` that fit the framework's shape), failing the schema or producing zero tasks. |
| **Severity** | High (blocks v0.3.0 end-to-end; bug ships a structurally-empty task list, breaking the rest of the lifecycle) |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run (operator diagnosis) |
| **Date Reported** | 2026-05-01 |
| **Date First Observed** | 2026-05-01 (after BUG-005/006/007 unblocked connectivity, signature verification, and row-ordering) |
| **Related** | FEAT-011 PR-3 simplifications: "Prompt drift surface" (the original migration doc flagged the copy-from-`.ai-framework/prompts/` pattern as a divergence risk; this is the divergence biting). |

---

## 2. Steps to Reproduce

1. Start `lifecycle-agent@0.3.0`.
2. Run reaches `generate_tasks` — the executor renders the system prompt from `executors/prompts/lifecycle/generate_tasks.md`.
3. **Observe**: the LLM returns `{"tasks": []}` (or hallucinates a field name like `{"parameter": "read_files"}` that doesn't match the schema).
4. `propose_tasks` then fails with `LifecycleMemory.tasks is empty — generate_tasks must populate it`, OR the schema validation in `LLMContentExecutor` retries and gives up.

**Reproducibility:** Always under live LLM. Does not reproduce under the scripted-stub e2e tests because those mocks return canned valid payloads regardless of prompt content.

---

## 3. Root Cause

`load_work_item.md` (27 lines) is short and schema-aligned — explicitly enumerates `work_item_id`, `title`, `summary`, says "Return one tool call with the following fields". It works.

`generate_tasks.md` (311 lines) and `generate_plan.md` (270 lines) are the unmodified authoring templates from `.ai-framework/prompts/`. They:
- Open with `> **Purpose**: Generate implementation tasks for a new feature…`.
- Spend ~200 lines on XML examples + a worked pizza-feature walkthrough.
- Specify a Markdown output format with `### T-001:`, `**Type:**`, `**Workflow:**`, `**Complexity:**`, etc.
- Never mention the actual JSON schema the executor is wrapping the call around.

The Anthropic provider gets:
- System prompt: "produce a Markdown document with T-XXX entries, Type, Workflow, Complexity, etc."
- Tool schema (auto-derived from the Pydantic `result_schema` by FEAT-011's `_tool_from_result_schema`): "emit a JSON object matching `GenerateTasksResult` / `GeneratePlanResult`".

The contradiction is irreconcilable. The model picks the tool schema (it must call a tool to terminate), then emits the closest-to-empty payload that satisfies the schema, or hallucinates field names that fit the framework's shape.

`review_implementation.md` (55 lines) was hand-written for this executor — explicitly references `review_implementation` as the tool, names the schema fields. It works.

---

## 4. Fix

Rewrite `generate_tasks.md` and `generate_plan.md` to match `load_work_item.md`'s shape: short, focused, executor-facing prompts that name the tool, enumerate the schema fields, give one concrete example, and forbid the framework-shaped Markdown output.

### Constraints preserved

- **H1 line shape.** The e2e test's `_ScriptedProvider` maps system prompts back to node names by inspecting the H1 (`tests/integration/test_lifecycle_v03_end_to_end.py:127`). New H1s preserve the trigger words: `# Feature Task Generation` (matches `feature` + `task`), `# Task Implementation Plan` (matches `plan`).
- **Schema fields named verbatim.** New prompts list `tasks[i].id|title|executor` and `task_id|plan_markdown` exactly so the model emits the right keys.
- **No external dependencies.** The original templates referenced ia-framework concepts (Workflow, Complexity, Acceptance Criteria) the deterministic agent doesn't carry; those references are dropped.

### Files

- `src/app/modules/ai/executors/prompts/lifecycle/generate_tasks.md` — 56 lines, was 311.
- `src/app/modules/ai/executors/prompts/lifecycle/generate_plan.md` — 51 lines, was 270.
- `load_work_item.md` and `review_implementation.md` left untouched.

---

## 5. Verification

- Existing v0.3.0 e2e + rejection tests still pass — the scripted stub provider returns valid JSON regardless of prompt content, so the previous mismatch never bit at unit-test scope. The new prompts also pass because their H1s still match the headline mapper.
- Live re-run of `lifecycle-agent@0.3.0` against Anthropic should now produce a non-empty `tasks` array with `id="T-NNN"`, `title=<imperative phrase>`, `executor="claude-code"` — and the propose_tasks step should advance.

---

## 6. Out of Scope

- The `.ai-framework/prompts/feature-tasks.md` and `plan-generation.md` files (the original authoring templates) are unchanged — they remain the canonical reference for the human-driven task-generation workflow. The FEAT-011 design called these "executor copies"; the divergence is intentional now.
- A linter that checks every executor prompt against its `result_schema`'s field names would prevent recurrence — possible follow-on. Out of scope for this PR.

---

## Changelog

- 2026-05-01 — Filed and resolved in the same PR; operator diagnosis.
