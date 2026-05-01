"""Sequenced engine-transition executor (BUG-004).

Used for nodes that need to chain N transitions on the same engine
target — e.g. ``close_work_item`` (W4 ``in_progress → ready`` then W6
``ready → closed``) or any other multi-hop step where each hop is
synchronous and the engine validates the from-state.

Wire-shape per dispatch:

1. Resolve the target id (custom resolver or ``engineItemId`` from intake).
2. For each ``(transition_key, to_status)`` hop, in order:
   - generate a fresh ``correlation_id``;
   - in one transaction insert a :class:`PendingAuxWrite` outbox row +
     call ``lifecycle_client.transition_item(...)``;
   - commit.
3. Return a ``dispatched`` engine-mode envelope carrying the **last**
   hop's correlation id — the runtime suspends on it via the supervisor
   future, and the reactor's wake-dispatch step resolves it when the
   final ``item.transitioned`` webhook arrives.

If hop ``k`` (k > 1) raises :class:`EngineError`, earlier hops are already
committed (each in its own tx) and visible to the engine — there is no
automatic compensation.  The dispatch returns a ``failed`` envelope whose
``detail`` lists which hop failed and the engine's error.  Carries hop
``k``'s correlation id in the envelope so an operator can correlate
the trace with the last successful engine call.

Mode is ``"engine"``.  Import quarantine matches :class:`EngineExecutor`:
:class:`FlowEngineLifecycleClient` is only imported under
:data:`typing.TYPE_CHECKING`; the deterministic runtime never transitively
pulls the engine HTTP client into ``sys.modules``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.executors.engine import TargetIdResolver
from app.modules.ai.models import PendingAuxWrite
from app.modules.ai.schemas import DispatchEnvelope

if TYPE_CHECKING:
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


class SequenceEngineExecutor:
    """Engine-bound executor that fires N sequential transitions on one target."""

    mode: ClassVar[ExecutorMode] = "engine"

    def __init__(
        self,
        ref: str,
        *,
        hops: Sequence[tuple[str, str]],
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        actor: str | None = None,
        target_id_resolver: TargetIdResolver | None = None,
    ) -> None:
        if not hops:
            raise ValueError("SequenceEngineExecutor requires at least one hop")
        self.name = ref
        self._ref = ref
        self._hops = [(str(k), str(s)) for k, s in hops]
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._actor = actor
        self._target_id_resolver = target_id_resolver

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        last_correlation_id = uuid.uuid4()  # placeholder; overwritten per hop

        item_id = await self._resolve_target(ctx)
        if isinstance(item_id, _ResolverFailure):
            return self._failed(
                ctx,
                started=started,
                correlation_id=last_correlation_id,
                transition_key=self._hops[-1][0],
                detail=item_id.detail,
            )

        last_engine_run_id: str | None = None
        for hop_index, (transition_key, to_status) in enumerate(self._hops, start=1):
            correlation_id = uuid.uuid4()
            last_correlation_id = correlation_id
            try:
                async with self._session_factory() as session, session.begin():
                    session.add(
                        PendingAuxWrite(
                            correlation_id=correlation_id,
                            signal_name=transition_key,
                            entity_type=_entity_type_from_key(transition_key),
                            entity_id=item_id,
                            payload={
                                "aux_type": "engine_dispatch",
                                "transition_key": transition_key,
                                "to_status": to_status,
                            },
                        )
                    )
                    response = await self._client.transition_item(
                        item_id=item_id,
                        to_status=to_status,
                        correlation_id=correlation_id,
                        actor=self._actor,
                    )
                    last_engine_run_id = _extract_engine_run_id(response)
            except EngineError as exc:
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    transition_key=transition_key,
                    detail=(
                        f"sequence_failed at hop {hop_index}/{len(self._hops)} "
                        f"({transition_key}, to_status={to_status!r}): {exc}"
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "sequence executor %s hop %d/%d raised unexpectedly",
                    self._ref,
                    hop_index,
                    len(self._hops),
                    extra={"dispatch_id": str(ctx.dispatch_id)},
                )
                return self._failed(
                    ctx,
                    started=started,
                    correlation_id=correlation_id,
                    transition_key=transition_key,
                    detail=f"hop {hop_index} crashed: {type(exc).__name__}: {exc}",
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
            correlation_id=last_correlation_id,
            transition_key=self._hops[-1][0],
            engine_run_id=last_engine_run_id,
        )

    async def _resolve_target(
        self, ctx: DispatchContext
    ) -> uuid.UUID | _ResolverFailure:
        if self._target_id_resolver is not None:
            try:
                resolved = await self._target_id_resolver(ctx)
            except Exception as exc:
                return _ResolverFailure(
                    f"target_id_resolver_failed: {type(exc).__name__}: {exc}"
                )
            if resolved is None:
                return _ResolverFailure(
                    "sequence executor: target_id_resolver returned None"
                )
            return resolved
        item_id_raw = ctx.intake.get("engineItemId") or ctx.intake.get("itemId")
        if item_id_raw is None:
            return _ResolverFailure(
                "sequence executor requires 'engineItemId' in dispatch intake; "
                f"got keys={sorted(ctx.intake.keys())!r}"
            )
        try:
            return uuid.UUID(str(item_id_raw))
        except ValueError as exc:
            return _ResolverFailure(
                f"sequence executor: malformed engineItemId={item_id_raw!r}: {exc}"
            )

    def _failed(
        self,
        ctx: DispatchContext,
        *,
        started: datetime,
        correlation_id: uuid.UUID,
        transition_key: str,
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
            transition_key=transition_key,
            engine_run_id=None,
        )


class _ResolverFailure:
    __slots__ = ("detail",)

    def __init__(self, detail: str) -> None:
        self.detail = detail


def _entity_type_from_key(transition_key: str) -> str:
    head, _, _ = transition_key.partition(".")
    if head in {"work_item", "task"}:
        return head
    return "work_item"


def _extract_engine_run_id(response: Mapping[str, Any] | None) -> str | None:
    if not response:
        return None
    for key in ("transitionRunId", "runId", "id"):
        value = response.get(key)
        if value is not None:
            return str(value)
    return None


__all__ = ["SequenceEngineExecutor"]
