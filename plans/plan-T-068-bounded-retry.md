# Implementation Plan: T-068 — Bounded retry with backoff + jitter

## Task Reference
- **Task ID:** T-068
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-067

## Overview
Wrap the API call in a retry loop: up to 3 attempts total, exponential backoff 500 ms → 1 s → 4 s capped, ±50 ms jitter. Retry only on transient errors (5xx, 429, connection/timeout); never retry on non-transient 4xx. The final `Usage.latency_ms` reflects cumulative wall-clock.

## Steps

### 1. Modify `src/app/core/llm_anthropic.py`
- Add module-level constants:
  ```python
  _MAX_ATTEMPTS = 3
  _BACKOFF_BASE_SECONDS = 0.5
  _BACKOFF_CAP_SECONDS = 4.0
  _JITTER_SECONDS = 0.05
  ```
- Add module-level pure helper `def _is_transient(exc: Exception) -> bool`:
  ```python
  if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
      return True
  if isinstance(exc, anthropic.APIStatusError):
      return exc.status_code == 429 or exc.status_code >= 500
  return False
  ```
- Add a `_rng` attribute on the provider (default `random.Random()` — tests can override via `provider._rng = random.Random(seed)` for determinism).
- Refactor `chat_with_tools` so the `try/except` from T-067 is inside a retry loop:
  ```python
  cumulative_latency_ms = 0
  last_exc: Exception | None = None
  for attempt in range(_MAX_ATTEMPTS):
      started = time.perf_counter()
      try:
          response = await self._client.messages.create(...)
          cumulative_latency_ms += int((time.perf_counter() - started) * 1000)
          break
      except (anthropic.APIStatusError, anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
          cumulative_latency_ms += int((time.perf_counter() - started) * 1000)
          last_exc = exc
          if not _is_transient(exc) or attempt == _MAX_ATTEMPTS - 1:
              # Non-transient or final attempt: map to the typed exception.
              self._raise_mapped(exc)
          backoff = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))
          jitter = self._rng.uniform(-_JITTER_SECONDS, _JITTER_SECONDS)
          logger.warning(
              "anthropic retry",
              extra={"attempt": attempt + 1, "backoff_s": backoff + jitter, "request_id": _request_id(exc) if isinstance(exc, anthropic.APIStatusError) else None},
          )
          await asyncio.sleep(max(0.0, backoff + jitter))
  else:
      # Unreachable — the loop either breaks on success or raises on non-transient / last attempt.
      assert last_exc is not None
      self._raise_mapped(last_exc)
  ```
- Move the T-067 error-mapping logic into a private method `_raise_mapped(self, exc)` that raises the right `ProviderError` and re-raises from `exc`.
- Downstream (after the loop breaks on success): the existing response-parsing logic runs, using `cumulative_latency_ms` for `Usage.latency_ms` instead of a single-shot measurement.
- Imports: `import asyncio`, `import logging`, `import random`, `import time`. `logger = logging.getLogger(__name__)` if not already present.

### 2. Create `tests/modules/core/test_llm_anthropic_retries.py`
- Seed the provider's RNG: `provider._rng = random.Random(42)` before every test so jitter is deterministic.
- Tests (all under `respx.mock(base_url="https://api.anthropic.com")`):
  - `test_three_consecutive_5xx_exhaust_retries_and_raise` — mock 3× 500 in sequence (use `side_effect=[resp1, resp2, resp3]` or manual call counting). Assert `ProviderError` raised, `route.call_count == 3`.
  - `test_retry_succeeds_on_second_attempt` — 500 then 200. Assert `ToolCall` returned, `route.call_count == 2`, `result.usage.latency_ms > 0` and roughly equals first-attempt-time + backoff + second-attempt-time.
  - `test_400_does_not_retry` — one 400 response. Assert `ProviderError`, `route.call_count == 1`.
  - `test_401_does_not_retry` — same, 401.
  - `test_429_retries` — 429 then 200. Assert `route.call_count == 2`.
  - `test_connection_error_retries` — first call raises `httpx.ConnectError` (via respx `side_effect`), second returns 200. Assert `route.call_count == 2`.
  - `test_warning_logged_on_each_retry` — use `caplog.at_level(logging.WARNING)`; after 2 retries then success, assert exactly 2 WARNING records with message `"anthropic retry"`.
  - `test_total_backoff_bounded` — 3× 500 failure. Measure `time.perf_counter()` around the call. Assert total elapsed < 6.5s (budget: 0.5 + 1.0 + ~0 retries = 1.5s max jitter; generous CI bound).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/llm_anthropic.py` | Modify | Retry loop + helper extraction. |
| `tests/modules/core/test_llm_anthropic_retries.py` | Create | 8 retry tests. |

## Edge Cases & Risks
- If `_is_transient` returns False, we MUST still raise a mapped `ProviderError` (not the raw SDK exception). The `_raise_mapped` helper handles that.
- Jitter can go negative; `max(0.0, backoff + jitter)` prevents a negative sleep.
- `asyncio.sleep(0.0)` is legal but wasteful — the `max` guard keeps it at 0 only in pathological jitter cases.
- The cumulative-latency assertion in T-068's "retry succeeds on second attempt" test is loose on purpose; exact timing is flaky in CI.

## Acceptance Verification
- [ ] Three 5xx failures raise `ProviderError` after exactly 3 attempts.
- [ ] A 5xx followed by 200 succeeds on attempt 2; `latency_ms` cumulative.
- [ ] 400/401/403 do not retry.
- [ ] 429 is retried.
- [ ] Connection / timeout is retried.
- [ ] Total backoff for a 3-failure run stays under ~6.5s (budget 0.5 + 1.0 = 1.5s sleeps + API time).
- [ ] WARNING log written on every retry with `attempt` + `backoff_s` + `request_id` (when applicable).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
