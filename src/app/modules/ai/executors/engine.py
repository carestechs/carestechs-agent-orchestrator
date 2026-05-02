"""Engine executor adapter (FEAT-010 / T-231).

The fourth sibling alongside :class:`LocalExecutor`, :class:`RemoteExecutor`,
and :class:`HumanExecutor`.  Where the others produce *data*, the engine
executor advances **engine state** - its dispatch maps a node selection to
a flow-engine workflow transition (W1-W6, T1-T12).

Wire-shape:

1. Open a session via the constructor-injected ``session_factory``.
2. Generate a fresh ``correlation_id`` (UUID).
3. In **one transaction**: insert a :class:`PendingAuxWrite` row keyed on
   the correlation id, and call ``lifecycle_client.transition_item(...)``
   encoding that same correlation id into the engine's ``triggeredBy``
   via the existing ``orchestrator-corr:<uuid>`` convention.
4. Commit.  Return a ``dispatched`` envelope carrying ``correlation_id``,
   ``transition_key``, and (when surfaced) ``engine_run_id``.

The supervisor's per-dispatch future is later resolved by the reactor
when the matching ``item.transitioned`` webhook arrives — that wake leg
is FEAT-010 PR 2 (T-233).  In PR 1 the executor exists but is not yet
registered for any agent.

**Import quarantine.** The :class:`FlowEngineLifecycleClient` type is
imported only under :data:`typing.TYPE_CHECKING` — the real client is
supplied via constructor injection by ``register_engine_executor``.
This preserves the FEAT-009 invariant that the deterministic runtime
loop never transitively pulls the engine HTTP client into ``sys.modules``
(verified by ``tests/test_engine_executor_import_quarantine.py``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import delete as _sql_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import EngineError
from app.modules.ai.enums import DispatchOutcome, DispatchState
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.models import Dispatch as _Dispatch
from app.modules.ai.models import PendingAuxWrite
from app.modules.ai.schemas import DispatchEnvelope

TargetIdResolver = Callable[[DispatchContext], Awaitable[uuid.UUID | None]]
"""Resolve the engine item id for a single dispatch.

Default behaviour reads ``engineItemId``/``itemId`` from ``DispatchContext.intake``
— preserved for the v0.1.0 work-item-only callers.  BUG-004 task-lifecycle
nodes (``assign_task``, ``generate_plan``, ``submit_implementation``,
``approve_review``) install a custom resolver that reads the *current task's*
``engine_item_id`` from ``LifecycleMemory.tasks[current_task_id]`` so the
transition addresses the task, not the work item.
"""

if TYPE_CHECKING:
    # NEVER import at module scope — the import-quarantine test asserts
    # ``app.modules.ai.lifecycle.engine_client`` is not pulled into
    # ``sys.modules`` when ``runtime_deterministic`` is imported.
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


class EngineExecutor:
    """Engine-bound executor: outbox row + engine transition in one tx."""

    mode: ClassVar[ExecutorMode] = "engine"

    def __init__(
        self,
        ref: str,
        *,
        transition_key: str,
        to_status: str,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        actor: str | None = None,
        target_id_resolver: TargetIdResolver | None = None,
    ) -> None:
        """Bind one ``(agent_ref, node_name)`` to an engine transition.

        Args:
            ref: Executor name carried into the dispatch envelope (e.g.
                ``"engine:work_item.W4"``).
            transition_key: Symbolic identifier for the transition (e.g.
                ``"work_item.W4"`` or ``"task.T6"``).  Carried in the
                trace; not parsed by the executor itself.
            to_status: Target engine status that ``transition_item`` will
                request (e.g. ``"review"``).  Static per binding —
                FEAT-009 multi-target branching is the resolver's job,
                not the executor's.
            lifecycle_client: Constructor-injected engine HTTP client.
            session_factory: Per-dispatch session factory (the loop's
                session is intentionally not threaded in).
            actor: Optional actor string forwarded to ``transition_item``;
                ends up in the engine's transition comment for audit.
        """
        self.name = ref
        self._ref = ref
        self._transition_key = transition_key
        self._to_status = to_status
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._actor = actor
        self._target_id_resolver = target_id_resolver

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        correlation_id = uuid.uuid4()

        # BUG-004: if a custom resolver is installed, it takes precedence;
        # the default path keeps the v0.1.0 ``engineItemId``-from-intake
        # behaviour the work-item-only bindings rely on.
        item_id: uuid.UUID | None = None
        if self._target_id_resolver is not None:
            try:
                item_id = await self._target_id_resolver(ctx)
            except Exception as exc:
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    detail=f"target_id_resolver_failed: {type(exc).__name__}: {exc}",
                )
            if item_id is None:
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    detail=(
                        "engine executor: target_id_resolver returned None — no engine target "
                        "for this dispatch (e.g. current_task_id not set or task missing engine_item_id)"
                    ),
                )
        else:
            item_id_raw = ctx.intake.get("engineItemId") or ctx.intake.get("itemId")
            if item_id_raw is None:
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    detail=(
                        "engine executor requires 'engineItemId' in dispatch intake; "
                        f"got keys={sorted(ctx.intake.keys())!r}"
                    ),
                )
            try:
                item_id = uuid.UUID(str(item_id_raw))
            except ValueError as exc:
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    detail=f"engine executor: malformed engineItemId={item_id_raw!r}: {exc}",
                )

        # Split-tx wake-race fix.  The engine fires its
        # ``item.transitioned`` webhook back to the orchestrator within
        # tens of milliseconds of accepting the transition — often
        # *before* the originating ``transition_item`` call returns.
        # When that happens, the reactor's wake-leg query
        # (``Dispatch.intake['correlation_id'].astext == str(corr)``)
        # cannot match this dispatch unless ``intake.correlation_id`` is
        # already committed.  So:
        #
        #   tx 1 (commits *before* HTTP): write the outbox row + stamp
        #     correlation_id / transition_key / engineItemId onto
        #     ``Dispatch.intake``.  Webhook arriving any time after this
        #     point can match.
        #   HTTP call: ``transition_item``.  Outside any tx.
        #   tx 2 (only on engine failure, compensating): mark Dispatch
        #     FAILED and remove the outbox row.
        #
        # Restart safety is preserved: if the orchestrator crashes
        # between tx 1 commit and the HTTP call, ``reconcile-dispatches``
        # observes a dispatched row with no engine effect, calls
        # ``get_item_state`` to confirm, and resolves accordingly.
        signal_name = self._transition_key
        envelope_intake: dict[str, Any] = {
            **ctx.intake,
            "correlation_id": str(correlation_id),
            "transition_key": self._transition_key,
            "engineItemId": str(item_id),
        }
        dispatched_at = datetime.now(UTC)
        try:
            async with self._session_factory() as session, session.begin():
                session.add(
                    PendingAuxWrite(
                        correlation_id=correlation_id,
                        signal_name=signal_name,
                        entity_type=_entity_type_from_key(self._transition_key),
                        entity_id=item_id,
                        payload={
                            "aux_type": "engine_dispatch",
                            "transition_key": self._transition_key,
                            "to_status": self._to_status,
                        },
                    )
                )
                dispatch_row = await session.get(_Dispatch, ctx.dispatch_id)
                if dispatch_row is not None:
                    merged = {**(dispatch_row.intake or {}), **envelope_intake}
                    # Never let executor stamps stomp the runtime's own
                    # bookkeeping keys.
                    merged.pop("runId", None)
                    merged.pop("nodeName", None)
                    merged.update(
                        {
                            k: v
                            for k, v in (dispatch_row.intake or {}).items()
                            if k in ("runId", "nodeName")
                        }
                    )
                    dispatch_row.intake = merged
        except Exception as exc:
            logger.exception(
                "engine executor %s pre-flight tx failed",
                self._ref,
                extra={"dispatch_id": str(ctx.dispatch_id)},
            )
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=f"pre_flight_tx_failed: {type(exc).__name__}: {exc}",
            )

        # HTTP call — outside any tx, after the outbox + intake commit.
        engine_run_id: str | None = None
        try:
            response = await self._client.transition_item(
                item_id=item_id,
                to_status=self._to_status,
                correlation_id=correlation_id,
                actor=self._actor,
            )
            engine_run_id = _extract_engine_run_id(response)
        except EngineError as exc:
            await self._compensate_failure(
                dispatch_id=ctx.dispatch_id,
                correlation_id=correlation_id,
                reason=f"engine_error: {exc}",
            )
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=f"engine_error: {exc}",
            )
        except Exception as exc:
            logger.exception(
                "engine executor %s HTTP call raised unexpectedly",
                self._ref,
                extra={"dispatch_id": str(ctx.dispatch_id)},
            )
            await self._compensate_failure(
                dispatch_id=ctx.dispatch_id,
                correlation_id=correlation_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=f"{type(exc).__name__}: {exc}",
            )

        # ``envelope_intake`` was already built + committed to the
        # dispatch row in tx 1 above; reuse it on the envelope for
        # symmetry with the runtime's post-dispatch update path.
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="engine",  # type: ignore[arg-type]
            state="dispatched",  # type: ignore[arg-type]
            intake=envelope_intake,
            started_at=started,
            dispatched_at=dispatched_at,
            correlation_id=correlation_id,
            transition_key=self._transition_key,
            engine_run_id=engine_run_id,
        )

    async def _compensate_failure(
        self,
        *,
        dispatch_id: uuid.UUID,
        correlation_id: uuid.UUID,
        reason: str,
    ) -> None:
        """Reverse the pre-flight tx after the engine call fails.

        Marks the dispatch as ``failed`` (state-machine transition
        DISPATCHED → FAILED) and removes the outbox row so a stray
        webhook for an unrelated transition can't accidentally
        materialise it.  Idempotent: missing rows are no-ops.
        """
        try:
            async with self._session_factory() as session, session.begin():
                dispatch_row = await session.get(_Dispatch, dispatch_id)
                if dispatch_row is not None and dispatch_row.state == DispatchState.DISPATCHED.value:
                    dispatch_row.mark_failed(
                        at=datetime.now(UTC),
                        outcome=DispatchOutcome.ERROR,
                        detail=reason,
                    )
                await session.execute(
                    _sql_delete(PendingAuxWrite).where(
                        PendingAuxWrite.correlation_id == correlation_id
                    )
                )
        except Exception:
            logger.exception(
                "engine executor %s compensating tx failed (dispatch_id=%s)",
                self._ref,
                dispatch_id,
            )

    def _failed(
        self,
        ctx: DispatchContext,
        *,
        started: datetime,
        correlation_id: uuid.UUID,
        detail: str,
    ) -> DispatchEnvelope:
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="engine",  # type: ignore[arg-type]
            state="failed",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            outcome="error",  # type: ignore[arg-type]
            detail=detail,
            started_at=started,
            finished_at=datetime.now(UTC),
            correlation_id=correlation_id,
            transition_key=self._transition_key,
            engine_run_id=None,
        )


def _entity_type_from_key(transition_key: str) -> str:
    """Parse ``"work_item.W4"`` -> ``"work_item"``; default to ``"work_item"``."""
    head, _, _ = transition_key.partition(".")
    if head in {"work_item", "task"}:
        return head
    return "work_item"


def _extract_engine_run_id(response: Mapping[str, Any] | None) -> str | None:
    """Best-effort: pull a run/transition id out of the engine response.

    The engine's ``transition_item`` returns the parsed ``data`` object;
    different engine versions surface the run id under different keys.
    Trace entries treat ``engine_run_id`` as optional, so a miss is fine.
    """
    if not response:
        return None
    for key in ("transitionRunId", "runId", "id"):
        value = response.get(key)
        if value is not None:
            return str(value)
    return None


__all__ = ["EngineExecutor", "TargetIdResolver"]
