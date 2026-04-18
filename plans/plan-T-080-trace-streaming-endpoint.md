# Implementation Plan: T-080 — `GET /api/v1/runs/{id}/trace` endpoint

## Task Reference
- **Task ID:** T-080
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-079

## Overview
Replace the placeholder route in `router.py` with a FastAPI `StreamingResponse` that consumes `service.stream_trace(...)` and emits NDJSON, carrying `Cache-Control: no-cache` and `X-Accel-Buffering: no` so proxies don't buffer.  Accepts `follow`, `since`, and repeatable `kind` query params.

## Steps

### 1. Modify `src/app/modules/ai/router.py`
- Import: `from datetime import datetime`, `from fastapi.responses import StreamingResponse` (or `starlette.responses.StreamingResponse` — match existing imports).
- Replace the existing `stream_trace` route:
  ```python
  @api_router.get("/runs/{run_id}/trace")
  async def stream_trace(
      run_id: uuid.UUID,
      db: Annotated[AsyncSession, Depends(get_db_session)],
      trace: Annotated[TraceStore, Depends(get_trace_store)],
      follow: Annotated[bool, Query()] = False,
      since: Annotated[datetime | None, Query()] = None,
      kind: Annotated[list[str] | None, Query()] = None,
  ) -> StreamingResponse:
      """Stream the run's trace as NDJSON.

      Query params:

      * ``follow=true`` keeps the stream open until the run terminates.
      * ``since=<ISO-8601>`` emits only records with a later timestamp.
      * ``kind=step`` / ``kind=policy_call`` / ``kind=webhook_event`` filters
        by record kind; the parameter is repeatable.
      """
      kinds: frozenset[str] | None = frozenset(kind) if kind else None
      iterator = service.stream_trace(
          run_id,
          db=db,
          trace=trace,
          follow=follow,
          since=since,
          kinds=kinds,
      )
      return StreamingResponse(
          iterator,
          media_type="application/x-ndjson",
          headers={
              "Cache-Control": "no-cache",
              "X-Accel-Buffering": "no",
          },
      )
  ```
- The `service.stream_trace` call raises `NotFoundError` BEFORE yielding — but note: `NotFoundError` won't be raised *until* the async generator is iterated.  FastAPI's `StreamingResponse` doesn't iterate until it starts writing the body.  Two ways to handle this:
  1. **Pre-flight check** in the route: call `repository.get_run_by_id(db, run_id)` here; raise `NotFoundError` before constructing the `StreamingResponse`.
  2. Rely on the global exception handler to catch `NotFoundError` thrown from inside the stream — but at that point headers have already been sent, so the handler can't return Problem Details.
  **Pick option 1** — move the 404 check into the route so `NotFoundError` raises before any bytes go out.  Keep the duplicate check inside `service.stream_trace` for safety / direct-call paths.
- The pre-flight check:
  ```python
  if await repository.get_run_by_id(db, run_id) is None:
      raise NotFoundError(f"run not found: {run_id}")
  ```
  Import `repository` at the top.  Check the existing router imports — may already have it.
- Adapter-thin: this route touches `AsyncSession` only as a type annotation via `Depends`, and `repository.get_run_by_id` is a one-line delegation.  The existing thin-adapter test allowlist already covers `AsyncSession`; no new imports trigger the walker.

### 2. Modify `tests/modules/ai/test_routes_control_plane.py`
- Remove `("GET", f"/api/v1/runs/{_RUN_ID}/trace", None)` from `_STUBBED_ENDPOINTS`.  The list might now be empty — keep it as `[]` and keep the `TestAuthenticatedStub501` class's parameterize; an empty list means the test generates zero cases, and that's a fine regression guard.
- The `_ENDPOINTS` list (used by `TestUnauthenticated` and `TestAuthenticatedStub501`) still contains the trace endpoint — the 401 test must still pass for it.  Keep it in `_ENDPOINTS`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/router.py` | Modify | Replace stub route with StreamingResponse. |
| `tests/modules/ai/test_routes_control_plane.py` | Modify | Remove trace from `_STUBBED_ENDPOINTS`. |

## Edge Cases & Risks
- `StreamingResponse` doesn't let you raise mid-stream and convert to Problem Details — the response headers have already gone out.  The pre-flight 404 check is the only clean way to distinguish "run doesn't exist" from "run exists, stream is just empty".
- `kind: list[str] | None = None` — FastAPI's repeatable query param parsing can coerce a single `?kind=step` to a one-element list.  The `frozenset(kind) if kind else None` guard handles both the `None` and empty-list cases.
- `since: datetime | None = None` — FastAPI parses ISO-8601 including `Z` suffix and timezone offsets.  The `tail_run_stream` implementation compares against timezone-aware `datetime`s stored on DTOs.  Test with a timezone-aware `since` AND a naive `since` to confirm behavior; prefer aware.
- `X-Accel-Buffering: no` is an nginx-specific hint.  Other proxies ignore it; they also typically don't buffer `application/x-ndjson` for long streams.  Document in the route docstring.

## Acceptance Verification
- [ ] `GET /api/v1/runs/{id}/trace` returns 200 `Content-Type: application/x-ndjson` on a known run.
- [ ] Response has `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- [ ] `?follow=true` accepted; `?since=<iso>` accepted; `?kind=step&kind=policy_call` accepted.
- [ ] Unknown run id → 404 Problem Details (RFC 7807, content-type `application/problem+json`).
- [ ] Adapter-thin check still passes.
- [ ] Endpoint returns 200 + empty body when `TRACE_BACKEND=noop` (via dep override).
