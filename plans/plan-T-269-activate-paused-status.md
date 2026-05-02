# Plan: T-269 — Activate `RunStatus.PAUSED` for human-mode dispatches

> **Task source:** IMP-002 (`docs/work-items/IMP-002-activate-paused-status-for-human-pauses.md`).
> **Workflow:** standard (no mockup, no investigation — IMP-002 is itself the investigation write-up).

---

## Task definition

| Field | Value |
|-------|-------|
| **ID** | T-269 |
| **Title** | Activate `RunStatus.PAUSED` for human-mode dispatches |
| **Type** | Backend / Runtime |
| **Complexity** | S |
| **Workflow** | standard |
| **Description** | When the deterministic runtime parks on a `mode=human` dispatch, flip `Run.status → paused` before awaiting the supervisor future and back to `running` after the future resolves (success / failure / timeout / exception). Engine and remote modes keep `Run.status='running'` — those waits are not human-handoff waits. |
| **Files to Modify** | `src/app/modules/ai/runtime_deterministic.py`, `tests/modules/ai/test_runtime_human_pause.py` (new), `docs/data-model.md` (Run entity status note), `CLAUDE.md` (Runtime Loop section) |
| **Acceptance Criteria** | (1) Live run on `request_implementation` shows `Run.status='paused'` until signal; (2) After signal, status returns to `running` and run advances to `submit_implementation`; (3) Cancel-while-paused terminates correctly; (4) Trace `?follow=true` keeps streaming through a paused interval; (5) Engine/remote dispatches do **not** flip status. |
| **Dependencies** | None (PR #69 already merged). |

---

## Context summary

`runtime_deterministic.py:289-340` is the dispatch lifecycle:

```python
# Mark dispatched
dispatch_row.mark_dispatched(...)
# Invoke executor
envelope = await binding.executor.dispatch(ctx)
# Non-terminal: wait for webhook / signal
if envelope.state == DispatchState.DISPATCHED:
    # ... engine-mode correlation_id stamp ...
    envelope = await asyncio.wait_for(supervisor.await_dispatch(dispatch_id), timeout=timeout)
```

`Run.status` is set to `RUNNING` once at loop entry (`runtime_deterministic.py:556`); never written between then and terminal. `_mark_running` already exists as a helper. `RunStatus.PAUSED` is in `enums.py:13` with zero writers.

`HumanExecutor` returns `DispatchEnvelope(state=DISPATCHED, mode='human')` immediately and is awaited via the same `supervisor.await_dispatch` future as engine and remote modes. The dispatch is delivered by `service.deliver_signal_for_dispatch` when an operator hits `POST /api/v1/runs/{id}/signals`.

`_TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}` lives in `service.py:221`. `paused` is intentionally *not* terminal — that's what makes a paused run still cancellable, still followable, still resumable.

---

## Implementation steps

### Step 1 — Add `_mark_paused` helper next to `_mark_running`

`src/app/modules/ai/runtime_deterministic.py`, after `_mark_running` (around line 558):

```python
async def _mark_paused(
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Flip the run to PAUSED while awaiting an external (human) action.

    Idempotent — terminal runs are not touched (matches ``_terminate``'s
    guard).  Re-entry into the loop after resume calls ``_mark_running``.
    """
    async with session_factory() as session:
        run_row = await session.get(Run, run_id)
        if run_row is None:
            return
        if RunStatus(run_row.status) in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        ):
            return
        run_row.status = RunStatus.PAUSED
        await session.commit()
```

Why: mirrors `_mark_running`'s shape (idempotent, terminal-guard, single-column write). Keeps the helper ladder small and grep-able.

### Step 2 — Wrap the human-mode `await_dispatch` leg with the flip

`src/app/modules/ai/runtime_deterministic.py`, around line 338 (the `if envelope.state == DispatchState.DISPATCHED:` block, just before `await asyncio.wait_for(...)`):

```python
# Non-terminal: webhook will deliver the terminal envelope.
timeout = binding.timeout_seconds if binding.timeout_seconds is not None else float(dispatch_timeout_seconds)
is_human_pause = binding.executor.mode == DispatchMode.HUMAN
if is_human_pause:
    await _mark_paused(run_id, session_factory)
try:
    envelope = await asyncio.wait_for(
        supervisor.await_dispatch(dispatch_id), timeout=timeout
    )
except TimeoutError:
    envelope = _synthesize_failed(
        ctx,
        ref=binding.executor.name,
        mode=binding.executor.mode,
        started=started_at,
        detail=f"timeout after {timeout}s",
    )
    await _mark_dispatch_failed(
        dispatch_id, detail=envelope.detail or "timeout", session_factory=session_factory
    )
finally:
    if is_human_pause:
        # Resume: only flip back if the run isn't already terminal
        # (a concurrent cancel could have landed during the wait).
        await _mark_running(run_id, session_factory)
```

Important details:

- Flip happens **before** `wait_for` registers, but the supervisor future was created in the runtime entry path; resolution that races with the flip gets queued on the future, not lost.
- `finally` covers all three exits (resolve, timeout, exception). `_mark_running` is already idempotent and terminal-guarded — if a cancel landed during the wait, we won't stomp `cancelled` back to `running`.
- The check is `binding.executor.mode == DispatchMode.HUMAN`, **not** `envelope.mode`. The envelope is what the executor returned, which may be wrong-mode if the executor synthesized a failure. The binding is the source of truth for "what kind of wait is this."

### Step 3 — Verify cancel path is paused-aware

Read `src/app/modules/ai/service.py:241` to confirm `cancel_run` only blocks on `_TERMINAL_STATUSES`. Expected: it does (paused is not terminal → cancel proceeds). Add an explicit assertion in the new test (Step 4 case 3) rather than touching `service.py`.

### Step 4 — New test file `tests/modules/ai/test_runtime_human_pause.py`

Four cases, all using `StubLLMProvider` and a real Postgres fixture (per CLAUDE.md testing conventions):

1. **`test_run_flips_to_paused_on_human_dispatch`** — Build a 3-node agent (`start → human_node → done`) registered with a `HumanExecutor`. Start the loop in the background. Poll `Run.status` until it reads `paused` (with a short timeout — the flip happens before `await_dispatch`). Deliver the signal via `supervisor.deliver_dispatch`. Wait for terminal. Assert: status sequence observed was `running → paused → running → completed`.

2. **`test_engine_mode_does_not_flip_to_paused`** — Same shape but the awaited node is bound to a stub engine executor (mock that returns `dispatched` without HTTP). Assert: `Run.status` is `running` while parked, never `paused`.

3. **`test_cancel_while_paused_terminates_run`** — Start the loop; wait for `paused`; call `cancel_run`; assert status flips to `cancelled` and `_TERMINAL_STATUSES` reasoning holds (no orphan dispatch row in `dispatched` state — should be `cancelled`).

4. **`test_timeout_in_paused_state_resumes_status`** — Use a very short `dispatch_timeout_seconds`; never deliver the signal; assert: after timeout, `Run.status` lands at `failed` (via `_terminate`'s `error` mapping), **not** stuck at `paused`. Verifies the `finally` flip-back works on the timeout path.

(Skipping a fifth "trace stream stays open during paused" test — that's covered by existing trace-stream coverage; `paused` is non-terminal and the stream's terminal check is enum-keyed.)

### Step 5 — Doc updates

- `docs/data-model.md` Run entity: add a sentence under `status` noting `paused` is now a live mid-run value, set by the runtime when awaiting a `mode=human` dispatch. Add changelog entry per CLAUDE.md doc discipline.
- `CLAUDE.md` Runtime Loop section: add a one-line bullet to the existing invariant list — "Run.status flips to `paused` while parked on a `mode=human` dispatch and back to `running` on resume; engine and remote waits stay `running`."
- No `api-spec.md` change needed (RunDto already exposes `status`; the value space documentation is in data-model).

---

## Out of scope (explicit non-goals)

- **Mode-aware `dispatch_timeout_seconds`.** Today every mode uses the same default (600 s). Human pauses legitimately last hours/days. Activating `paused` is a precondition for thinking honestly about a longer (or no) timeout for human-mode dispatches; that policy change is a follow-on IMP, not this task.
- **Outbound notification effector for paused runs.** The status flag is the *read* side. A push channel ("hey, this run needs a human") is a separate FEAT.
- **`status='paused'` on remote-executor waits.** Remote callbacks come from code we control; the wait is not "blocked on external party." Treat them like engine waits — no flip.
- **Schema migration / new column.** `Run.status` is already free-text validated at the application layer via the `RunStatus` StrEnum. No Alembic revision.

---

## Verification

- `uv run pytest tests/modules/ai/test_runtime_human_pause.py` — new suite green.
- `uv run pytest` — full suite green; `test_runtime_deterministic_is_pure` still passes (no new imports of `core.llm` or executor handler modules in `runtime_deterministic.py`).
- `uv run pyright` and `uv run ruff check .` clean.
- Live re-run: start `lifecycle-agent@0.3.0`, advance to `request_implementation`, observe `GET /api/v1/runs/{id}` returns `status='paused'`, send signal, observe status returns to `running` and run advances to `submit_implementation`.

---

## Rollback

Single-file revert of `runtime_deterministic.py`. Test file deletion. Doc reverts. No data shape to undo. Rows that ended up `status='paused'` mid-revert are normalized by `_mark_running` at the next loop entry (the existing run-resume guard already forces RUNNING on entry).

---

## Notes for the implementer

- Don't move the flip into `HumanExecutor.dispatch()` — executors are passive descriptors per CLAUDE.md ("Service layer owns logic"). The runtime is the single writer of `Run.status`.
- `binding.executor.mode` (a `ClassVar`) is the right discriminator; do not introspect by `isinstance(binding.executor, HumanExecutor)` — it's brittle if a second human executor ever appears.
- Keep the flip narrow — only the human-mode branch. Engine wakes happen on millisecond-to-second timescales; flipping to `paused` for those would be misleading.
