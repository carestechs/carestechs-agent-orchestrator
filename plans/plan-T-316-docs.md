# Implementation Plan: T-316 — Docs (api-spec, data-model note, CLAUDE.md pattern bullet)

## Task Reference
- **Task ID:** T-316
- **Type:** Docs
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Doc-first discipline per CLAUDE.md Documentation Maintenance table. Without the CLAUDE.md bullet, the next agent author reads `ctx.intake["codeSource"]` directly and bypasses the accessor.

## Overview
Three doc edits — `api-spec.md` (intake contract + changelog), `data-model.md` (one-line note on `Run.intake` + changelog), `CLAUDE.md` (new pattern bullet). One env-var mention in the relevant config doc surface.

## Implementation Steps

### Step 1: Extend `docs/api-spec.md`
**File:** `docs/api-spec.md`
**Action:** Modify

Find the `POST /api/v1/runs` body schema. Add `codeSource` to the documented intake fields:

```jsonc
{
  "agentRef": "lifecycle-agent@0.4.0-manual",
  "intake": {
    "workItem": { ... },
    "codeSource": {
      "repo": "carestechs/carestechs-agent-orchestrator",
      "baseBranch": "main",
      "workBranch": "feat/imp-042"   // optional
    }
  }
}
```

Add a `CodeSourceDto` definition block alongside `WorkItemIntakeDto` documenting each field's validation rule:

- `repo`: GitHub `owner/name`; no URL prefix; no `.git` suffix; matches `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`.
- `baseBranch`: non-empty; no leading `/`; no `..`; no whitespace; no control chars.
- `workBranch`: optional; same rules as `baseBranch`.

Note the deprecation flag explicitly in the body's prose: "When `LIFECYCLE_CODE_SOURCE_REQUIRED=false` (default during the deprecation window), `codeSource` is optional and missing values log a warning. When `true`, missing `codeSource` returns 400 `intake-validation-failed`."

Append a changelog entry at the bottom:

```markdown
- **2026-05-17 (IMP-005):** Added `codeSource` block to the `POST /api/v1/runs`
  intake contract. Fields: `repo` (GitHub `owner/name`, required), `baseBranch`
  (required), `workBranch` (optional). Currently optional behind
  `LIFECYCLE_CODE_SOURCE_REQUIRED=false`; will become required when the
  deprecation window closes. Persists to `Run.intake.codeSource`; read by
  executors via `read_code_source(ctx, memory=...)`.
```

### Step 2: Annotate `docs/data-model.md`
**File:** `docs/data-model.md`
**Action:** Modify

Find the `Run.intake` field description. Append a "Well-known keys" note (or extend an existing one if present):

> **Well-known intake keys:** `workItem` (FEAT-014), `codeSource` (IMP-005 — `{repo, baseBranch, workBranch?}`). The JSONB column has no schema enforcement at the SQL layer; shape validation happens at the route via Pydantic.

Append a changelog entry:

```markdown
- **2026-05-17 (IMP-005):** Documented `codeSource` as a well-known key on
  `Run.intake`. No SQL schema change — the field rides the existing JSONB
  column.
```

### Step 3: Add the CLAUDE.md pattern bullet
**File:** `CLAUDE.md`
**Action:** Modify

In the "Patterns to Follow" section, insert a new bullet near the FEAT-014 "Work-item bodies live in the DB" bullet (semantically adjacent):

> - **Code source lives on intake, read through `read_code_source`.** Every run's `(repo, baseBranch)` and optional `workBranch` are persisted on `Run.intake.codeSource` at run start (validated by `CodeSourceDto`). Executors MUST read it via `from app.modules.ai.executors.code_source import read_code_source` — direct access to `ctx.intake["codeSource"]` bypasses the memory-sidecar precedence and is a review blocker. Read order is fixed: memory sidecar's `workBranch` → intake's `workBranch` → None. Operator-supplied `workBranch` always wins; a producer executor (when one ships) writes `workBranch` to `RunMemory.data["codeSource"]` only when intake omitted it. No DB column — the field rides the existing `Run.intake` JSONB.

### Step 4: Document the env var
**File:** `CLAUDE.md` (Quick Reference section) or wherever `LIFECYCLE_MAX_CORRECTIONS` is documented
**Action:** Modify

Find the surface that lists other lifecycle env vars (e.g. `LIFECYCLE_MAX_CORRECTIONS`, `LIFECYCLE_REVIEWER`). Add:

```
LIFECYCLE_CODE_SOURCE_REQUIRED (default: false) — when true, POST /api/v1/runs
rejects intakes missing the `codeSource` block. Deprecation window default is
false; flip to true once all callers supply codeSource.
```

### Step 5: Verify no other docs need touching
**File:** N/A
**Action:** Verify

- `docs/ARCHITECTURE.md`: no — no new component or boundary.
- `docs/ui-specification.md`: N/A (no UI in v1).
- `docs/stakeholder-definition.md`: no — scope unchanged.
- `docs/personas/`: no.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/api-spec.md` | Modify | Body schema + DTO + prose + changelog. |
| `docs/data-model.md` | Modify | Well-known-keys note + changelog. |
| `CLAUDE.md` | Modify | New pattern bullet + env-var line. |

## Edge Cases & Risks
- **Changelog format consistency:** mirror the existing entries' date/IMP-id prefix. If the changelog uses a different bullet style, match it.
- **`CLAUDE.md` pattern bullet placement:** semantically adjacent to "Work-item bodies live in the DB" (FEAT-014). Don't insert mid-list in a way that breaks the existing logical grouping (deterministic-runtime patterns stay clustered, lifecycle patterns stay clustered).
- **Env var doc surface:** if `LIFECYCLE_REVIEWER` is documented in a `.env.example` file, mirror — add `LIFECYCLE_CODE_SOURCE_REQUIRED=false` there too. Check `.env.example` and `docker-compose.yml` for any env-var listing.
- **Don't update `docs/work-items/IMP-005...md`:** the work item is the source brief, not a maintenance target. Status flips to "Completed" only when all six tasks land (separate PR or final commit).

## Acceptance Verification
- [ ] `docs/api-spec.md` documents `CodeSourceDto` with all three field rules + an example payload.
- [ ] `docs/api-spec.md` changelog has the IMP-005 entry dated 2026-05-17.
- [ ] `docs/data-model.md` `Run.intake` description mentions `codeSource` as a well-known key.
- [ ] `docs/data-model.md` changelog has the IMP-005 entry.
- [ ] `CLAUDE.md` Patterns section has the "Code source lives on intake" bullet.
- [ ] `CLAUDE.md` lists `LIFECYCLE_CODE_SOURCE_REQUIRED` in the env-var surface.
- [ ] No edits to `docs/ARCHITECTURE.md` or `docs/ui-specification.md`.
- [ ] Reading the updated `CLAUDE.md` end-to-end, the new bullet integrates without breaking the surrounding list structure.
