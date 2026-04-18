# Plan — T-018 Trace store protocol + no-op implementation

## Objective

Define a `TraceStore` Protocol in `modules/ai/trace.py` with typed methods for recording steps, policy calls, webhook events, and streaming trace data. Ship a `NoopTraceStore` as the default implementation and wire it as a FastAPI dependency with a test-override path.

## Steps

### 1. Create `src/app/modules/ai/trace.py`

- Define `TraceStore` as a `typing.Protocol` (runtime-checkable) with:
  - `async record_step(run_id: uuid.UUID, step: StepDto) -> None`
  - `async record_policy_call(run_id: uuid.UUID, call: PolicyCallDto) -> None`
  - `async record_webhook_event(run_id: uuid.UUID, event: WebhookEventDto) -> None`
  - `async open_run_stream(run_id: uuid.UUID) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]`
- Define `NoopTraceStore` implementing the protocol (all methods are no-ops; `open_run_stream` yields nothing).
- Define `get_trace_store()` factory function suitable for FastAPI `Depends()`. Returns `NoopTraceStore()` by default.

### 2. Create `tests/modules/ai/test_trace_noop.py`

- Test `NoopTraceStore.record_step` accepts a call without error.
- Test `NoopTraceStore.record_policy_call` accepts a call without error.
- Test `NoopTraceStore.record_webhook_event` accepts a call without error.
- Test `NoopTraceStore.open_run_stream` returns an empty async iterator.
- Test `get_trace_store()` returns a `NoopTraceStore` instance.
- Test that `NoopTraceStore` satisfies `isinstance(..., TraceStore)` (runtime-checkable).

### 3. Verify

- `uv run pytest tests/modules/ai/test_trace_noop.py -v`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pyright`
