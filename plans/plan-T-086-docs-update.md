# Implementation Plan: T-086 — Stub-list audit + documentation updates

## Task Reference
- **Task ID:** T-086
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-080, T-081

## Overview
Close the loop: clean up the now-stale stub-list fixtures, update 5 docs to reflect that trace streaming is real (no more "deferred to FEAT-004" notes), and flip the FEAT-004 brief to `Completed`.

## Steps

### 1. Modify `tests/modules/ai/test_routes_control_plane.py`
- `_STUBBED_ENDPOINTS` is now either empty or contains a single stale entry (the trace endpoint — already removed in T-080).
- If the list is empty: keep it plus the `TestAuthenticatedStub501` class; pytest collects zero parametrize cases and the class becomes a regression tripwire (any future stub reintroduced will have to re-populate this list).
- If the class is noisy as a no-op, **delete** it and also drop the `_STUBBED_ENDPOINTS` constant.  Keep the `_ENDPOINTS` list (used by `TestUnauthenticated`).

### 2. Modify `tests/test_cli_stubs.py`
- Same choice for `_STUB_INVOCATIONS` + `TestStubsExit2`.  Prefer the empty-list-as-guard variant unless pytest warns about empty parametrize.

### 3. Modify `CLAUDE.md`
- Remove or update any "FEAT-004"/"deferred" references that mention `runs trace` or `stream_trace`.  Grep the file for `FEAT-004` and `stream_trace` before editing.
- No new "Patterns" or "Anti-patterns" entries.  The streaming endpoint follows the same thin-adapter rule as every other route.

### 4. Modify `docs/ARCHITECTURE.md`
- Under "### Runtime Loop Components", append a one-line bullet:
  - `**Trace streaming** (FEAT-004) — ``GET /api/v1/runs/{id}/trace`` returns the run's JSONL trace as ``application/x-ndjson``, optionally tailing live writes (``?follow=true``) and filtering by kind/since.  Reader path lives in ``JsonlTraceStore.tail_run_stream``; the service wraps the iterator with terminal-state close detection.`
- Add changelog entry at the bottom:
  - `2026-04-18 — FEAT-004 — Added trace-streaming reader; documented the NDJSON endpoint and CLI ``runs trace`` command.  No entity or API-contract changes beyond filling the existing ``/runs/{id}/trace`` endpoint's reserved shape.`

### 5. Modify `docs/ui-specification.md`
- Find the `runs trace` and `run --follow` entries.  Remove any "deferred to FEAT-004" language.
- Describe the real CLI behavior:
  - `runs trace <id>` — dumps the trace once.
  - `runs trace <id> --follow` — tails; Ctrl-C exits 0.
  - `--since`, `--kind` (repeatable), `--json` flags.
  - Exit codes: `0` normal / SIGINT, `1` run not found, `2` missing API key, `3` 5xx.
- Add changelog entry:
  - `2026-04-18 — FEAT-004 — ``runs trace`` + ``run --follow`` are now real.  Documented filter flags, exit codes, and the NDJSON output contract.`

### 6. Modify `docs/api-spec.md`
- Find the `/api/v1/runs/{id}/trace` endpoint entry.  Replace any "501 stub" note with the real response shape:
  - `200 OK` / `application/x-ndjson` / chunked.
  - Query params: `follow`, `since`, `kind` (repeatable).
  - Body: one JSON object per line, shape `{"kind": ..., "data": <DTO>}`.
  - `404` Problem Details on unknown run id.
- Add changelog entry:
  - `2026-04-18 — FEAT-004 — ``GET /runs/{id}/trace`` graduates from 501 stub to live NDJSON stream.  Reserved contract from FEAT-001 unchanged.`

### 7. Modify `README.md`
- Extend the "First Run" section with a trace step after step 4:
  ```markdown
  # 5. Watch the trace live (in yet another terminal).
  uv run orchestrator runs trace <run-id> --follow
  ```
- Shift the existing trace-file step (`cat .trace/<run-id>.jsonl | jq`) to note that `orchestrator runs trace --json` is the HTTP-friendly alternative.
- Verify `wc -l README.md` stays ≤ 150.

### 8. Modify `docs/work-items/FEAT-004-trace-streaming.md`
- Flip the Status field: `Not Started` → `Completed`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_routes_control_plane.py` | Modify | Stub-list audit. |
| `tests/test_cli_stubs.py` | Modify | Stub-list audit. |
| `CLAUDE.md` | Modify | Remove deferral notes. |
| `docs/ARCHITECTURE.md` | Modify | Runtime Loop bullet + changelog. |
| `docs/ui-specification.md` | Modify | Real `runs trace` behavior + changelog. |
| `docs/api-spec.md` | Modify | Replace 501-stub language + changelog. |
| `README.md` | Modify | Extend First Run. |
| `docs/work-items/FEAT-004-trace-streaming.md` | Modify | Status → Completed. |

## Edge Cases & Risks
- `docs/data-model.md` is NOT in the list — the feature touches no entities, so no entry or changelog there.
- Changelog entries are one line, dated, FEAT-referenced.  Don't editorialize.
- Before marking the task done, `grep -r "FEAT-004" .` and `grep -r "stream_trace" .` to catch stray forward-references.

## Acceptance Verification
- [ ] `_STUBBED_ENDPOINTS` and `_STUB_INVOCATIONS` empty or deleted.
- [ ] 4 docs have a 2026-04-18 FEAT-004 changelog entry.
- [ ] README ≤ 150 lines.
- [ ] FEAT-004 brief Status = `Completed`.
- [ ] `grep -r "deferred to FEAT-004"` returns no hits in docs.
- [ ] Full test suite + ruff + pyright green after docs-only PR.
