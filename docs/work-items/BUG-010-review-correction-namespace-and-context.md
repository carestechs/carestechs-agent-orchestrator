# Bug Report: BUG-010 — Review/correction memory writes bypass `lifecycle.v1`; reviewer LLM gets no context

> **Purpose**: Three coupled bugs surfaced on the live `lifecycle-agent@0.3.0` run on 2026-05-02 after BUG-009 unblocked operator signal delivery. The first two are namespace-mismatch writes that mirror BUG-009 on the *write* side; the third is a missing prompt loader that left the reviewer LLM with nothing to judge against.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-010 |
| **Summary** | Two writers (`_patch_review`, `correct_implementation` handler) wrote to top-level `RunMemory.data` slots that no canonical reader sees, and the reviewer LLM's user prompt carried only `{taskId}` — no plan, no task body, no implementation evidence. Net effect on the live run: every review fail-looped because (a) `correction_attempts` never landed where the predicate read from → budget never tripped → infinite reject/resubmit, (b) the reviewer rationally returned `verdict=fail` because it had no input. |
| **Severity** | High (both bugs together create an unbounded loop in any review-rejection scenario; the reviewer-context bug alone makes review-pass effectively impossible) |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run on 2026-05-02 (operator diagnosis: step 10 review wrote `__memory_patch.review_history` snake-case at top level; step 11 correction wrote `__memory_patch.correction_attempts` at top level; review user-prompt `"Review the implementation for task: {taskId}"` produced LLM verdict `fail` with feedback "task spec, implementation plan, and git diff for T-001 are all missing from this request"). |
| **Date Reported** | 2026-05-02 |
| **Related** | BUG-009 (sister bug on the *read* side; this PR is the corresponding write-side migration), FEAT-011/T-255 (introduced `lifecycle.v1` namespace), IMP-002 (human-pause activation that exposed this) |

---

## 2. Steps to Reproduce

**Bug 1 + 2 (review/correction namespace):**

1. Run `lifecycle-agent@0.3.0` end-to-end. Send `implementation-complete` signal at `request_implementation`.
2. `review_implementation` runs, returns `verdict=fail` (almost always — see Bug 3).
3. `correct_implementation` runs, attempts to bump `correction_attempts[T-001]`.
4. **Observe:** `RunMemory.data` ends up with two top-level keys nothing reads — `review_history: [...]` and `correction_attempts: {T-001: 1}`. The canonical `lifecycle.v1.reviewHistory` and `lifecycle.v1.correctionAttempts` stay empty.
5. Resolver evaluates `correction_attempts_under_bound` predicate → reads `memory["correction_attempts"]` (top level), finds 1, but the predicate already had a sister bug pointing at the wrong key (BUG-009 fixed read side for `unplanned_tasks_remaining`, this PR fixes it for `correction_attempts_under_bound`). Either way the predicate returns True → loop back to `request_implementation`. Forever.

**Bug 3 (reviewer context):**

1. Same setup. `review_implementation` runs.
2. **Observe:** Anthropic Messages API receives a user prompt that's literally `"Review the implementation for task: T-001"`. The model has no plan, no task body, no diff, no commit info. Returns `verdict=fail` with `feedback="The task spec, implementation plan, and git diff for T-001 are all missing from this request."`

---

## 3. Root Cause

### Bug 1 — `_patch_review` writes to top-level `review_history` (snake_case)

```python
def _patch_review(result):
    return {
        "review_history": [
            {"task_id": ..., "verdict": ..., "feedback": ...}
        ]
    }
```

The runtime's `__memory_patch` applier merges shallowly: `data["review_history"] = patch["review_history"]`. So `data["review_history"]` exists but no reader looks there. The canonical `LifecycleMemory.review_history` lives at `data["lifecycle.v1"]["reviewHistory"]` (camelCase via Pydantic alias_generator) and stays empty.

### Bug 2 — `correct_implementation` handler reads/writes top-level `correction_attempts`

```python
existing.get("correction_attempts") or {}        # read top-level
"__memory_patch": {"correction_attempts": ...}   # write top-level
```

Same shape bug. Plus the `correction_attempts_under_bound` predicate read top-level too (it pre-dated FEAT-011 just like the `unplanned_tasks_remaining` predicate did before BUG-009 fixed it). All three sites pointed at the wrong key.

### Bug 3 — Reviewer prompt has no context loader

The `review_implementation` LLMContentExecutor was instantiated without a `prompt_context_loader`, and its `user_prompt_template` was a one-liner `"Review the implementation for task: {taskId}"`. The model had nothing to compare against.

### Why the namespace fix needs `LLMContentExecutor` plumbing

Reviewing requires *appending* the new review entry to the existing `reviewHistory` list. The runtime's `__memory_patch` applier replaces top-level keys verbatim — a partial write under `lifecycle.v1` would stomp `tasks`, `workItem`, etc. So the patch builder must read existing memory, append, and return the full merged `lifecycle.v1` blob. That requires giving the builder access to current memory, which the existing `MemoryPatchBuilder = Callable[[result], dict]` signature didn't allow.

---

## 4. Fix

Five coordinated changes:

1. **`MemoryPatchBuilder` signature** — extended in both `executors/llm_content.py` and `executors/composite.py` from `(result) -> patch` to `(result, current_memory) -> patch`. Symmetric across both LLM-content executors.

2. **`LLMContentExecutor` reads memory** — new optional `session_factory` constructor arg + `_read_memory` helper. When the builder is set, the executor reads `RunMemory.data` before invoking it. `register_lifecycle_v03` wires `session_factory` into all four LLMContentExecutor instantiations.

3. **`CompositeLLMEngineExecutor` reads memory** — uses its existing `session_factory` to read `RunMemory.data` before invoking its builder.

4. **Writers migrated to canonical namespace:**
   - `_patch_review` — reads existing `lifecycle.v1.reviewHistory` via `read_lifecycle_memory`, appends the new review entry (computing `attempt` from prior history), returns `{LIFECYCLE_MEMORY_NS: to_run_memory(memory_model)}`.
   - `correct_implementation` handler — reads `lifecycle.v1.correctionAttempts`, bumps for the current task, writes the merged namespace.

5. **`correction_attempts_under_bound` predicate** — reads via `read_lifecycle_memory(memory).correction_attempts` (matches BUG-009's fix to `unplanned_tasks_remaining`).

6. **Reviewer prompt context** — new `_load_review_context` `PromptContextLoader` that reads:
   - Task body from `lifecycle.v1.tasks[current_task_id]` (title, description, acceptance criteria, complexity).
   - Plan markdown from top-level `plans[task.id].plan_markdown` (the canonical write site of `_patch_generate_plan`).
   - Implementation evidence from `__feat009.last_dispatch_result.payload` — the operator's signal payload (whatever they put in `POST /signals` body).

   The user prompt template now includes the plan, the task body, and an `IMPLEMENTATION EVIDENCE` block. Diff is operator-supplied via the signal payload — the orchestrator does not read git directly in v1.

---

## 5. Verification

- `tests/modules/ai/executors/test_composite_wake_race.py` — patch builder updated to the 2-arg signature.
- `tests/modules/ai/test_lifecycle_v03_branch_walk.py` — `correct_implementation` scenarios use new `_lifecycle_memory_with_corrections` helper that seeds the canonical namespace shape.
- Full suite: 1163 passed, 12 skipped.
- Live re-run will confirm: reviewer receives plan + task body + signal-payload evidence; correction_attempts lands under `lifecycle.v1.correctionAttempts`; predicate trips after 2 attempts and the run terminates with `correction_budget_exceeded` instead of looping forever.

---

## 6. Out of Scope

- **`real-run.sh` snake_case `task_id`** in signal payload + empty task_id extractor — operator-side script, fixed separately.
- **Diff/git-blob fetch by the orchestrator.** v1 stays out of git; what the operator puts in the signal payload is what the reviewer sees. Future FEAT could add a GitHub-PR-fetch effector that injects a diff into memory before the reviewer runs.
- **Mode-aware dispatch timeout** (still pending from IMP-002 follow-on).
- **Migration of existing `RunMemory.data` rows.** The wrong-key writes were silent; rows that have stale top-level `review_history` / `correction_attempts` blobs are harmless (no canonical reader sees them). They will be naturally orphaned as runs terminate. No backfill needed.

---

## Changelog

- 2026-05-02 — Filed and resolved in the same PR. `MemoryPatchBuilder` signature extended to pass current memory; two writers + one predicate migrated to `lifecycle.v1`; reviewer prompt loader added with plan + task body + signal-payload evidence.
