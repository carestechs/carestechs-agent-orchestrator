# Implementation Plan: T-077 — Extend `TraceStore` protocol with `tail_run_stream`

## Task Reference
- **Task ID:** T-077
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Grow the `TraceStore` protocol by one method — `tail_run_stream` — so the streaming endpoint has a richer reader than the one-shot `open_run_stream` provides.  Ship `NoopTraceStore.tail_run_stream` as a zero-yield async generator in the same commit so the protocol is implementable from day one.

## Steps

### 1. Modify `src/app/modules/ai/trace.py`
- Add `datetime` to the top-level imports (for the `since` parameter type).
- Add method to `TraceStore` protocol:
  ```python
  async def tail_run_stream(
      self,
      run_id: uuid.UUID,
      *,
      follow: bool = False,
      since: datetime | None = None,
      kinds: frozenset[str] | None = None,
  ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]:
      """Richer reader for the streaming endpoint.

      Non-follow mode yields every committed record once and closes.
      Follow mode keeps polling for new records until the caller breaks
      out of ``async for``.  ``kinds=None`` (or empty) means "all kinds";
      ``since=None`` means "no lower bound".
      """
      ...
  ```
- Add `NoopTraceStore.tail_run_stream`:
  ```python
  async def tail_run_stream(
      self,
      run_id: uuid.UUID,
      *,
      follow: bool = False,
      since: datetime | None = None,
      kinds: frozenset[str] | None = None,
  ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]:
      """Yield nothing regardless of args — noop backend has no data."""
      return _empty_async_iterator()
  ```
  The existing `_empty_async_iterator()` helper is already in the module.
- Leave `open_run_stream` unchanged.

### 2. Modify `tests/modules/ai/test_trace_noop.py`
- Add one test class `TestTailRunStream`:
  - `test_tail_yields_nothing_without_follow` — `async for` the iterator, assert zero items.
  - `test_tail_yields_nothing_with_follow` — same but `follow=True`. Must return immediately, not hang.
  - `test_tail_respects_kinds_and_since_without_effect` — call with specific filters, still zero items.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/trace.py` | Modify | Protocol method + Noop impl. |
| `tests/modules/ai/test_trace_noop.py` | Modify | 3 tail tests. |

## Edge Cases & Risks
- The `NoopTraceStore.tail_run_stream(follow=True)` MUST NOT hang; returning the empty iterator is the whole point.  An accidental `while True: await asyncio.sleep(...)` here would make the noop-backend route (see T-080) hang forever.
- Keep the Protocol method's default arguments matching the feature brief verbatim — T-078, T-079, and T-080 all rely on the signature being stable.

## Acceptance Verification
- [ ] `TraceStore.tail_run_stream` declared with the exact signature from the feature brief.
- [ ] `NoopTraceStore.tail_run_stream` is an async iterator that yields nothing.
- [ ] `isinstance(NoopTraceStore(), TraceStore)` is still True.
- [ ] New noop tests pass.
- [ ] `uv run pyright src/` + `uv run ruff check .` clean.
