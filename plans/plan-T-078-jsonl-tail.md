# Implementation Plan: T-078 — `JsonlTraceStore.tail_run_stream` polling tail + filters

## Task Reference
- **Task ID:** T-078
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-077

## Overview
Implement the load-bearing reader: open the run's JSONL file read-only, replay existing lines with `kinds` / `since` filters, and — in follow mode — poll every 200 ms for new lines without touching the writer's per-run lock.

## Steps

### 1. Modify `src/app/modules/ai/trace_jsonl.py`
- Add module-level constant `_TAIL_POLL_SECONDS = 0.2` so tests can `monkeypatch.setattr(...)` it.
- Extract the existing `_replay(path)` function's per-line parsing into a private helper `_parse_line(line, path, line_number) -> StepDto | PolicyCallDto | WebhookEventDto | None`:
  - Returns `None` for a malformed line (logs a `WARNING` with `path` + `line_number`).
  - Returns the hydrated DTO for a valid line (using the existing `_DTO_BY_KIND` map).
- Add `_record_timestamp(dto)` helper: returns `dto.created_at` for `StepDto`/`PolicyCallDto` when present; `dto.received_at` for `WebhookEventDto`; `None` otherwise.  Used by the `since` filter.

  Note: FEAT-002's `StepDto` does NOT carry a `created_at` field (check `schemas.py`); for steps, fall back to `dto.dispatched_at or dto.completed_at` — whichever is non-null.  If both are `None`, the record passes the `since` filter (rationale: records without a timestamp are included by default so `since` is a lower bound, not a mandatory exclude).
- Implement `tail_run_stream`:
  ```python
  async def tail_run_stream(
      self,
      run_id: uuid.UUID,
      *,
      follow: bool = False,
      since: datetime | None = None,
      kinds: frozenset[str] | None = None,
  ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]:
      effective_kinds = kinds if kinds else None
      path = self._path(run_id)

      # Filename-await under follow.
      if not path.is_file():
          if not follow:
              return _empty_async_iterator()
          while not path.is_file():
              await asyncio.sleep(_TAIL_POLL_SECONDS)

      return _tail(path, follow, since, effective_kinds)
  ```
- Add the private `_tail` async generator:
  ```python
  async def _tail(
      path: Path,
      follow: bool,
      since: datetime | None,
      kinds: frozenset[str] | None,
  ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]:
      async with aiofiles.open(path, encoding="utf-8") as f:
          line_no = 0
          while True:
              async for raw in f:
                  line_no += 1
                  line = raw.strip()
                  if not line:
                      continue
                  record = json.loads(line)
                  kind = record.get("kind")
                  if kinds is not None and kind not in kinds:
                      continue
                  dto = _parse_line(line, path, line_no)
                  if dto is None:
                      continue
                  if since is not None:
                      ts = _record_timestamp(dto)
                      if ts is not None and ts < since:
                          continue
                  yield dto
              if not follow:
                  return
              await asyncio.sleep(_TAIL_POLL_SECONDS)
  ```
  The outer `while True` keeps trying after `async for raw in f` exhausts; `aiofiles` returns from the inner loop on EOF, but re-entering it after a sleep picks up new lines the writer has appended (aiofiles follows the file descriptor, so new bytes after the cursor are seen).
- Update `_replay` (used by `open_run_stream`) to delegate to `_parse_line` + the same `_DTO_BY_KIND` dispatch — DRY, and a single point of truth for the "unknown kind" warning.

### 2. Modify `tests/modules/ai/test_trace_jsonl.py`
- Existing tests must stay green.  Add 8 new tests under a new `TestTailRunStream` class (or structured inside existing classes):
  1. `test_non_follow_yields_every_committed_line` — write 3 records via the store, call `tail_run_stream(follow=False)`, assert 3 DTOs of the right types.
  2. `test_non_follow_missing_file_yields_empty` — unknown run id, empty iterator.
  3. `test_follow_streams_new_lines_as_writer_appends` — monkeypatch `_TAIL_POLL_SECONDS = 0.01`; spawn a writer task that appends 5 records with `asyncio.sleep(0.01)` between each; assert the reader yields all 5, in order.
  4. `test_follow_waits_for_filename` — reader starts before any writes; writer creates file + 1 record; assert reader yields the record.
  5. `test_kinds_filter_narrows_stream` — write 1 step + 1 policy_call + 1 webhook_event; tail with `kinds=frozenset({"step"})`; assert only the step.
  6. `test_since_filter_excludes_earlier_records` — write 3 records; tail with `since=<timestamp in the middle>`; assert only later records yielded.
  7. `test_concurrent_readers_see_same_lines` — spawn 2 tail tasks + 1 writer task; both readers yield the full list.
  8. `test_malformed_line_logged_and_skipped` — write directly to the file: valid / garbage / valid; monkeypatch a spy on `logger.warning`; tail assert yields 2 DTOs + 1 WARNING call.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/trace_jsonl.py` | Modify | `tail_run_stream` + `_tail` + helpers; `_replay` refactor. |
| `tests/modules/ai/test_trace_jsonl.py` | Modify | 8 new reader tests. |

## Edge Cases & Risks
- `aiofiles` may cache EOF; always close and reopen is the safe play, but slow.  The `async for raw in f` + `await asyncio.sleep(...)` + re-iterating trick works because aiofiles' text-mode iterator resumes from the last read position.  If a test reveals it doesn't, fall back to `await f.seek(await f.tell())` before the next `async for`.
- The existing `_locks` dict in `JsonlTraceStore` is for *writers*.  Readers MUST NOT touch it — they open their own read-only handles and the OS's page cache handles concurrent access.
- Malformed-line handling: `json.loads(line)` can raise `json.JSONDecodeError`.  Wrap in try/except inside `_tail` and call `_parse_line`'s warning path.
- `since` on a `StepDto` whose `dispatched_at` and `completed_at` are both `None` (freshly-persisted pending step): the record passes the filter.  That matches the "lower bound, don't exclude ambiguous" semantic stated in step 1.
- Tests running in parallel may share one `tmp_path` — pytest isolates by test so that's fine, but DO NOT share a `run_id` across tests (the test writes + the store's in-memory lock map would interfere).

## Acceptance Verification
- [ ] Non-follow yields every committed line, in order, and closes on EOF.
- [ ] Missing file + `follow=False` → empty iterator (no error).
- [ ] Filename-await under follow: file created *after* iterator starts is still streamed from the start.
- [ ] `kinds` filter rejects other kinds.
- [ ] `since` filter excludes earlier-timestamped records.
- [ ] Two concurrent readers see the same lines.
- [ ] Malformed line → WARNING + skip; stream does not error.
- [ ] `uv run pyright src/` + `uv run ruff check .` clean.
