"""Composite LLM-content + engine-transition executor (FEAT-011 / T-254).

Implements **Option 1** from the FEAT-011 design doc: one executor per
composite node that internally chains an :class:`LLMContentExecutor`
call followed by an engine transition.  This keeps the agent YAML at
its target eight-node shape without splitting each composite into two
adjacent nodes.

The wire-shape:

1. Run the inner :class:`LLMContentExecutor` synchronously.  On failure
   return its envelope unchanged (mode coerced to ``"engine"`` so it
   matches the registered binding's mode and the runtime treats it as a
   terminal engine-mode failure — no engine HTTP call is made).
2. On success, write the LLM-content patch into ``RunMemory.data`` under
   the lifecycle namespace inside the **same transaction** that inserts
   the :class:`PendingAuxWrite` outbox row and calls
   ``lifecycle_client.transition_item``.  This is the load-bearing
   detail the design doc calls out: the engine-mode dispatch returns
   ``state="dispatched"`` (non-terminal), so the runtime's
   ``__memory_patch`` merge does not run for it.  Persisting the patch
   inline keeps the LLM-produced content alive across the engine round
   trip.
3. Return a ``dispatched`` engine-mode envelope carrying the
   correlation id; the supervisor future is resolved later when the
   engine's webhook is matched by the reactor's wake-dispatch step.

DB session ownership mirrors :class:`EngineExecutor` — the executor
opens its own session via the injected ``session_factory``.  The
runtime's per-iteration session is intentionally not threaded in.

Import quarantine: this module imports neither ``app.core.llm`` nor any
provider SDK at module scope.  The :class:`LLMProvider` reaches the
inner executor through constructor injection; the composite itself only
references its inner executor and the engine client.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.executors.llm_content import LLMContentExecutor
from app.modules.ai.models import PendingAuxWrite, RunMemory
from app.modules.ai.schemas import DispatchEnvelope

if TYPE_CHECKING:
    # Import-quarantine: keep ``FlowEngineLifecycleClient`` off the
    # module-scope graph so ``runtime_deterministic`` does not pull the
    # engine HTTP client into ``sys.modules``.
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


MemoryPatchBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
"""Callable that converts the validated LLM result dict into a memory patch.

Receives the dict the inner :class:`LLMContentExecutor` returned (already
validated against its schema).  The patch is merged shallowly into
``RunMemory.data`` (top-level keys overwrite their previous values).
Use :func:`app.modules.ai.tools.lifecycle.memory.write_lifecycle_memory`
for the canonical lifecycle shape.
"""


class CompositeLLMEngineExecutor:
    """Composite executor: LLM-content production then engine transition.

    Registered as ``mode="engine"`` so the runtime honours the wake-on-
    correlation contract.  When the inner LLM call fails, the failed
    envelope is still surfaced as ``mode="engine"`` (the binding's
    declared mode) — the runtime treats it as a terminal engine-mode
    failure with no outbox row to reconcile.
    """

    mode: ClassVar[ExecutorMode] = "engine"

    def __init__(
        self,
        ref: str,
        *,
        llm_executor: LLMContentExecutor,
        transition_key: str,
        to_status: str,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        memory_patch_builder: MemoryPatchBuilder,
        actor: str | None = None,
    ) -> None:
        self.name = ref
        self._ref = ref
        self._llm = llm_executor
        self._transition_key = transition_key
        self._to_status = to_status
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._memory_patch_builder = memory_patch_builder
        self._actor = actor

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)

        # 1. Inner LLM-content step.
        llm_envelope = await self._llm.dispatch(ctx)
        if llm_envelope.outcome is not None and llm_envelope.outcome.value != "ok":
            return self._failed(
                ctx,
                started=started,
                correlation_id=uuid.uuid4(),
                detail=f"llm_content_failed: {llm_envelope.detail or 'no detail'}",
            )

        # The LLM result is the validated dict the inner executor
        # returned.  The patch builder converts it into a JSON-safe
        # ``RunMemory.data`` patch.
        llm_result: dict[str, Any] = dict(llm_envelope.result or {})
        try:
            memory_patch = self._memory_patch_builder(llm_result)
        except Exception as exc:
            return self._failed(
                ctx,
                started=started,
                correlation_id=uuid.uuid4(),
                detail=f"memory_patch_builder_failed: {type(exc).__name__}: {exc}",
            )

        # 2. ``engineItemId`` resolution mirrors :class:`EngineExecutor`.
        item_id_raw = ctx.intake.get("engineItemId") or ctx.intake.get("itemId")
        correlation_id = uuid.uuid4()
        if item_id_raw is None:
            return self._failed(
                ctx,
                started=started,
                correlation_id=correlation_id,
                detail=(
                    "composite executor requires 'engineItemId' in dispatch intake; "
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
                detail=f"composite executor: malformed engineItemId={item_id_raw!r}: {exc}",
            )

        # 3. One transaction: memory write + outbox row + engine call.
        signal_name = self._transition_key
        engine_run_id: str | None = None
        try:
            async with self._session_factory() as session, session.begin():
                # Memory write — merge the LLM patch under the namespace.
                await self._merge_memory(session, run_id=ctx.run_id, patch=memory_patch)
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
                "composite executor %s dispatch failed unexpectedly",
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _merge_memory(
        self,
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        patch: Mapping[str, Any],
    ) -> None:
        """Shallow-merge ``patch`` into ``RunMemory.data`` for ``run_id``.

        Mirrors the runtime's own ``_write_state`` merge semantics:
        top-level keys in ``patch`` overwrite their previous values;
        keys outside ``patch`` (e.g. the runtime's bookkeeping namespace
        ``__feat009``) are preserved untouched.  Deep-copies the loaded
        JSON before mutating + flag_modified so SQLAlchemy detects the
        change (the JSON type's equality check treats shared-ref nested
        dicts as unchanged otherwise).
        """
        import copy as _copy

        from sqlalchemy.orm.attributes import flag_modified

        memory_row = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
        existing: dict[str, Any] = _copy.deepcopy(memory_row.data if memory_row is not None else {}) or {}
        for key, value in patch.items():
            existing[key] = value
        if memory_row is None:
            session.add(RunMemory(run_id=run_id, data=existing))
        else:
            memory_row.data = existing
            flag_modified(memory_row, "data")

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
    """Parse ``"work_item.W1"`` -> ``"work_item"``; default to ``"work_item"``.

    Mirrors the same helper in :mod:`app.modules.ai.executors.engine` —
    duplicated here to keep the composite module self-contained.
    """
    head, _, _ = transition_key.partition(".")
    if head in {"work_item", "task"}:
        return head
    return "work_item"


def _extract_engine_run_id(response: Mapping[str, Any] | None) -> str | None:
    """Pull a run/transition id out of the engine response (best-effort)."""
    if not response:
        return None
    for key in ("transitionRunId", "runId", "id"):
        value = response.get(key)
        if value is not None:
            return str(value)
    return None


__all__ = ["CompositeLLMEngineExecutor", "MemoryPatchBuilder"]
