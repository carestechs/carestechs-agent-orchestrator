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
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.models import PendingAuxWrite
from app.modules.ai.schemas import DispatchEnvelope

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

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        correlation_id = uuid.uuid4()

        # ``item_id`` (the engine's UUID for the work item / task) is
        # supplied by the runtime via ``ctx.intake``.  FEAT-011 will
        # thread it in from memory; in PR 1 we surface a clear failure
        # if it's absent rather than letting ``transition_item`` 404.
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

        # One transaction: outbox row + engine call.  Engine 4xx/5xx
        # raises ``EngineError``; the ``async with begin()`` exit rolls
        # the outbox insert back, so on failure no aux row leaks.
        signal_name = self._transition_key  # carries the transition tag
        try:
            engine_run_id: str | None = None
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
                response = await self._client.transition_item(
                    item_id=item_id,
                    to_status=self._to_status,
                    correlation_id=correlation_id,
                    actor=self._actor,
                )
                engine_run_id = _extract_engine_run_id(response)
        except EngineError as exc:
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=f"engine_error: {exc}",
            )
        except Exception as exc:
            logger.exception(
                "engine executor %s dispatch failed unexpectedly",
                self._ref,
                extra={"dispatch_id": str(ctx.dispatch_id)},
            )
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=f"{type(exc).__name__}: {exc}",
            )

        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="engine",  # type: ignore[arg-type]
            state="dispatched",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            started_at=started,
            dispatched_at=datetime.now(UTC),
            correlation_id=correlation_id,
            transition_key=self._transition_key,
            engine_run_id=engine_run_id,
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


__all__ = ["EngineExecutor"]
