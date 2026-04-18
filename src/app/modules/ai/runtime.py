"""Agent runtime loop (T-039).

Each iteration opens its own :class:`AsyncSession`, evaluates stop
conditions, calls the policy, persists the decision, dispatches a step,
then awaits a webhook-driven wake-up.  Termination writes ``status``,
``stop_reason``, ``final_state``, and ``ended_at`` in one commit and emits
a final trace line.

Every terminal path routes through :func:`_terminate`.  The control flow
has exactly one way to exit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.core.exceptions import EngineError, PolicyError, ProviderError
from app.core.llm import LLMProvider, ToolCall
from app.core.logging import bind_run_id, bind_step_id
from app.modules.ai.enums import StepStatus, StopReason
from app.modules.ai.models import PolicyCall, Run, RunMemory, Step
from app.modules.ai.runtime_helpers import (
    PauseForSignal,
    build_prompt_context,
    merge_memory,
    run_status_for,
    tool_call_to_node,
    validate_tool_arguments,
)
from app.modules.ai.schemas import PolicyCallDto, StepDto
from app.modules.ai.stop_conditions import (
    RuntimeState,
    evaluate,
    find_correction_exceedance,
)
from app.modules.ai.tools import TERMINATE_TOOL_NAME, build_tools
from app.modules.ai.tools.lifecycle.memory import (
    LifecycleMemory,
    from_run_memory,
    to_run_memory,
)
from app.modules.ai.tools.lifecycle.registry import LOCAL_TOOL_HANDLERS, is_local_tool

if TYPE_CHECKING:
    from app.modules.ai.agents import AgentDefinition
    from app.modules.ai.engine_client import FlowEngineClient
    from app.modules.ai.supervisor import RunSupervisor
    from app.modules.ai.trace import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the policy for an agent-driven loop. Each turn, you will see "
    "the current run state — the intake, the accumulated memory, and the "
    "last step's outcome — and you MUST advance the run by calling exactly "
    "one of the provided tools. The tool list on each turn is already "
    "narrowed to the moves allowed from the current state: do not try to "
    "re-run a stage that already completed; choose the forward action. "
    "Read memory carefully — once a step wrote to memory, that record IS "
    "the evidence that the step is done. "
    f"Call the {TERMINATE_TOOL_NAME!r} tool only when no progress tool is "
    "available AND the run's goal has been met."
)


def _allowed_tools_for(
    agent: AgentDefinition,
    last_tool: ToolCall | None,
    fallback: list[str],
) -> list[str]:
    """Return the tool names the policy is allowed to choose from this iteration.

    Gating uses ``agent.flow.transitions``: on the very first iteration
    (``last_tool is None``) only the entry node is exposed; subsequent
    iterations expose the declared successors of the last tool.  A missing
    or empty transitions entry falls back to *fallback* (every declared
    node) so permissive agents still work.

    This is the "Omit it from the per-call tool list to gate availability"
    pattern from CLAUDE.md — stops the policy from re-running completed
    stages just because the tool is still visible.
    """
    transitions = agent.flow.transitions or {}
    if last_tool is None:
        return [agent.flow.entry_node]
    successors = transitions.get(last_tool.name)
    if successors is None:
        return fallback
    # Empty successor list = terminal node; returning [] hides every node
    # tool, leaving only ``terminate`` in the tool list the runtime builds.
    return list(successors)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_loop(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    policy: LLMProvider,
    engine: FlowEngineClient,
    trace: TraceStore,
    supervisor: RunSupervisor,
    session_factory: async_sessionmaker[AsyncSession],
    cancel_event: asyncio.Event,
) -> None:
    """Execute the agent runtime loop until a stop condition fires."""
    with bind_run_id(str(run_id)):
        try:
            await _mark_running(run_id, session_factory)
            await _iterate(
                run_id=run_id,
                agent=agent,
                policy=policy,
                engine=engine,
                trace=trace,
                supervisor=supervisor,
                session_factory=session_factory,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            await _terminate(run_id, session_factory, StopReason.CANCELLED, final_state={})
            raise
        except Exception as exc:
            logger.exception("runtime loop crashed for run %s", run_id)
            await _terminate(
                run_id,
                session_factory,
                StopReason.ERROR,
                final_state={"crash": str(exc)},
            )


# ---------------------------------------------------------------------------
# Main loop body
# ---------------------------------------------------------------------------


async def _iterate(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    policy: LLMProvider,
    engine: FlowEngineClient,
    trace: TraceStore,
    supervisor: RunSupervisor,
    session_factory: async_sessionmaker[AsyncSession],
    cancel_event: asyncio.Event,
) -> None:
    step_count = 0
    token_count = 0
    last_tool: ToolCall | None = None
    last_policy_error: Exception | None = None
    last_engine_error: Exception | None = None

    terminal_nodes = frozenset(agent.terminal_nodes)
    all_node_names = [n.name for n in agent.nodes]

    max_steps = agent.default_budget.max_steps
    max_tokens = agent.default_budget.max_tokens
    max_corrections = get_settings().lifecycle_max_corrections

    while True:
        correction_attempts = await _load_correction_attempts(run_id, session_factory)
        state = RuntimeState(
            last_tool=last_tool,
            step_count=step_count,
            token_count=token_count,
            max_steps=max_steps,
            max_tokens=max_tokens,
            last_policy_error=last_policy_error,
            last_engine_error=last_engine_error,
            cancel_requested=cancel_event.is_set() or supervisor.is_cancelled(run_id),
            terminal_nodes=terminal_nodes,
            correction_attempts=correction_attempts,
            max_corrections=max_corrections,
        )
        reason = evaluate(state)
        if reason is not None:
            await _terminate(
                run_id,
                session_factory,
                reason,
                final_state=_final_state_from(state, last_tool),
            )
            return

        # ------------------------------------------------------------------
        # Policy call
        # ------------------------------------------------------------------
        try:
            prompt_context = await _build_context(run_id, session_factory)
            allowed_tools = _allowed_tools_for(agent, last_tool, all_node_names)
            tools = build_tools(agent, allowed_tools)
            # Anthropic's Messages API requires ``content`` to be a string
            # or a list of content blocks — a raw dict yields HTTP 400.  We
            # serialize the context to JSON so the policy sees the full
            # structure verbatim.  The ``PolicyCall`` row still stores the
            # dict form via ``prompt_context`` for trace fidelity.
            messages = [
                {"role": "user", "content": json.dumps(prompt_context, default=str)}
            ]
            tool_call = await policy.chat_with_tools(
                system=_SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
            )
        except (ProviderError, PolicyError) as exc:
            last_policy_error = exc
            continue

        token_count += tool_call.usage.input_tokens + tool_call.usage.output_tokens
        last_tool = tool_call

        # Persist the policy call BEFORE we do anything with it.
        step_row = await _persist_policy_call(
            run_id=run_id,
            agent=agent,
            tool_call=tool_call,
            prompt_context=prompt_context,
            tools=tools,
            step_number=step_count + 1,
            session_factory=session_factory,
        )

        if step_row is not None:
            # Only written for non-terminate decisions (terminate loops back to evaluate()).
            await _record_step_trace(trace, run_id, step_row)
            await _record_policy_trace(trace, run_id, step_row.id, tool_call, tools, agent)

        if tool_call.name == TERMINATE_TOOL_NAME:
            # Loop back; next evaluate() returns POLICY_TERMINATED.
            continue

        # ------------------------------------------------------------------
        # Tool → node, arg validation
        # ------------------------------------------------------------------
        try:
            node_name = tool_call_to_node(tool_call, agent)
            validate_tool_arguments(tool_call, agent)
        except PolicyError as exc:
            last_policy_error = exc
            continue

        assert node_name is not None  # terminate already handled above
        assert step_row is not None

        # ------------------------------------------------------------------
        # Local tool path (FEAT-005): execute the handler in-process.
        # Bypasses engine dispatch; may pause via PauseForSignal.
        # ------------------------------------------------------------------
        if is_local_tool(node_name):
            with bind_step_id(str(step_row.id)):
                try:
                    await _execute_local_tool(
                        run_id=run_id,
                        step_id=step_row.id,
                        tool_call=tool_call,
                        supervisor=supervisor,
                        session_factory=session_factory,
                        trace=trace,
                    )
                except PolicyError as exc:
                    last_policy_error = exc
                    await _mark_step_failed(step_row.id, exc, session_factory)
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_policy_error = PolicyError(f"local tool crashed: {exc}")
                    await _mark_step_failed(step_row.id, last_policy_error, session_factory)
                    continue

            step_count += 1
            continue

        node = next(n for n in agent.nodes if n.name == node_name)

        # ------------------------------------------------------------------
        # Dispatch to engine
        # ------------------------------------------------------------------
        with bind_step_id(str(step_row.id)):
            try:
                engine_run_id = await engine.dispatch_node(
                    run_id=run_id,
                    step_id=step_row.id,
                    agent_ref=agent.ref,
                    node_name=node_name,
                    node_inputs=tool_call.arguments,
                )
            except EngineError as exc:
                last_engine_error = exc
                await _mark_step_failed(step_row.id, exc, session_factory)
                continue

            await _mark_step_dispatched(step_row.id, engine_run_id, session_factory)

            # ------------------------------------------------------------------
            # Wait for webhook
            # ------------------------------------------------------------------
            try:
                await asyncio.wait_for(
                    supervisor.await_wake(run_id),
                    timeout=node.timeout_seconds,
                )
            except TimeoutError:
                last_engine_error = EngineError(
                    f"step timeout after {node.timeout_seconds}s",
                    engine_http_status=None,
                    engine_correlation_id=None,
                    original_body=None,
                )
                await _mark_step_failed(step_row.id, last_engine_error, session_factory)
                continue
            finally:
                supervisor.clear_wake(run_id)

        # ------------------------------------------------------------------
        # Post-step: merge memory, advance counters
        # ------------------------------------------------------------------
        refreshed_step = await _reload_step(step_row.id, session_factory)
        if refreshed_step is not None and refreshed_step.status == StepStatus.FAILED:
            last_engine_error = EngineError(
                "step finished in FAILED state per webhook",
                engine_http_status=None,
                engine_correlation_id=None,
                original_body=None,
            )
            continue
        if refreshed_step is not None:
            await _merge_memory(run_id, refreshed_step.node_result, session_factory)

        step_count += 1


# ---------------------------------------------------------------------------
# Session helpers (each opens its own AsyncSession per CLAUDE.md)
# ---------------------------------------------------------------------------


async def _mark_running(
    run_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id))
        if run is None:
            return
        run.status = "running"
        await session.commit()


async def _build_context(
    run_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    async with session_factory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id))
        memory = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
        last_step = await session.scalar(
            select(Step).where(Step.run_id == run_id).order_by(Step.step_number.desc()).limit(1)
        )
        if run is None or memory is None:
            return {"run_id": str(run_id)}
        return build_prompt_context(run, memory, last_step)


async def _persist_policy_call(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    tool_call: ToolCall,
    prompt_context: dict[str, Any],
    tools: list[Any],
    step_number: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> Step | None:
    """Write PolicyCall + (for non-terminate choices) the pending Step row."""
    async with session_factory() as session:
        if tool_call.name == TERMINATE_TOOL_NAME:
            # Terminate-only policy calls are recorded against a lightweight
            # sentinel step so the PolicyCall FK (unique step_id) still has a
            # target.  But the loop doesn't need that row — we don't dispatch
            # anything.  For v1 we skip persistence on terminate and let the
            # terminal trace line carry the information.
            return None

        step = Step(
            run_id=run_id,
            step_number=step_number,
            node_name=tool_call.name,
            node_inputs=tool_call.arguments,
            status=StepStatus.PENDING,
        )
        session.add(step)
        await session.flush()

        policy_call = PolicyCall(
            run_id=run_id,
            step_id=step.id,
            prompt_context=prompt_context,
            available_tools=[
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ],
            provider=agent.ref,  # placeholder — real provider/model come from policy
            model="stub",
            selected_tool=tool_call.name,
            tool_arguments=tool_call.arguments,
            input_tokens=tool_call.usage.input_tokens,
            output_tokens=tool_call.usage.output_tokens,
            latency_ms=tool_call.usage.latency_ms,
            raw_response=tool_call.raw_response,
        )
        session.add(policy_call)
        await session.commit()
        await session.refresh(step)
        return step


async def _mark_step_dispatched(
    step_id: uuid.UUID,
    engine_run_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        step = await session.scalar(select(Step).where(Step.id == step_id))
        if step is None:
            return
        step.engine_run_id = engine_run_id
        step.status = StepStatus.DISPATCHED
        step.dispatched_at = datetime.now(UTC)
        await session.commit()


async def _mark_step_failed(
    step_id: uuid.UUID,
    error: Exception,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        step = await session.scalar(select(Step).where(Step.id == step_id))
        if step is None:
            return
        step.status = StepStatus.FAILED
        step.error = _error_to_dict(error)
        step.completed_at = datetime.now(UTC)
        await session.commit()


async def _reload_step(
    step_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> Step | None:
    async with session_factory() as session:
        return await session.scalar(select(Step).where(Step.id == step_id))


async def _merge_memory(
    run_id: uuid.UUID,
    node_result: dict[str, Any] | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not node_result:
        return
    async with session_factory() as session:
        memory = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
        if memory is None:
            return
        memory.data = merge_memory(memory.data, node_result)
        flag_modified(memory, "data")
        await session.commit()


async def _load_correction_attempts(
    run_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, int]:
    """Snapshot ``memory.correction_attempts`` for stop-condition evaluation.

    Returns an empty dict when the run has no memory row yet or when the
    agent isn't a lifecycle agent (its memory shape won't have the field).
    Defensive: invalid JSON types fall back to ``{}``.
    """
    async with session_factory() as session:
        memory = await session.scalar(
            select(RunMemory).where(RunMemory.run_id == run_id)
        )
        if memory is None:
            return {}
        data = memory.data or {}
    raw: Any = data.get("correctionAttempts") or data.get("correction_attempts") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): int(v)
        for k, v in cast("dict[Any, Any]", raw).items()
        if isinstance(v, int)
    }


async def _load_lifecycle_memory(
    run_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession]
) -> LifecycleMemory:
    async with session_factory() as session:
        memory = await session.scalar(
            select(RunMemory).where(RunMemory.run_id == run_id)
        )
        data = memory.data if memory is not None else {}
    return from_run_memory(data)


async def _save_lifecycle_memory(
    run_id: uuid.UUID,
    lifecycle_memory: LifecycleMemory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        memory = await session.scalar(
            select(RunMemory).where(RunMemory.run_id == run_id)
        )
        if memory is None:
            return
        memory.data = to_run_memory(lifecycle_memory)
        flag_modified(memory, "data")
        await session.commit()


async def _mark_step_in_progress(
    step_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        step = await session.scalar(select(Step).where(Step.id == step_id))
        if step is None:
            return
        step.status = StepStatus.IN_PROGRESS
        step.dispatched_at = datetime.now(UTC)
        await session.commit()


async def _mark_step_completed(
    step_id: uuid.UUID,
    node_result: dict[str, Any] | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        step = await session.scalar(select(Step).where(Step.id == step_id))
        if step is None:
            return
        step.status = StepStatus.COMPLETED
        step.node_result = node_result
        step.completed_at = datetime.now(UTC)
        await session.commit()


async def _execute_local_tool(
    *,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    tool_call: ToolCall,
    supervisor: RunSupervisor,
    session_factory: async_sessionmaker[AsyncSession],
    trace: TraceStore,
) -> None:
    """Run a lifecycle tool in-process and persist the step accordingly.

    Two paths:
    - ``LifecycleMemory`` return → step completes immediately.
    - ``(LifecycleMemory, PauseForSignal)`` return → step goes
      ``in_progress``, loop awaits :meth:`RunSupervisor.await_signal`,
      and the signal payload becomes ``node_result`` on step completion.
    """
    handler = LOCAL_TOOL_HANDLERS[tool_call.name]
    memory = await _load_lifecycle_memory(run_id, session_factory)
    result = await handler(tool_call.arguments, memory=memory)

    new_memory: LifecycleMemory
    pause: PauseForSignal | None
    if isinstance(result, tuple):
        new_memory, pause = result
    else:
        new_memory = result
        pause = None

    await _save_lifecycle_memory(run_id, new_memory, session_factory)

    if pause is None:
        await _mark_step_completed(step_id, {"local": True}, session_factory)
    else:
        await _mark_step_in_progress(step_id, session_factory)
        refreshed = await _reload_step(step_id, session_factory)
        if refreshed is not None:
            await _record_step_trace(trace, run_id, refreshed)

        payload = await supervisor.await_signal(run_id, pause.name, pause.task_id)
        await _mark_step_completed(step_id, payload, session_factory)

    refreshed = await _reload_step(step_id, session_factory)
    if refreshed is not None:
        await _record_step_trace(trace, run_id, refreshed)


async def _terminate(
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    reason: StopReason,
    *,
    final_state: dict[str, Any],
) -> None:
    async with session_factory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id))
        if run is None:
            return
        if run.status in {"completed", "failed", "cancelled"}:
            return  # already terminal — avoid overwriting
        run.status = run_status_for(reason)
        run.stop_reason = reason
        run.final_state = final_state
        run.ended_at = datetime.now(UTC)
        await session.commit()


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _error_to_dict(exc: Exception) -> dict[str, Any]:
    info: dict[str, Any] = {"type": exc.__class__.__name__, "message": str(exc)}
    if isinstance(exc, EngineError):
        info["engine_http_status"] = exc.engine_http_status
        info["engine_correlation_id"] = exc.engine_correlation_id
        info["original_body"] = exc.original_body
    return info


def _final_state_from(state: RuntimeState, last_tool: ToolCall | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "step_count": state.step_count,
        "token_count": state.token_count,
        "max_steps": state.max_steps,
        "max_tokens": state.max_tokens,
    }
    if last_tool is not None:
        snapshot["last_tool"] = last_tool.name
    if state.last_policy_error is not None:
        snapshot["policy_error"] = str(state.last_policy_error)
    if state.last_engine_error is not None:
        snapshot["engine_error"] = str(state.last_engine_error)

    exceedance = find_correction_exceedance(state)
    if exceedance is not None:
        task_id, attempts = exceedance
        snapshot["reason"] = "correction_budget_exceeded"
        snapshot["task_id"] = task_id
        snapshot["attempts"] = attempts
    return snapshot


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


async def _record_step_trace(trace: TraceStore, run_id: uuid.UUID, step: Step) -> None:
    try:
        dto = StepDto.model_validate(step, from_attributes=True)
        await trace.record_step(run_id, dto)
    except Exception:
        logger.warning("trace write failed for step", exc_info=True)


async def _record_policy_trace(
    trace: TraceStore,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    tool_call: ToolCall,
    tools: list[Any],
    agent: AgentDefinition,
) -> None:
    try:
        dto = PolicyCallDto(
            id=uuid.uuid4(),  # trace-only id; DB has the canonical one
            step_id=step_id,
            provider=agent.ref,
            model="stub",
            selected_tool=tool_call.name,
            tool_arguments=tool_call.arguments,
            available_tools=[
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ],
            input_tokens=tool_call.usage.input_tokens,
            output_tokens=tool_call.usage.output_tokens,
            latency_ms=tool_call.usage.latency_ms,
            created_at=datetime.now(UTC),
        )
        await trace.record_policy_call(run_id, dto)
    except Exception:
        logger.warning("trace write failed for policy call", exc_info=True)
