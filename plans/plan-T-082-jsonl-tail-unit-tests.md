# Implementation Plan: T-082 — `JsonlTraceStore.tail_run_stream` unit tests

## Task Reference
- **Task ID:** T-082
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-078

## Overview
The reader is load-bearing; this task pins every invariant from the brief as a standalone failing-if-broken unit test.  All tests live in `tests/modules/ai/test_trace_jsonl.py` so the JSONL reader/writer coexist in one place.

## Steps

### 1. Modify `tests/modules/ai/test_trace_jsonl.py`
Add a new class `TestTailRunStream` with the 8 test methods listed in the task (some may have been stubbed during T-078 — if so, flesh them out here rather than duplicate).  Use helper factories already defined in the file (`_step_dto`, `_policy_call_dto`, `_webhook_event_dto`).

Each test should monkeypatch `_TAIL_POLL_SECONDS` to `0.01` at the top so follow-mode tests finish in milliseconds:
```python
monkeypatch.setattr("app.modules.ai.trace_jsonl._TAIL_POLL_SECONDS", 0.01)
```

1. **`test_non_follow_yields_every_committed_line`**
   - Write 3 records via `store.record_step` / `record_policy_call` / `record_webhook_event`.
   - `items = [item async for item in store.tail_run_stream(run_id, follow=False)]`.
   - Assert `len(items) == 3` and types match expected.

2. **`test_non_follow_missing_file_yields_empty`**
   - Generate a new `run_id`; do not write anything.
   - Iterator should yield nothing and close.

3. **`test_follow_streams_new_lines_as_writer_appends`**
   - Spawn a reader task that consumes with `follow=True` until it has 5 items or 2 s elapsed.
   - Spawn a writer task that appends 5 records with `asyncio.sleep(0.01)` between each.
   - `asyncio.gather(reader, writer)`.
   - Assert the reader saw all 5 in order.

4. **`test_follow_waits_for_filename`**
   - `run_id = uuid.uuid4()` — file does not exist yet.
   - Reader task: opens `tail_run_stream(run_id, follow=True)`, consumes until 1 item or 2 s elapsed.
   - Writer task: `asyncio.sleep(0.02)`, then writes 1 record via the store.
   - Assert reader yields the record.

5. **`test_kinds_filter_narrows_stream`**
   - Write 1 step + 1 policy_call + 1 webhook_event.
   - `async for item in store.tail_run_stream(run_id, follow=False, kinds=frozenset({"step"}))`.
   - Assert exactly 1 item, `isinstance(item, StepDto)`.

6. **`test_since_filter_excludes_earlier_records`**
   - Write 3 policy_call records manually with explicit `created_at` values at `t0`, `t1`, `t2`.
   - `async for item in store.tail_run_stream(run_id, follow=False, since=t1)`.
   - Assert 2 items yielded (t1 and t2 — note the semantics: `since` is a lower bound; `< since` excluded, `>= since` included).

7. **`test_concurrent_readers_see_same_lines`**
   - Write 5 records.
   - Spawn 2 reader tasks (`follow=False`) + one writer task that appends 3 more during the reads.
   - Readers may be launched before or after the 3 extra writes — since they are `follow=False`, they see only what's already flushed when they open the file.  Alternative: launch both with `follow=True`, start the writer, have each reader stop after collecting 8 items.
   - Prefer the follow variant: assert both readers collected the same 8 records in the same order (8 = 5 pre + 3 during).

8. **`test_malformed_line_logged_and_skipped`**
   - Write a file directly: one valid NDJSON line + one garbage line (`"{oops"`) + one valid NDJSON line.
   - Monkey-patch `logger.warning` on `app.modules.ai.trace_jsonl` with a spy (pattern from T-068).
   - `items = [item async for item in store.tail_run_stream(run_id, follow=False)]`.
   - Assert 2 items yielded + at least 1 WARNING call mentioning the bad line number.

### Quality
- All 8 should be marked `@pytest.mark.asyncio(loop_scope="function")`.
- Total runtime goal: under 3 s for the whole class.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_trace_jsonl.py` | Modify | 8 new tail tests. |

## Edge Cases & Risks
- The concurrent-reader test is the most likely to flake.  Give it generous asyncio.wait_for timeouts (2 s bounds) so CI cold-start doesn't cause false failures.
- The malformed-line test writes directly to the filesystem — bypass the store so the garbage line is actually in the file.  Use `aiofiles` or sync `path.write_text` inside the test.
- `since` comparison: if the store stores ISO-8601 strings in JSON and the DTO's `created_at` is a `datetime`, `model_validate` coerces on read.  The `_record_timestamp` helper from T-078 must return a `datetime` for the comparison to work.  Verify.

## Acceptance Verification
- [ ] 8 tests, all pass.
- [ ] Total runtime < 3 s.
- [ ] No leaked tasks or warnings.
