# Implementation Plan: T-040 — `start_run` non-blocking with supervisor spawn

## Task Reference
- **Task ID:** T-040
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-039, T-032

## Overview
Replace the `NotImplementedYet` body with a real non-blocking start: validate inputs, write `Run` + `RunMemory`, commit, spawn the supervised loop task, return the summary DTO. AD-2 compliance: no await on the loop before returning.

## Steps

### 1. Modify `src/app/modules/ai/service.py`
- Import `agents`, `runtime.run_loop`, `supervisor`, `policy` factory, `engine_client`, `trace_store` factory.
- Replace `start_run(request, db)` body:
  1. `agent = agents.load_agent(request.agent_ref)` — raises `NotFoundError` if missing.
  2. Validate `request.intake` against `agent.intake_schema` using `jsonschema`; raises `ValidationError` with per-field errors.
  3. Build `Run` row: `id=generate_uuid7()`, `agent_ref=request.agent_ref`, `agent_definition_hash=agent.agent_definition_hash`, `intake=request.intake`, `status=RunStatus.PENDING`, `started_at=now_utc()`, `trace_uri=f"file://.trace/{run.id}.jsonl"`.
  4. Build `RunMemory(run_id=run.id, data={})`.
  5. `db.add_all([run, memory])`; `await db.commit()`; `await db.refresh(run)`.
  6. `trace_store` is initialized implicitly by `run_loop` (no-op for now).
  7. Supervisor spawn: `supervisor.spawn(run.id, lambda event: run_loop(run_id=run.id, agent=agent, policy=..., engine=..., trace=..., supervisor=supervisor, session_factory=..., cancel_event=event))`.
  8. Return `RunSummaryDto.model_validate(run, from_attributes=True)`.
- The service takes new dependencies: `supervisor`, `session_factory`, `policy`, `engine`, `trace_store`. Extend the function signature to accept them — they're injected at the route layer.

### 2. Modify `src/app/modules/ai/router.py`
- `create_run` route now injects all five deps via `Depends(...)` and forwards them to `service.start_run`.

### 3. Modify `src/app/core/dependencies.py`
- Expose a `get_session_factory()` dep returning the module-level `async_sessionmaker` bound to the singleton engine.

### 4. Create `tests/modules/ai/test_service_start_run.py`
- Happy path: 202-equivalent — writes Run + RunMemory rows; returns DTO; no exception.
- Unknown agent → `NotFoundError` BEFORE any row written (assert `count(Run) == 0`).
- Intake validation failure → `ValidationError` with per-field errors, no rows.
- Supervisor is called with `run.id` (use a `FakeSupervisor` recording `spawn(run_id, ...)`).
- Timing: test returns within 50 ms on local DB (with asserted generous CI bound).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/service.py` | Modify | Real `start_run`. |
| `src/app/modules/ai/router.py` | Modify | Inject supervisor + policy + engine + trace store + session factory deps. |
| `src/app/core/dependencies.py` | Modify | `get_session_factory`. |
| `tests/modules/ai/test_service_start_run.py` | Create | Happy + edge cases. |

## Edge Cases & Risks
- `ValidationError` must run BEFORE the Run row is inserted — enforce by ordering (validate first, insert second).
- Supervisor spawn must not `await` the coroutine — `asyncio.create_task` lives inside `spawn`; the route returns immediately after.
- On supervisor spawn failure (very unlikely — task creation error), the Run row is orphaned. Acceptable trade-off; zombie reconciliation (T-045) cleans it up on next restart.

## Acceptance Verification
- [ ] Returns within 50 ms (generous 200 ms bound in CI).
- [ ] Unknown ref → `NotFoundError`, no rows.
- [ ] Invalid intake → `ValidationError`, no rows.
- [ ] Run + RunMemory rows exist after success.
- [ ] Supervisor `spawn` called with the new run id.
