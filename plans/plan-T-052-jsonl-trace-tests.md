# Implementation Plan: T-052 — JSONL trace-store unit tests

## Task Reference
- **Task ID:** T-052
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-035

## Overview
Extend T-035 basic tests with concurrency, permissions, replay correctness, and multi-run isolation.

## Steps

### 1. Extend `tests/modules/ai/test_trace_jsonl.py`

Add:
- `test_file_mode_0600`: `os.stat(path).st_mode & 0o777 == 0o600` after first write.
- `test_concurrent_writes_same_run`:
  - Spawn 50 `asyncio.gather(...)` writes into one run's trace.
  - Assert file contains exactly 50 lines.
  - Assert every line is valid JSON (parse each).
  - Assert no line is a partial JSON fragment (re-parse whole file with `json.loads` per line).
- `test_independent_runs_no_cross_lock`:
  - 50 writes each into 2 different run IDs; measure total time; assert < 2× single-run time (verifies no cross-run contention).
- `test_replay_returns_written_records_in_order`:
  - Write step, policy_call, webhook_event in sequence.
  - `async for line in store.open_run_stream(run_id)` yields exactly those 3 in order.
- `test_replay_missing_file`:
  - Unknown run id → `open_run_stream` yields nothing (no file-not-found exception).
- `test_chmod_failure_does_not_block_writes`:
  - Monkeypatch `os.chmod` to raise; writes still succeed; warning logged.

### 2. Add fixtures to support the tests
- `trace_store` fixture wrapping `JsonlTraceStore(tmp_path)` so each test gets an isolated dir.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_trace_jsonl.py` | Modify | 6 new tests covering concurrency, perms, replay. |

## Edge Cases & Risks
- On fast local disks, 50 concurrent writes complete in < 10 ms — timing assertions need a generous CI bound or should be relative rather than absolute.
- `os.stat` mode bits don't include chmod results on Windows / some Dockers without a mounted volume — skip the mode test on platforms where it's unsupported via `pytest.mark.skipif(sys.platform == "win32", ...)`.

## Acceptance Verification
- [ ] 50 concurrent writes produce 50 valid JSON lines.
- [ ] Cross-run independence holds.
- [ ] Replay order correct.
- [ ] Missing file yields empty replay.
- [ ] Permission failure during chmod is non-fatal.
