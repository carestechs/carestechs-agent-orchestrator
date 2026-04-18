# Implementation Plan: T-098 — `POST /api/v1/runs/{id}/signals` endpoint + service

## Task Reference
- **Task ID:** T-098
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-088, T-096

## Overview
The operator-signal ingress. Body `{name, task_id, payload?}`. Persists a `RunSignal` row via the repository (idempotent on `dedupe_key`), then calls `supervisor.deliver_signal(...)`. Order is persist-first, wake-second — matches the webhook pipeline. Auth via existing `X-API-Key` dependency. Extends the JSONL trace with a new `operator_signal` kind.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
Confirm `SignalCreateRequest` and `RunSignalDto` exist from T-088. Add the envelope response:
```python
class SignalCreateResponse(BaseModel):
    model_config = _CAMEL_CONFIG
    data: RunSignalDto
    meta: dict[str, Any] | None = None
```

### 2. Modify `src/app/modules/ai/service.py`
Add service function:
```python
async def send_signal(
    *, run_id: UUID, name: str, task_id: str, payload: dict[str, Any],
    db: AsyncSession, supervisor: RunSupervisor, trace: TraceStore,
) -> tuple[RunSignalDto, bool]:
    run = await repository.get_run_by_id(db, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")
    if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        raise ConflictError(f"run already terminal: {run.status}")
    memory_row = await repository.get_run_memory(db, run_id)
    memory = LifecycleMemory.from_run_memory(memory_row.data if memory_row else {})
    if not any(t.id == task_id for t in memory.tasks):
        raise NotFoundError(f"task not found in run: {task_id}")

    dedupe_key = hashlib.sha256(f"{run_id}:{name}:{task_id}".encode()).hexdigest()
    row, created = await repository.create_run_signal(
        db, run_id=run_id, name=name, task_id=task_id,
        payload=payload, dedupe_key=dedupe_key,
    )
    await db.commit()

    dto = RunSignalDto.model_validate(row)
    if created:
        await trace.record_operator_signal(dto)
        await supervisor.deliver_signal(run_id, name, task_id, payload)
    return dto, created
```

### 3. Modify `src/app/modules/ai/trace.py`
Add `record_operator_signal(signal: RunSignalDto) -> None` to the `TraceStore` protocol. `NoopTraceStore` implementation yields nothing (no-op).

### 4. Modify `src/app/modules/ai/trace_jsonl.py`
Implement `record_operator_signal`:
```python
async def record_operator_signal(self, signal: RunSignalDto) -> None:
    line = {"kind": "operator_signal", "data": signal.model_dump(mode="json", by_alias=True)}
    await self._append_line(signal.run_id, line)
```
Extend `_DTO_BY_KIND` (used by `open_run_stream` / `tail_run_stream`): add `"operator_signal": RunSignalDto`.

### 5. Modify `src/app/modules/ai/router.py`
Add the route:
```python
@router.post("/runs/{run_id}/signals",
             response_model=SignalCreateResponse,
             status_code=status.HTTP_202_ACCEPTED)
async def post_signal(
    run_id: UUID,
    body: SignalCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    supervisor: RunSupervisor = Depends(get_supervisor),
    trace: TraceStore = Depends(get_trace_store),
) -> SignalCreateResponse:
    dto, created = await service.send_signal(
        run_id=run_id, name=body.name, task_id=body.task_id,
        payload=body.payload, db=db, supervisor=supervisor, trace=trace,
    )
    meta = None if created else {"alreadyReceived": True}
    return SignalCreateResponse(data=dto, meta=meta)
```

Because `SignalCreateRequest.name` is a `Literal["implementation-complete"]`, an unknown `name` yields FastAPI's automatic 422 — upgrade this to our 400 Problem Details pattern via a validator if the 422 shape is inconsistent.

### 6. Modify `docs/api-spec.md`
Draft the endpoint section under `ai` module's Runs group, referencing `RunSignalDto`. Defer the changelog entry to T-106.

### 7. Tests
- `tests/modules/ai/test_routes_signals.py` — 5 cases: happy → 202 + supervisor woken (spy); duplicate → 202 + `alreadyReceived=true` + supervisor NOT re-woken; unknown `name` → 400; unknown run → 404; unknown task → 404; run terminal → 409.
- `tests/integration/test_signal_flow.py` — 1 end-to-end case: stub-policy run hits `implementation`, test POSTs to `/signals`, run advances to `review`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | `SignalCreateResponse`. |
| `src/app/modules/ai/service.py` | Modify | `send_signal` service function. |
| `src/app/modules/ai/trace.py` | Modify | `record_operator_signal` on protocol + noop. |
| `src/app/modules/ai/trace_jsonl.py` | Modify | JSONL impl + `_DTO_BY_KIND` extension. |
| `src/app/modules/ai/router.py` | Modify | `POST /runs/{id}/signals` route. |
| `docs/api-spec.md` | Modify | Endpoint draft (changelog in T-106). |
| `tests/modules/ai/test_routes_signals.py` | Create | 5 route tests. |
| `tests/integration/test_signal_flow.py` | Create | End-to-end signal delivery test. |

## Edge Cases & Risks
- **409 persists the signal**: we still write the row before returning 409 — keeps the "persist before refuse" invariant. Only the supervisor wake is skipped.
- **Trace append on duplicate**: idempotent signals do NOT re-append a trace line — the first delivery already created one. Guarded by `if created:`.
- **Read-modify-write race on `RunMemory`**: the task-existence check reads memory outside the write path of the runtime loop. A race where the runtime mid-writes memory while the endpoint reads is harmless — memory is append-only during a run (tasks list only grows in v1).
- **Trace-kind enum drift**: readers filtering by `--kind operator_signal` in `orchestrator runs trace` must recognize the new value. T-106's adapter-thin extension should also add a `_KNOWN_TRACE_KINDS` guard if the project maintains one.
- **Dedupe key determinism**: hashing `f"{run_id}:{name}:{task_id}"` — the same string across every caller produces the same key. Unit-test the hash helper to prevent accidental format drift.

## Acceptance Verification
- [ ] `POST /runs/{id}/signals` registered; returns 202 with envelope.
- [ ] Unknown `name` → 400 Problem Details.
- [ ] Unknown run → 404; unknown task → 404; terminal run → 409.
- [ ] Idempotent second call → 202 + `alreadyReceived=true` + no re-wake.
- [ ] JSONL trace appends `{"kind": "operator_signal", "data": ...}` on first delivery only.
- [ ] 6 route tests + 1 integration test pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
