# Implementation Plan: T-035 — JSONL `TraceStore` implementation (AD-5 v1)

## Task Reference
- **Task ID:** T-035
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-031

## Overview
First real `TraceStore` implementation per AD-5 v1. Appends NDJSON to `.trace/<run_id>.jsonl` with per-run `asyncio.Lock` serialization. Factory dispatches on `Settings.trace_backend`.

## Steps

### 1. Modify `pyproject.toml`
- Add `"aiofiles>=23,<25"` to `[project].dependencies`.

### 2. Modify `src/app/config.py`
- Add `trace_dir: Path = Path(".trace")`.
- Add `trace_backend: Literal["noop", "jsonl"] = "jsonl"`.

### 3. Create `src/app/modules/ai/trace_jsonl.py`
- Imports: `aiofiles`, `asyncio`, `json`, `os`, `uuid`, `pathlib.Path`, `AsyncIterator`.
- `class JsonlTraceStore:` (implements `TraceStore` Protocol):
  - `__init__(self, trace_dir: Path)`: store `self._dir = trace_dir`; `self._locks: dict[UUID, asyncio.Lock] = {}`; `self._locks_guard = asyncio.Lock()`.
  - Private `_path(run_id) -> Path`: `self._dir / f"{run_id}.jsonl"`.
  - Private `async _lock_for(run_id) -> asyncio.Lock`: guarded map-or-create, returns per-run lock.
  - Private `async _append(run_id, record: dict)`: serialize `json.dumps(record, default=str, sort_keys=False) + "\n"`; `async with _lock_for(run_id)` → ensure `self._dir` exists (`mkdir(parents=True, exist_ok=True)`); open in `"a"` mode via `aiofiles.open`; write; if file was just created, `os.chmod(path, 0o600)`.
  - `async record_step(run_id, step: StepDto)`: call `_append(run_id, {"kind": "step", **step.model_dump(mode="json", by_alias=True)})`.
  - `async record_policy_call(run_id, call: PolicyCallDto)`: same pattern, `"kind": "policy_call"`.
  - `async record_webhook_event(run_id, event: WebhookEventDto)`: same pattern, `"kind": "webhook_event"`.
  - `async open_run_stream(run_id) -> AsyncIterator[dict]`: open file, iterate lines, `yield json.loads(line)` per line. If file missing, yield nothing. (Live tailing deferred to FEAT-004.)

### 4. Modify `src/app/modules/ai/trace.py`
- Update factory `get_trace_store()` to branch on `Settings.trace_backend`:
  - `"noop"` → `NoopTraceStore()`.
  - `"jsonl"` → `JsonlTraceStore(settings.trace_dir)`.

### 5. Create `tests/modules/ai/test_trace_jsonl.py`
- Fixture: `tmp_path` for `trace_dir`.
- Test file creation + file mode `0o600` after first write.
- Test append semantics: 3 sequential `record_step` calls → 3 lines in file.
- Test `open_run_stream` replays written records in order.
- Concurrency test: 50 `asyncio.gather` writes → 50 valid JSON lines, no interleave.
- Cross-run independence: two `run_id`s write to independent files; no cross-locking stalls.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `aiofiles`. |
| `src/app/config.py` | Modify | `trace_dir`, `trace_backend`. |
| `src/app/modules/ai/trace_jsonl.py` | Create | `JsonlTraceStore` implementation. |
| `src/app/modules/ai/trace.py` | Modify | Factory dispatch. |
| `tests/modules/ai/test_trace_jsonl.py` | Create | Trace store unit tests. |

## Edge Cases & Risks
- File-handle lifetime: opening/closing per write is slow but leak-free. Revisit in FEAT-005 load-test if needed (flagged in FEAT-002 §Risks).
- `trace_dir` inside a Docker volume with root-only perms: chmod may fail silently — catch and warn, don't block the run.
- Serialization of `datetime`: use `default=str` or Pydantic `model_dump(mode="json")` so timestamps serialize as ISO8601 strings.

## Acceptance Verification
- [ ] Three write methods produce valid NDJSON.
- [ ] File mode `0o600` after first write.
- [ ] 50-concurrent test: no interleaved bytes.
- [ ] Factory returns correct impl per `trace_backend`.
- [ ] `uv run pyright` clean (Protocol conformance check).
