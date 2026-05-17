# Implementation Plan: T-314 — Extend `intakeSchema` for v0.3.0 and v0.4.0-manual

## Task Reference
- **Task ID:** T-314
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** The YAML `intakeSchema` is the agent-level contract; the DTO is the orchestrator-level contract. Both must align. The YAML stays permissive on shape (regex only) — the DTO is the tighter floor.

## Overview
Add a `codeSource` object property to the `intakeSchema` block of two agent YAMLs. During the deprecation window (`LIFECYCLE_CODE_SOURCE_REQUIRED=false`), `codeSource` is **defined but not in `required:`** — keeping the YAML aligned with the orchestrator-level soft mode. The follow-on PR that flips the env var also adds `codeSource` to `required:`.

## Implementation Steps

### Step 1: Read both agent YAMLs
**File:** `agents/lifecycle-agent@0.3.0.yaml`, `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Read

Locate the existing `intakeSchema` block in each. Note current shape — likely:

```yaml
intakeSchema:
  type: object
  required: [workItem]
  additionalProperties: false
  properties:
    workItem: {...}
```

Note the indentation (2 spaces).

### Step 2: Extend v0.3.0's `intakeSchema`
**File:** `agents/lifecycle-agent@0.3.0.yaml`
**Action:** Modify

Add the `codeSource` property under `properties:`. Do NOT add it to `required:` yet (deprecation-window posture):

```yaml
    codeSource:
      type: object
      required: [repo, baseBranch]
      additionalProperties: false
      properties:
        repo:
          type: string
          pattern: "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
        baseBranch:
          type: string
          minLength: 1
        workBranch:
          type: string
          minLength: 1
```

The nested `required: [repo, baseBranch]` means: IF `codeSource` is supplied, THEN `repo` + `baseBranch` are mandatory inside it. That's correct shape semantics even while top-level `codeSource` itself is optional.

### Step 3: Mirror the edit on v0.4.0-manual
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Same `codeSource` block, same placement under `intakeSchema.properties:`. Same exclusion from top-level `required:`.

### Step 4: Update header comments
**File:** Both YAMLs above
**Action:** Modify

If the top-of-file header comment lists intake fields, add a line for `codeSource`. Note the deprecation-window posture explicitly — e.g.:

```
# Intake inputs:
#   - workItem (required)
#   - codeSource (optional during deprecation window; becomes required when
#     LIFECYCLE_CODE_SOURCE_REQUIRED=true)
```

### Step 5: Verify legacy agents untouched
**File:** `agents/lifecycle-agent@0.2.0.yaml`, `agents/lifecycle-agent@0.1.0.yaml`
**Action:** Verify (no edit)

`git diff agents/lifecycle-agent@0.{1,2}.0.yaml` must show no changes. These are the demo / legacy LLM-policy agents — out of scope.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `agents/lifecycle-agent@0.3.0.yaml` | Modify | Add `codeSource` property + header comment. |
| `agents/lifecycle-agent@0.4.0-manual.yaml` | Modify | Same. |

## Edge Cases & Risks
- **Top-level `required:` deliberately untouched.** During the deprecation window the YAML's required-ness is in lockstep with the env flag. A follow-on one-line PR adds `codeSource` to `required:` when the flag flips. Don't preempt — operators with scripts that omit `codeSource` need the soft window.
- **The nested `required: [repo, baseBranch]` IS in place.** If an operator supplies `codeSource: {}`, the agent loader rejects it at load time — which is fine, because the orchestrator setting only governs presence, not shape.
- **`additionalProperties: false` inside `codeSource`** mirrors the DTO's `extra="forbid"`. Catches typos like `workbranch`.
- **YAML indentation:** the `properties:` block under `intakeSchema:` uses 4-space indent inside a 2-space outer scope. Match the existing pattern exactly — `pyyaml` is forgiving but `jsonschema` validators downstream are not, and a misaligned key silently lands at the wrong level.
- **Boot validation:** `uv run uvicorn app.main:app` exercises the agent loader at startup. If the YAML is malformed, boot fails fast — that's the test.

## Acceptance Verification
- [ ] `lifecycle-agent@0.3.0.yaml` `intakeSchema.properties` includes `codeSource` with the shape above.
- [ ] `lifecycle-agent@0.4.0-manual.yaml` same.
- [ ] Neither YAML adds `codeSource` to top-level `required:` (deprecation posture).
- [ ] `lifecycle-agent@0.2.0.yaml` and `lifecycle-agent@0.1.0.yaml` byte-unchanged.
- [ ] Header comments on edited YAMLs list `codeSource` with the deprecation-window note.
- [ ] `uv run uvicorn app.main:app` boots cleanly.
- [ ] A test POST with a well-formed `codeSource` against either agent succeeds.
- [ ] A test POST with `codeSource: {}` against either agent is rejected by the agent loader (nested `required` enforcement).
