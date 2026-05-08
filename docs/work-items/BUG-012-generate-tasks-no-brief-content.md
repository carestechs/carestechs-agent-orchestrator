# Bug Report: BUG-012 — generate_tasks user prompt carries only the work item id, never the brief

> **Purpose**: Same shape as BUG-005(b) but at the next node down. The `generate_tasks` LLM-content binding's user prompt template is `"Generate the task breakdown for work item id: {workItemId}"` — the model only sees the external ref, not the brief. `load_work_item` was fixed in BUG-005; `generate_tasks` was not. Filed and resolved in the same PR.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-012 |
| **Summary** | `register_lifecycle_v03` registers `generate_tasks` with `user_prompt_template="Generate the task breakdown for work item id: {workItemId}"`. The LLM has no access to the work-item file or its compressed summary in this call, so the task list is generated from the external ref alone. Acceptance criteria, scope, and constraints written into the original brief do not survive into the generated tasks — and from there into `generate_plan` and `review_implementation`, which both consume `task.acceptance_criteria` as ground truth. |
| **Severity** | Medium-High. Doesn't crash anything (the run completes), but every downstream LLM step is anchored on tasks invented from a 1-3 sentence summary plus an id. The reviewer ultimately judges implementation against acceptance criteria the agent fabricated, three layers removed from the operator's intent. |
| **Status** | Resolved |
| **Reported By** | Operator review of the lifecycle pipeline |
| **Date Reported** | 2026-05-08 |
| **Date First Observed** | 2026-05-08 |
| **Related** | BUG-005 (same shape at `load_work_item`), FEAT-011 (lifecycle v0.3.0 deterministic port) |

---

## 2. Root Cause

`load_work_item` was fixed in BUG-005 by attaching a `prompt_context_loader` (`_load_work_item_brief_file`) that reads the file at `intake.workItemPath` and exposes its contents as `{workItemBrief}` for the user prompt template. `generate_tasks` was registered without that loader. Its user prompt is one line and carries only `{workItemId}` — populated from `Run.intake` after `register_work_item` writes the external ref back. The file body is two lines of code away (the loader already exists in scope at the registration site) but never wired in.

The `generate_tasks` *system* prompt was already written assuming the brief would be provided ("You will be given a work-item brief…"), so the model is told it has context it never receives.

The compounding effect: `generate_tasks` writes `tasks[].acceptance_criteria` into `LifecycleMemory.tasks`. `generate_plan` reads from memory. `review_implementation` reads `task.acceptance_criteria` from memory and judges the operator-submitted implementation against it. Three LLM steps consume invented criteria as if they were the operator's contract.

---

## 3. Fix

Add `prompt_context_loader=_load_work_item_brief_file` to the `generate_tasks` `LLMContentExecutor` binding in `register_lifecycle_v03` (`src/app/modules/ai/executors/bootstrap.py`). Update the user-prompt template to embed `{workItemBrief}` between the same `BEGIN/END WORK ITEM BRIEF` markers `load_work_item` already uses. Update the system prompt at `src/app/modules/ai/executors/prompts/lifecycle/generate_tasks.md` to be explicit that the brief is delivered in the user message between markers (replacing the pre-fix "optional memory snapshot" wording).

The loader is the same closure already defined for `load_work_item` and is in scope at the registration site — no new module, no new helper, no new config. File-not-found and OSError degrade the same way they degrade for `load_work_item`.

---

## 4. Verification

- `uv run pytest tests/modules/ai/executors/ tests/integration/test_lifecycle_*` — full lifecycle executor + integration suite green (96 tests).
- `uv run ruff check src/app/modules/ai/executors/bootstrap.py` clean.
- Pyright clean for the diff (one pre-existing partially-unknown-type warning at line 759, unrelated to this change).
- Manual: a real run with `LLM_PROVIDER=anthropic` against a non-trivial `FEAT-*.md` produces tasks whose `acceptance_criteria` quote / paraphrase the brief's own acceptance criteria, instead of generic phrasing derived from the title.

---

## 5. Out of Scope

- The same gap may exist at `generate_plan` and `review_implementation` (their `prompt_context_loader` reads the *current task* body but does not re-read the original brief). Whether that's actually a problem depends on whether `task.description` + `task.acceptance_criteria` carry enough context — which itself is downstream of this fix. Re-evaluate after this lands.
- Persisting the full brief into `LifecycleMemory.work_item` as a `brief_markdown` field would be a more general fix (every node sees it without re-reading the file). Not done here; the per-binding `prompt_context_loader` keeps the change minimal and matches the BUG-005 precedent.
