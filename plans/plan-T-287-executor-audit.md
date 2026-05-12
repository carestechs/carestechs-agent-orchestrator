# Implementation Plan: T-287 — Audit downstream executors for brief disk reads

## Task Reference
- **Task ID:** T-287
- **Type:** Backend
- **Workflow:** investigation-first
- **Complexity:** M
- **Rationale:** AC-5 is end-to-end — the registration path is moot if a later executor still opens the file. This task closes the gap.

## Overview
Two phases: (1) **investigate** — grep every reference to `workItemPath` / `Path(...read_text` in executor code and document findings; (2) **remediate** — swap each remaining disk read for a `WorkItem.body_md` lookup via the memory patch from T-286, adjusting prompt templates as needed.

## Investigation Phase

### Step I-1: Grep for live disk reads
**Action:** Read-only audit
Run:
```
grep -rn "workItemPath\|workItemEngineId" src/app/modules/ai/executors/ src/app/modules/ai/lifecycle/
grep -rn "\.read_text\|\.read_bytes\|open(" src/app/modules/ai/executors/
grep -rn "Path(" src/app/modules/ai/executors/
```
Confirmed candidate sites (from FEAT-014 brief drafting):
- `bootstrap.py` line ~408 — `raw_path = ctx.intake.get("workItemPath")` inside `generate_tasks` adapter.
- `bootstrap.py` line ~437 — prompt template includes `{workItemPath}` literally.
- `bootstrap.py` line ~503 — `generate_tasks` prompt: `Generate the task breakdown for work item id: {workItemId}`.
- `propose_tasks.py` — check whether it consumes the body or only the id.

Document every site found in this plan's "Investigation Findings" section below.

### Step I-2: Categorize each site
For each found site, classify as:
- **(A) Reads body content** → swap to `memory["work_item_body"]` (or a fresh `_load_work_item_body` call).
- **(B) Reads path string for display/prompt only** → swap to `workItemId` or remove.
- **(C) Already DB-backed** → no change.

### Step I-3: Write findings into the plan file before remediation
**File:** `plans/plan-T-287-executor-audit.md` (this file)
**Action:** Modify
Fill in the "Investigation Findings" section below with grep output and per-site classification. **This step must complete before remediation begins** (per the `investigation-first` workflow).

## Remediation Phase

### Step R-1: `bootstrap.py` `generate_tasks` adapter
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify
Replace `raw_path = ctx.intake.get("workItemPath")` and the subsequent file read with:
```python
body_md = ctx.memory.get("work_item_body")
external_ref = ctx.memory.get("work_item_id") or ctx.intake.get("workItem", {}).get("id")
if not body_md:
    # Last-resort fetch (defensive — `load_work_item` should have populated it)
    body_md, _ = await _load_work_item_body(ctx.session_factory, external_ref=external_ref)
```
The prompt template at line ~437 changes `{workItemPath}` → `{workItemId}` plus an inline `Body:\n\n{workItemBody}` block.

### Step R-2: `propose_tasks.py`
**File:** `src/app/modules/ai/executors/propose_tasks.py`
**Action:** Modify (conditional on investigation finding)
If propose_tasks reads body content from disk, replace the read with a `ctx.memory["work_item_body"]` access. If it only reads ids, no change. Document the outcome in the investigation section.

### Step R-3: Prompt files under `executors/prompts/lifecycle/`
**File:** `src/app/modules/ai/executors/prompts/lifecycle/*.md`
**Action:** Modify (conditional)
Any prompt that references `{workItemPath}` as a template variable changes to `{workItemId}` (and adds `{workItemBody}` if the body wasn't already piped in via another variable). Walk each file in the directory.

### Step R-4: Drop unused `workItemPath` reads
**File:** Various
**Action:** Modify
Anywhere `workItemPath` was used purely as a "tell the LLM the path" string, drop the read. Keep `workItemId` for traceability.

### Step R-5: Confirm structural cleanliness
Final grep:
```
grep -rn "workItemPath" src/app/modules/ai/executors/
```
Acceptable matches after this task:
- T-288's deprecation log emission point.
- Anything explicitly marked `# DEPRECATED FEAT-014`.

Nothing else.

## Investigation Findings

<!--
Fill in during Step I-3. Format:

### Site 1: `bootstrap.py:408` — `generate_tasks` adapter
- Category: (A) reads body content
- Action: swap to ctx.memory["work_item_body"]; update prompt template
- Verified by: existing test `test_generate_tasks_*.py` passes without modification

### Site 2: `bootstrap.py:437` — prompt template variable
- Category: (B) reads path string for prompt only
- Action: change `{workItemPath}` → `{workItemId}` + add `{workItemBody}` block

(etc.)
-->

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/bootstrap.py` | Modify | `generate_tasks` adapter swap; prompt template variable |
| `src/app/modules/ai/executors/propose_tasks.py` | Modify (cond.) | If body read on disk, swap to memory |
| `src/app/modules/ai/executors/prompts/lifecycle/*.md` | Modify (cond.) | `{workItemPath}` → `{workItemId}` + `{workItemBody}` |

## Edge Cases & Risks
- **Prompt-template behavior drift.** Replacing `{workItemPath}` with content changes the prompt seen by the LLM. The existing FEAT-011 reviewer/proposer prompts were tuned against the path string. If a prompt drifts so much that review quality regresses, the right fix is to mention the id rather than inlining the body twice — the body is already passed in via the existing brief variable.
- **Memory-patch ordering.** `_handle_request_work_item_load` runs before `generate_tasks` in the lifecycle agent flow. Confirm via `agents/lifecycle-agent@0.3.0.yaml` that the node order is preserved; the new code defends with a defensive `_load_work_item_body` fallback.
- **Tests for executors that consume body content** — they probably already stub the memory dict. Verify they still pass after the key changes.

## Acceptance Verification
- [ ] Investigation Findings section is filled in *before* the remediation phase begins.
- [ ] `grep -rn "workItemPath" src/app/` returns only the T-288 deprecation site and the FEAT-006 / lifecycle/service.py legacy LLM-policy path (which is excluded from FEAT-014 by design).
- [ ] All existing executor tests pass without modification (or their changes are scoped to the prompt-variable rename).
- [ ] No `Path(...).read_text()` on a `docs/work-items/...` path in any executor handler.
