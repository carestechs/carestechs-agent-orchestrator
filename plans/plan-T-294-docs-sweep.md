# Implementation Plan: T-294 — Documentation sweep + anti-pattern entry

## Task Reference
- **Task ID:** T-294
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-9. Per CLAUDE.md's "Documentation Maintenance Discipline" table, every entity / endpoint / pattern change requires a doc update in the same PR. Without the anti-pattern entry in CLAUDE.md, the next AI agent will reintroduce a disk read.

## Overview
Round-trip update to `data-model.md`, `api-spec.md`, and `CLAUDE.md` capturing the final shape of FEAT-014. Flip the brief's Status to `Completed`.

## Implementation Steps

### Step 1: `docs/data-model.md` — `WorkItem` entity update
**File:** `docs/data-model.md`
**Action:** Modify
Find the `WorkItem` entity section. Update the fields table to include the two new columns (T-282 left stubs; this step finalizes them):
- `body_md` (TEXT, nullable) — markdown body uploaded by the client; NULL for pre-FEAT-014 rows backed by `source_path`.
- `body_sha256` (TEXT, nullable, 64 hex chars, CHECK `^[0-9a-f]{64}$`) — content hash for idempotent dedupe.

Add a "Business rules" entry:
> **Briefs are content-addressed and immutable.** Once `body_sha256` is populated, the body cannot be changed; re-uploads with a different body are rejected with HTTP 409 (`work-item-content-conflict`). To replace a brief, register a new `external_ref`.

Add a changelog entry at the bottom of the file (per CLAUDE.md changelog rule):
```
### 2026-05-XX — FEAT-014 (work-item upload)
- Added `WorkItem.body_md` (TEXT, nullable) — uploaded markdown body.
- Added `WorkItem.body_sha256` (TEXT, nullable, CHECK `^[0-9a-f]{64}$`) — content hash.
- Migration revision: `<rev_id_from_T-282>`.
```

### Step 2: `docs/api-spec.md` — intake shape + error catalog + new endpoint
**File:** `docs/api-spec.md`
**Action:** Modify
Three sub-changes:

1. **`POST /api/v1/runs` intake schema** — document `intake.workItem` as the canonical key. List `workItemPath` under "Deprecated keys" with a removal note.

2. **Problem Details catalog** — add four entries:
   - `work-item-not-registered` — 400 — emitted when `intake.workItem` references an `id` with no stored row and no content was supplied.
   - `work-item-content-conflict` — 409 — emitted on sha256 mismatch; response `meta` includes `storedSha256`, `uploadedSha256`.
   - `work-item-kind-conflict` — 409 — emitted on kind mismatch; response `meta` includes `storedKind`, `uploadedKind`.
   - `payload-too-large` — 413 — emitted when `intake.workItem.content` exceeds `INTAKE_WORK_ITEM_MAX_BYTES`.

3. **New endpoint `POST /api/v1/work-items`** — document:
   - Body: `RunIntakeWorkItem`.
   - Responses: 201 on insert, 200 on idempotent reuse, 409 on conflict.
   - Auth: same as control plane.
   - Purpose: import without starting a run; used by `orchestrator import-work-items`.

Changelog entry at the bottom referencing FEAT-014.

### Step 3: `CLAUDE.md` — patterns, anti-patterns, command examples
**File:** `CLAUDE.md`
**Action:** Modify

**Patterns section** — add a new bullet near the trace-protocol entry:
> - **Work-item bodies live in the DB.** `_handle_request_work_item_load` and every downstream consumer read brief content from `WorkItem.body_md` keyed on `external_ref`. The orchestrator process never opens a file under `docs/work-items/` after FEAT-014 — verified by `tests/test_executors_dont_read_briefs.py`. The single exception is the T-288 deprecation shim, tagged `# DEPRECATED FEAT-014` for removal in the next minor.

**Anti-Patterns section** — add a new bullet:
> - **Don't read a work-item brief from the filesystem in an executor.** The body lives in `WorkItem.body_md`. Disk reads of `docs/work-items/*.md` are forbidden in executor handlers (enforced by structural guard `tests/test_executors_dont_read_briefs.py`). The legacy `intake.workItemPath` shape is deprecated; new callers must use `intake.workItem = {id, kind, content?}`.

**Quick Reference commands** — update the run-start example:
```bash
# Before FEAT-014:
uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=docs/work-items/FEAT-042.md --follow

# After FEAT-014:
uv run orchestrator run lifecycle-agent@0.3.0 --work-item docs/work-items/FEAT-042.md --follow

# Operator import (FEAT-014 / T-290):
uv run orchestrator import-work-items docs/work-items/
```

### Step 4: Flip FEAT-014 brief Status
**File:** `docs/work-items/FEAT-014-work-item-upload-not-filesystem-read.md`
**Action:** Modify
Change the Status row from `Not Started` to:
> **Status** | Completed (T-282..T-294 all landed)

This step is the *final commit* of the FEAT — mirrors the FEAT-013 closeout pattern.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/data-model.md` | Modify | `WorkItem` entity + immutability rule + changelog |
| `docs/api-spec.md` | Modify | Intake shape + 4 error codes + new endpoint + changelog |
| `CLAUDE.md` | Modify | Pattern + anti-pattern + Quick Reference command examples |
| `docs/work-items/FEAT-014-work-item-upload-not-filesystem-read.md` | Modify | Status → Completed |

## Edge Cases & Risks
- **Changelog format.** Each updated doc needs an entry per `.ai-framework/guides/maintenance.md`. The format above matches the FEAT-013 closeout entries — reuse it.
- **CLAUDE.md is the most-read file** by both humans and AI agents. The anti-pattern entry must be unambiguous and the test guard must be cited (so the cost of regression is concrete).
- **Anti-pattern wording.** "Don't read a work-item brief from the filesystem in an executor" — phrased as a behavior rule, not a code pattern, to keep it stable across refactors.
- **Forgetting the brief Status flip.** Reviewers should catch it via the FEAT-014 commit graph; making it the last commit in the closeout PR is the discipline.

## Acceptance Verification
- [ ] All three docs updated; each has a changelog entry at the bottom referencing FEAT-014.
- [ ] `grep -n "workItemPath" CLAUDE.md` returns the deprecation note only (no recommended-pattern usage).
- [ ] CLAUDE.md Quick Reference shows `--work-item` example, not `--intake workItemPath=`.
- [ ] `grep -A2 "Don't read a work-item brief" CLAUDE.md` cites the structural guard test by file path.
- [ ] FEAT-014 brief Status flipped to `Completed (T-282..T-294 all landed)`.
