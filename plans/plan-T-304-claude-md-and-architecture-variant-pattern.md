# Implementation Plan: T-304 — `CLAUDE.md` + `docs/ARCHITECTURE.md` — variant pattern + Quick Reference

## Task Reference
- **Task ID:** T-304
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Future contributors (and the AI in future sessions) need a one-stop signpost that lifecycle agent variants are a thing and the bootstrap-function pattern is consistent. Skipping this leaves the next variant author re-discovering the design from grep.

## Overview
Two small documentation edits:
1. **`CLAUDE.md` Quick Reference** — add a `orchestrator run lifecycle-agent@0.4.0-manual` example command alongside the existing v0.3.0 example. Add a one-line Patterns entry describing the variant-naming + bootstrap-function convention.
2. **`docs/ARCHITECTURE.md`** — short subsection (1-2 paragraphs) under the lifecycle-agent component documenting the `register_lifecycle_v0X_*` pattern, listing the currently-shipping variants (v0.3.0 LLM, v0.4.0-manual, future v0.4.0-auto). Add a changelog entry.

## Implementation Steps

### Step 1: Update `CLAUDE.md` Quick Reference command list
**File:** `CLAUDE.md`
**Action:** Modify

Locate the `## Quick Reference` → `### Common Commands` block (near the top of the file). Find the existing `uv run orchestrator run lifecycle-agent@0.3.0 ...` example. Below it, add:

```bash
# Manual variant — pauses for operator approval at every LLM→engine seam
uv run orchestrator run lifecycle-agent@0.4.0-manual --work-item docs/work-items/FEAT-042.md --follow
```

Leave the existing v0.3.0 example untouched as the autonomous default.

### Step 2: Update `CLAUDE.md` Patterns to Follow
**File:** `CLAUDE.md`
**Action:** Modify

Locate the `### Patterns to Follow` section. Add a single-line pattern entry (alphabetic or thematic placement — near the "Two entry points, one core" pattern, which is also about single-source-of-truth orchestration):

```markdown
- **Lifecycle agent variants are peers registered under distinct `agent_ref`s.** Each variant ships its own `agents/lifecycle-agent@X.Y.Z-<variant>.yaml` plus a `register_lifecycle_v0X_<variant>(...)` bootstrap function. The variant is selected at run start via the existing `agent_ref` parameter — no runtime branching. Shared bindings are factored through the lowest-version helper (today: `register_lifecycle_v03(agent_ref=..., skip_review_implementation=...)`), and variant-specific bindings (e.g., human checkpoints) are added on top. Current variants: `@0.3.0` (LLM-driven autonomous), `@0.4.0-manual` (operator-driven, FEAT-015). The pattern scales to any future variant — e.g., `@0.4.0-auto` (FEAT-016, planned).
```

### Step 3: Update `docs/ARCHITECTURE.md`
**File:** `docs/ARCHITECTURE.md`
**Action:** Modify

Locate the section describing the lifecycle agent (search for `lifecycle-agent` or "FEAT-005" / "FEAT-011"). Add a new subsection — placement depends on the existing structure but should sit alongside the lifecycle agent's component description:

```markdown
### Lifecycle agent variants

The lifecycle agent is shipped as a family of variants — distinct
agent definitions that share the same `lifecycle.v1` memory shape, the
same engine workflows (`work_item_workflow`, `task_workflow`), and the
same data-model contract, but differ in their **flow graph** and
**executor bindings**. Each variant is a YAML in `agents/` plus a
`register_lifecycle_v0X_<variant>(...)` bootstrap function in
`src/app/modules/ai/executors/bootstrap.py`. Lifespan invokes one
bootstrap per variant; the executor registry resolves bindings via the
`(agent_ref, node_name)` key, so per-variant routing is implicit in
the run-start request's `agentRef` parameter — no runtime branching.

Current variants:

| Agent ref | Bootstrap | Source feature | Use case |
|-----------|-----------|----------------|----------|
| `lifecycle-agent@0.3.0` | `register_lifecycle_v03` | FEAT-011 | Autonomous, LLM-driven brief → tasks → plan → review → close. |
| `lifecycle-agent@0.4.0-manual` | `register_lifecycle_v04_manual` | FEAT-015 | Operator-driven; four human checkpoints + human reviewer. Reuses v0.3.0's executor set via `register_lifecycle_v03(agent_ref=..., skip_review_implementation=True)`. |

The shared-helper-plus-overrides pattern (a sibling-variant calls the
prior-version helper under its own `agent_ref` and then registers its
variant-specific bindings on top) keeps drift to a minimum: every
shared node is rooted in one place. New variants follow the same
shape.
```

### Step 4: Add a changelog entry to `docs/ARCHITECTURE.md`
**File:** `docs/ARCHITECTURE.md`
**Action:** Modify

Find the changelog section at the bottom of the file. Add:

```markdown
- **YYYY-MM-DD (FEAT-015):** Documented the lifecycle agent variants pattern — sibling agent refs share a single bootstrap helper rooted at the lowest version (currently `register_lifecycle_v03`). Added `lifecycle-agent@0.4.0-manual` as the first non-LLM-driven variant.
```

Replace `YYYY-MM-DD` with the current date.

### Step 5: Verify
**File:** N/A
**Action:** Verify

- Confirm `CLAUDE.md` renders cleanly on GitHub.
- Confirm the new ARCHITECTURE.md table aligns with the rest of the file's tables.
- Run `grep -c "lifecycle-agent@0.4.0-manual" CLAUDE.md docs/ARCHITECTURE.md` — expect at least 1 hit per file.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `CLAUDE.md` | Modify | One new command example in Quick Reference; one new Patterns entry. |
| `docs/ARCHITECTURE.md` | Modify | New "Lifecycle agent variants" subsection + changelog entry. |

## Edge Cases & Risks
- **Misplaced Patterns entry.** If the Patterns section has a strict ordering convention (e.g., alphabetic), respect it. The existing order in `CLAUDE.md` is thematic — group with other "single-source-of-truth" patterns ("Two entry points, one core", "Tool definition doubles as policy action space").
- **Future-variant placeholder.** The "Current variants" table mentions `@0.4.0-auto` (FEAT-016) as "planned". When FEAT-016 lands, that row updates from "planned" to a real entry — don't add the row pre-emptively here.
- **`docs/ARCHITECTURE.md` length.** Keep the addition tight — 1-2 paragraphs plus the small table. The Feature Brief and design docs carry the deep design; ARCHITECTURE.md is a signpost.
- **No `docs/data-model.md` or `docs/ui-specification.md` update needed.** This feature is purely flow-graph + bootstrap + signal payloads. No new entities, no new screens. Confirm by re-reading FEAT-015 §6 and §8.

## Acceptance Verification
- [ ] AC-1 — `CLAUDE.md` Quick Reference includes a `lifecycle-agent@0.4.0-manual` example command line.
- [ ] AC-2 — `CLAUDE.md` Patterns section includes the single-line variant naming + bootstrap-function convention entry.
- [ ] AC-3 — `docs/ARCHITECTURE.md` has a "Lifecycle agent variants" subsection with the two-variant table.
- [ ] AC-4 — `docs/ARCHITECTURE.md` changelog entry exists with today's date.
- [ ] AC-5 — Both files render cleanly on GitHub preview.
- [ ] AC-6 — No edits to `docs/data-model.md` (correctly — no entity changes) or `docs/ui-specification.md` (correctly — no UI).
