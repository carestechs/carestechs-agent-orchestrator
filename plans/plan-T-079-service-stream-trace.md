# Implementation Plan: T-079 — `service.stream_trace` terminal-state close detection

## Task Reference
- **Task ID:** T-079
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-078

## Overview
Wire the service layer: validate the run exists, delegate to `trace.tail_run_stream`, serialize each yielded DTO as an NDJSON line, and — in follow mode — close the stream cleanly once `Run.status` is terminal AND the tail has been quiet for two consecutive poll intervals.

## Steps

### 1. Modify `src/app/modules/ai/service.py`
- Import: `from app.modules.ai.schemas import StepDto, PolicyCallDto, WebhookEventDto` (likely already imported); add `from collections.abc import AsyncIterator` if missing.
- Add module-level `_TAIL_POLL_SECONDS = 0.2` for the terminal-close poll cadence (distinct from the JSONL store's own poll constant).
- Replace the `stream_trace` body:
  ```python
  async def stream_trace(
      run_id: uuid.UUID,
      *,
      db: AsyncSession,
      trace: TraceStore,
      follow: bool = False,
      since: datetime | None = None,
      kinds: frozenset[str] | None = None,
  ) -> AsyncIterator[str]:
      run = await repository.get_run_by_id(db, run_id)
      if run is None:
          raise NotFoundError(f"run not found: {run_id}")

      iterator = trace.tail_run_stream(
          run_id, follow=follow, since=since, kinds=kinds,
      )

      if not follow:
          async for dto in iterator:
              yield _serialize(dto)
          return

      # Follow mode — close after two consecutive quiet polls once the run is terminal.
      empty_polls = 0
      while True:
          produced = False
          async for dto in iterator:
              produced = True
              yield _serialize(dto)
          if not produced:
              empty_polls += 1
          else:
              empty_polls = 0

          # Re-check run status fresh per poll.
          async with ???  # see Note
  ```

  **Note:** the existing pattern across the loop opens a new session per iteration via `session_factory`.  BUT `stream_trace` runs inside a FastAPI request handler that already holds a `db: AsyncSession` from `get_db_session`.  To re-check `Run.status` without polluting that session with a stale read, use `await db.refresh(run)` between polls.  If that becomes awkward, inject `session_factory` too and open a short-lived session per status check.  Pick the simpler option that tests don't fight.
- Rewrite the body using the simpler plan (refresh the already-fetched run):
  ```python
  async def stream_trace(
      run_id: uuid.UUID,
      *,
      db: AsyncSession,
      trace: TraceStore,
      follow: bool = False,
      since: datetime | None = None,
      kinds: frozenset[str] | None = None,
  ) -> AsyncIterator[str]:
      run = await repository.get_run_by_id(db, run_id)
      if run is None:
          raise NotFoundError(f"run not found: {run_id}")

      iterator = trace.tail_run_stream(
          run_id, follow=follow, since=since, kinds=kinds,
      )
      aiter = iterator.__aiter__()

      if not follow:
          async for dto in iterator:
              yield _serialize(dto)
          return

      empty_polls = 0
      while True:
          try:
              dto = await asyncio.wait_for(aiter.__anext__(), timeout=_TAIL_POLL_SECONDS)
              yield _serialize(dto)
              empty_polls = 0
              continue
          except TimeoutError:
              pass
          except StopAsyncIteration:
              break

          await db.refresh(run)
          if RunStatus(run.status) in _TERMINAL_RUN_STATUSES and empty_polls >= 1:
              break
          empty_polls += 1
  ```
  - `_TERMINAL_RUN_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}` — add as module-level constant (reuses the existing `_TERMINAL_STATUSES` set from `cancel_run` if present; check and dedupe).
  - The `asyncio.wait_for` with a short timeout gives the underlying JSONL tail time to yield a record AND bounds our own poll cadence at the service layer.
- Add a small helper `def _serialize(dto) -> str`:
  ```python
  kind = _KIND_BY_TYPE[type(dto)]
  return json.dumps({
      "kind": kind,
      "data": dto.model_dump(mode="json", by_alias=True),
  }) + "\n"
  ```
  where `_KIND_BY_TYPE: dict[type, str]` is the inverse of the JSONL writer's `_DTO_BY_KIND`.  Prefer defining `_KIND_BY_TYPE` as a module-level constant next to the DTO imports.

### 2. Create `tests/modules/ai/test_service_stream_trace.py`
- Fixtures:
  - `seeded_trace_store(tmp_path)` → a `JsonlTraceStore(tmp_path)` with a handful of pre-written lines for a known `run_id`.
  - `seeded_run(db_session, run_id)` → insert a `Run` with status `completed` (and later, tests that need a running run will insert `running` + flip to terminal mid-test).
- Cases:
  - `test_unknown_run_raises_not_found` — call with a random UUID; `pytest.raises(NotFoundError)`; assert no bytes yielded (check: calling `async for _ in iter: break` should not leak anything; easier: assert NotFoundError raises before the generator is iterated).
  - `test_non_follow_emits_every_line_once` — seed 3 records, call with `follow=False`, collect lines.  Assert 3 lines each starting with `{"kind":` and each ending in `\n`.
  - `test_follow_terminal_run_closes_within_budget` — seed a completed run + 3 trace lines; call with `follow=True`; measure wall-clock; assert iterator closes within ~1s even though `follow=True`.
  - `test_follow_filters_forwarded` — pass `kinds={"step"}`; assert non-step lines filtered out.
  - `test_serialize_is_valid_ndjson` — parse every yielded line as JSON + confirm the outer shape is `{"kind": "...", "data": {...}}`.
  - Monkeypatch `_TAIL_POLL_SECONDS` (both in `service` and `trace_jsonl`) to 0.01 for fast tests.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/service.py` | Modify | Real `stream_trace` body + helpers + terminal-state const. |
| `tests/modules/ai/test_service_stream_trace.py` | Create | 5 unit tests. |

## Edge Cases & Risks
- `asyncio.wait_for` + `aiter.__anext__()` cancels the pending `__anext__` when the timeout fires.  The underlying `aiofiles` file object should handle cancel cleanly (generator is resumed on the next attempt).  If this pattern turns out to leak file handles in CI, switch to a manual sleep loop around `anext` without `wait_for`.
- `db.refresh(run)` issues a SELECT each poll.  At a 200ms cadence for a terminal run this adds up; the quiet-poll check (`empty_polls >= 1`) caps total extra selects at 2 per stream close.  Acceptable.
- The `Run` model has `lazy="raise"` on relationships — `db.refresh(run)` may fail if it accidentally triggers an eager load.  SQLAlchemy's `refresh` only reloads column attributes by default; confirm with a test.
- If `follow=False` and the JSONL file doesn't exist yet (very new run): the tail yields nothing → empty stream.  That matches AC-5 for noop backend and is fine here too.
- Don't leak `db: AsyncSession` across the whole stream lifetime in production — FastAPI's `get_db_session` already scopes it to the request, and `StreamingResponse` holds the request open until the stream drains.  For very long follow-mode runs, the session stays open.  Acceptable for v1 since Postgres pool is small and the session does near-zero work per poll; revisit if operators report pool exhaustion.

## Acceptance Verification
- [ ] Unknown run → `NotFoundError` before any yield.
- [ ] Non-follow mode yields every record exactly once.
- [ ] Follow mode closes within a generous bound (1 s) on a terminal run.
- [ ] `kinds` + `since` filters forwarded to the trace store verbatim.
- [ ] Every yielded string is a valid NDJSON line ending in `\n`.
- [ ] `uv run pyright src/` + `uv run ruff check .` clean.
