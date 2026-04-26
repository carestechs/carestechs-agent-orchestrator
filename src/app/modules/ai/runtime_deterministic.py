"""Deterministic-policy runtime loop (FEAT-009 / T-220).

Runs the four-step loop for agents declaring ``flow.policy: deterministic``:

1. **Resolve.** ``FlowResolver.resolve_next`` picks the next node from
   ``(declaration, current_node, memory_snapshot, last_dispatch_result)``.
   No LLM call.
2. **Dispatch.** ``ExecutorRegistry.resolve(agent_ref, node_name)`` returns
   a binding; the runtime persists a ``Dispatch`` row, registers the
   future with the supervisor, and calls ``executor.dispatch``.
3. **Wait.** If the executor returned a non-terminal envelope (remote /
   human modes), the loop awaits ``supervisor.await_dispatch`` with the
   per-binding timeout.  Local executors return terminal envelopes and
   skip the wait.
4. **Record + advance.** Step status is derived from the envelope; the
   trace gets an ``executor_call`` entry; memory is updated from the
   envelope's ``result`` (specifically ``__memory_patch`` if present);
   the loop continues with the new ``current_node``.

This module imports neither ``app.core.llm`` nor any executor handler
module — verified at boot by the structural test in T-228.  LLM access
is an executor-internal concern.

The LLM-policy path (``runtime.py``) is untouched.  ``run_loop`` in
``service.start_run`` selects between the two paths based on
``agent.flow.policy``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai import flow_resolver
from app.modules.ai.enums import (
    DispatchOutcome,
    DispatchState,
    RunStatus,
    StepStatus,
    StopReason,
)
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.flow_resolver import NextNode, TerminalSentinel
from app.modules.ai.models import Dispatch, Run, RunMemory, Step, generate_uuid7
from app.modules.ai.schemas import DispatchEnvelope, ExecutorCallDto, StepDto

if TYPE_CHECKING:
    from app.modules.ai.agents import AgentDefinition
    from app.modules.ai.executors.registry import ExecutorRegistry
    from app.modules.ai.supervisor import RunSupervisor
    from app.modules.ai.trace import TraceStore

logger = logging.getLogger(__name__)


_MEMORY_NS = "__feat009"
"""Reserved key inside ``RunMemory.data`` for runtime bookkeeping.

Holds ``current_node`` and ``last_dispatch_result``.  Anything outside
this key is the agent's own scratchpad and is preserved untouched.
"""


async def run_deterministic_loop(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    trace: TraceStore,
    supervisor: RunSupervisor,
    registry: ExecutorRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    cancel_event: asyncio.Event,
    dispatch_timeout_seconds: int,
) -> None:
    """Run the deterministic-policy loop until a stop condition fires."""
    try:
        await _mark_running(run_id, session_factory)
        step_count = 0
        max_steps = agent.default_budget.max_steps or 0

        while True:
            if cancel_event.is_set() or supervisor.is_cancelled(run_id):
                await _terminate(run_id, session_factory, StopReason.CANCELLED)
                return

            if max_steps and step_count >= max_steps:
                await _terminate(run_id, session_factory, StopReason.BUDGET_EXCEEDED)
                return

            memory_snapshot, last_result, current_node = await _read_state(run_id, agent, session_factory)

            decision = flow_resolver.resolve_next(
                _agent_to_declaration(agent),
                current_node,
                memory_snapshot,
                last_result,
            )

            if isinstance(decision, TerminalSentinel):
                stop_reason = StopReason.DONE_NODE if decision.reason == "done_node" else StopReason.POLICY_TERMINATED
                await _terminate(run_id, session_factory, stop_reason)
                return

            assert isinstance(decision, NextNode)
            await _execute_node(
                run_id=run_id,
                agent=agent,
                node_name=decision.name,
                step_number=step_count + 1,
                trace=trace,
                supervisor=supervisor,
                registry=registry,
                session_factory=session_factory,
                dispatch_timeout_seconds=dispatch_timeout_seconds,
            )
            step_count += 1

    except asyncio.CancelledError:
        await _terminate(run_id, session_factory, StopReason.CANCELLED)
        raise
    except Exception as exc:
        logger.exception("deterministic runtime crashed for run %s", run_id)
        await _terminate(
            run_id,
            session_factory,
            StopReason.ERROR,
            final_state={"crash": str(exc)},
        )


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


async def _read_state(
    run_id: uuid.UUID,
    agent: AgentDefinition,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Return ``(memory_snapshot, last_dispatch_result, current_node)``."""
    async with session_factory() as session:
        memory_row = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
    data: dict[str, Any] = (memory_row.data if memory_row is not None else {}) or {}
    bookkeeping: dict[str, Any] = data.get(_MEMORY_NS) or {}
    current_node: str = bookkeeping.get("current_node") or agent.flow.entry_node
    last_result: dict[str, Any] | None = bookkeeping.get("last_dispatch_result")

    snapshot: dict[str, Any] = {k: v for k, v in data.items() if k != _MEMORY_NS}
    return snapshot, last_result, current_node


async def _write_state(
    run_id: uuid.UUID,
    *,
    current_node: str,
    last_dispatch_result: dict[str, Any] | None,
    memory_patch: dict[str, Any] | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        memory_row = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
        data: dict[str, Any] = ((memory_row.data if memory_row is not None else {}) or {}).copy()
        bookkeeping: dict[str, Any] = data.get(_MEMORY_NS) or {}
        bookkeeping["current_node"] = current_node
        bookkeeping["last_dispatch_result"] = last_dispatch_result
        data[_MEMORY_NS] = bookkeeping
        if memory_patch:
            for key, value in memory_patch.items():
                if not key.startswith("_"):
                    data[key] = value
        if memory_row is None:
            session.add(RunMemory(run_id=run_id, data=data))
        else:
            memory_row.data = data
        await session.commit()


# ---------------------------------------------------------------------------
# Per-iteration body
# ---------------------------------------------------------------------------


async def _execute_node(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    node_name: str,
    step_number: int,
    trace: TraceStore,
    supervisor: RunSupervisor,
    registry: ExecutorRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    dispatch_timeout_seconds: int,
) -> None:
    binding = registry.resolve(agent.ref, node_name)

    step_id = generate_uuid7()
    dispatch_id = generate_uuid7()
    started_at = datetime.now(UTC)

    intake = _build_node_intake(agent, run_id, node_name)

    async with session_factory() as session:
        step = Step(
            id=step_id,
            run_id=run_id,
            step_number=step_number,
            node_name=node_name,
            node_inputs=intake,
            status=StepStatus.PENDING,
        )
        session.add(step)
        # Flush Step first so the FK from Dispatch.step_id resolves
        # before the SQLAlchemy unit-of-work emits the dispatches INSERT.
        await session.flush()
        dispatch = Dispatch(
            dispatch_id=dispatch_id,
            step_id=step_id,
            run_id=run_id,
            executor_ref=binding.executor.name,
            mode=binding.executor.mode,
            state=DispatchState.PENDING,
            intake=intake,
        )
        session.add(dispatch)
        await session.commit()

    supervisor.register_dispatch(run_id, dispatch_id)

    ctx = DispatchContext(
        dispatch_id=dispatch_id,
        run_id=run_id,
        step_id=step_id,
        agent_ref=agent.ref,
        node_name=node_name,
        intake=intake,
        extras=binding.extras,
    )

    # Mark dispatched + invoke. Local executors return terminal directly;
    # remote/human return a non-terminal envelope and the result arrives
    # via /hooks/executors/{id} or /signals.
    async with session_factory() as session:
        dispatch_row = await session.get(Dispatch, dispatch_id)
        assert dispatch_row is not None
        dispatch_row.mark_dispatched(at=datetime.now(UTC))
        await session.commit()

    try:
        envelope = await binding.executor.dispatch(ctx)
    except Exception as exc:
        envelope = _synthesize_failed(
            ctx,
            ref=binding.executor.name,
            mode=binding.executor.mode,
            started=started_at,
            detail=f"executor.dispatch raised: {type(exc).__name__}: {exc}",
        )

    if envelope.state == DispatchState.DISPATCHED:
        # Non-terminal: webhook will deliver the terminal envelope.
        timeout = binding.timeout_seconds if binding.timeout_seconds is not None else float(dispatch_timeout_seconds)
        try:
            envelope = await asyncio.wait_for(supervisor.await_dispatch(dispatch_id), timeout=timeout)
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

    # Record terminal state on the persisted Dispatch + Step rows.
    await _commit_terminal(
        dispatch_id=dispatch_id,
        step_id=step_id,
        envelope=envelope,
        session_factory=session_factory,
    )

    # Trace.
    try:
        await trace.record_executor_call(
            run_id,
            ExecutorCallDto(
                dispatch_id=dispatch_id,
                run_id=run_id,
                executor_ref=binding.executor.name,
                mode=binding.executor.mode,  # type: ignore[arg-type]
                started_at=started_at,
                finished_at=envelope.finished_at,
                outcome=envelope.outcome,
                detail=envelope.detail,
            ),
        )
        await trace.record_step(
            run_id,
            StepDto(
                id=step_id,
                step_number=step_number,
                node_name=node_name,
                status=StepStatus(_step_status_from(envelope)),
                node_inputs=intake,
                node_result=envelope.result,
                dispatched_at=started_at,
                completed_at=envelope.finished_at,
            ),
        )
    except Exception:
        logger.warning("trace write failed for dispatch %s", dispatch_id, exc_info=True)

    # Update memory bookkeeping for the next resolver call.
    memory_patch: dict[str, Any] | None = None
    if envelope.result is not None:
        # Convention: a result key named ``__memory_patch`` is merged into
        # RunMemory.data; everything else is preserved on the dispatch row
        # for the resolver's expression evaluation.
        patch = envelope.result.get("__memory_patch")
        if isinstance(patch, dict):
            memory_patch = dict(patch)  # type: ignore[arg-type]

    await _write_state(
        run_id,
        current_node=node_name,
        last_dispatch_result=dict(envelope.result) if envelope.result else None,
        memory_patch=memory_patch,
        session_factory=session_factory,
    )

    if envelope.outcome == DispatchOutcome.ERROR:
        # Surface the error to the loop; the next iteration's resolver
        # call will see the failed dispatch and may short-circuit via the
        # executor-terminal flag, otherwise we stop here on safety.
        raise _ExecutorFailure(envelope.detail or f"executor {binding.executor.name} failed")


class _ExecutorFailure(RuntimeError):
    """Internal sentinel: an executor returned a failed envelope."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_to_declaration(agent: AgentDefinition) -> dict[str, Any]:
    """Serialize the agent's flow + terminalNodes into the resolver shape."""
    return {
        "terminalNodes": list(agent.terminal_nodes),
        "flow": {
            "entryNode": agent.flow.entry_node,
            "transitions": dict(agent.flow.transitions),
        },
    }


def _build_node_intake(agent: AgentDefinition, run_id: uuid.UUID, node_name: str) -> dict[str, Any]:
    """Construct the ``intake`` payload handed to the executor.

    For now this is the run's intake plus the node name and run id;
    executor-specific shape is the executor's contract.
    """
    return {
        "runId": str(run_id),
        "nodeName": node_name,
        # Intake from the agent definition is delivered separately via
        # extras; node intake here is the run-time variant. Concrete
        # v0.2.0 executors (T-223) tighten this contract.
    }


def _synthesize_failed(
    ctx: DispatchContext,
    *,
    ref: str,
    mode: str,
    started: datetime,
    detail: str,
) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=ctx.dispatch_id,
        step_id=ctx.step_id,
        run_id=ctx.run_id,
        executor_ref=ref,
        mode=mode,  # type: ignore[arg-type]
        state="failed",  # type: ignore[arg-type]
        intake=dict(ctx.intake),
        outcome="error",  # type: ignore[arg-type]
        detail=detail,
        started_at=started,
        finished_at=datetime.now(UTC),
    )


def _step_status_from(envelope: DispatchEnvelope) -> str:
    if envelope.state == DispatchState.COMPLETED:
        return StepStatus.COMPLETED.value
    if envelope.state == DispatchState.FAILED:
        return StepStatus.FAILED.value
    return StepStatus.IN_PROGRESS.value


async def _commit_terminal(
    *,
    dispatch_id: uuid.UUID,
    step_id: uuid.UUID,
    envelope: DispatchEnvelope,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist the terminal envelope onto the Dispatch + Step rows.

    The webhook route already terminalizes the Dispatch row when remote
    executors deliver via ``/hooks/executors/{id}``.  This helper re-checks
    the row and only mutates it if it is still non-terminal — avoids the
    illegal-transition error on the second hand-off.
    """
    async with session_factory() as session:
        dispatch_row = await session.get(Dispatch, dispatch_id)
        if dispatch_row is None:  # pragma: no cover — created in same loop
            return
        if dispatch_row.state in (
            DispatchState.PENDING,
            DispatchState.DISPATCHED,
        ):
            now = datetime.now(UTC)
            if dispatch_row.state == DispatchState.PENDING:
                dispatch_row.mark_dispatched(at=now)
            if envelope.outcome == DispatchOutcome.OK:
                dispatch_row.mark_completed(at=now, result=envelope.result, detail=envelope.detail)
            else:
                dispatch_row.mark_failed(at=now, result=envelope.result, detail=envelope.detail)

        step_row = await session.get(Step, step_id)
        if step_row is not None:
            step_row.status = _step_status_from(envelope)
            step_row.node_result = envelope.result
            step_row.dispatched_at = envelope.started_at
            step_row.completed_at = envelope.finished_at
        await session.commit()


async def _mark_dispatch_failed(
    dispatch_id: uuid.UUID,
    *,
    detail: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        dispatch_row = await session.get(Dispatch, dispatch_id)
        if dispatch_row is None or dispatch_row.state in (
            DispatchState.COMPLETED,
            DispatchState.FAILED,
            DispatchState.CANCELLED,
        ):
            return
        dispatch_row.mark_failed(at=datetime.now(UTC), detail=detail)
        await session.commit()


async def _mark_running(
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run_row = await session.get(Run, run_id)
        if run_row is None:
            return
        run_row.status = RunStatus.RUNNING
        await session.commit()


async def _terminate(
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    reason: StopReason,
    *,
    final_state: dict[str, Any] | None = None,
) -> None:
    """Flip the run to its terminal status.  Idempotent."""
    async with session_factory() as session:
        run_row = await session.get(Run, run_id)
        if run_row is None or RunStatus(run_row.status) in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        ):
            return
        run_row.stop_reason = reason
        if reason in (StopReason.DONE_NODE, StopReason.POLICY_TERMINATED):
            run_row.status = RunStatus.COMPLETED
        elif reason == StopReason.CANCELLED:
            run_row.status = RunStatus.CANCELLED
        else:
            run_row.status = RunStatus.FAILED
        run_row.ended_at = datetime.now(UTC)
        if final_state:
            existing = dict(run_row.final_state or {})
            existing.update(final_state)
            run_row.final_state = existing
        await session.commit()
