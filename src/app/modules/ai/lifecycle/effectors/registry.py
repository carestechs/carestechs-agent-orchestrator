"""Effector dispatch registry + canonical transition-key scheme.

The registry is per-process. The composition root instantiates one,
registers concrete effectors against transition keys, and hands the
instance to the reactor (wired in T-162).

Dispatch order is insertion order per key. Each effector fires in its
own try/except envelope so one misbehaving effector never blocks the
rest — a contract breach is logged, a structured ``error`` result is
appended, and dispatch continues.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import UTC, datetime

from app.modules.ai.lifecycle.effectors.base import Effector
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)
from app.modules.ai.schemas import EffectorCallDto
from app.modules.ai.trace import TraceStore

logger = logging.getLogger(__name__)


def build_transition_key(
    entity_type: str, from_state: str | None, to_state: str
) -> str:
    """Canonical key for registering + dispatching effectors.

    Two shapes:

    * ``"{entity_type}:entry:{to_state}"`` — entry-only (no from-state
      context needed; e.g. W1 creating a work item).
    * ``"{entity_type}:{from_state}->{to_state}"`` — transition between
      known states (most lifecycle transitions).
    """
    if from_state is None:
        return f"{entity_type}:entry:{to_state}"
    return f"{entity_type}:{from_state}->{to_state}"


async def dispatch_effector(
    effector: Effector,
    ctx: EffectorContext,
    trace: TraceStore,
) -> EffectorResult:
    """Fire a single effector, emit its ``effector_call`` trace, and return.

    Mirrors :meth:`EffectorRegistry.fire_all`'s per-effector firing loop
    for call sites that need to dispatch an effector constructed on-the-fly
    (e.g. a signal adapter that pulls the GitHub client from per-request
    DI and can't register permanently at lifespan). Guarantees the same
    trace shape as the registry, so observability stays uniform.

    :class:`~app.core.exceptions.ValidationError` is re-raised unchanged —
    it signals a caller-data defect (e.g. malformed PR URL) that the
    FastAPI handler maps to 400. Every other exception is captured as
    an ``error`` result so the rest of the signal flow is not disturbed.
    """
    from app.core.exceptions import ValidationError

    start = time.monotonic()
    try:
        result = await effector.fire(ctx)
    except ValidationError:
        raise
    except Exception as exc:
        result = EffectorResult(
            effector_name=effector.name,
            status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error_code="effector-exception",
            detail=f"{type(exc).__name__}: {exc}",
        )
        logger.exception(
            "effector raised",
            extra={
                "effector": effector.name,
                "entity_id": str(ctx.entity_id),
            },
        )
    dto = EffectorCallDto(
        effector_name=result.effector_name,
        entity_type=ctx.entity_type,
        entity_id=ctx.entity_id,
        transition=ctx.transition,
        transition_key=build_transition_key(
            ctx.entity_type, ctx.from_state, ctx.to_state
        ),
        status=result.status,
        duration_ms=result.duration_ms,
        error_code=result.error_code,
        detail=result.detail,
        emitted_at=datetime.now(UTC),
    )
    try:
        await trace.record_effector_call(ctx.entity_id, dto)
    except Exception:
        logger.exception(
            "effector trace emit failed",
            extra={
                "effector": result.effector_name,
                "entity_id": str(ctx.entity_id),
            },
        )
    return result


class EffectorRegistry:
    """In-process registry + dispatcher for lifecycle effectors."""

    def __init__(self, trace: TraceStore) -> None:
        self._trace = trace
        self._effectors: dict[str, list[Effector]] = defaultdict(list)

    def register(self, key: str, effector: Effector) -> None:
        """Append *effector* to the dispatch list for *key*."""
        self._effectors[key].append(effector)

    def registered_keys(self) -> frozenset[str]:
        """Return the set of transition keys with at least one effector.

        Used by T-171's startup validator to cross-check against the
        transition catalog and the ``no_effector`` exemption list.
        """
        return frozenset(k for k, v in self._effectors.items() if v)

    async def fire_all(self, ctx: EffectorContext) -> list[EffectorResult]:
        """Dispatch every effector registered under *ctx*'s transition key.

        Emits one ``effector_call`` trace entry per result, in order.
        Exceptions from an effector are caught and surfaced as an
        ``error`` result; later effectors still fire.
        """
        key = build_transition_key(ctx.entity_type, ctx.from_state, ctx.to_state)
        results: list[EffectorResult] = []
        for effector in self._effectors.get(key, []):
            start = time.monotonic()
            try:
                result = await effector.fire(ctx)
            except Exception as exc:
                result = EffectorResult(
                    effector_name=effector.name,
                    status="error",
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error_code="effector-exception",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                logger.exception(
                    "effector raised",
                    extra={
                        "effector": effector.name,
                        "entity_id": str(ctx.entity_id),
                        "transition_key": key,
                    },
                )
            results.append(result)
            await self._emit_trace(ctx, result)
        return results

    async def _emit_trace(
        self, ctx: EffectorContext, result: EffectorResult
    ) -> None:
        dto = EffectorCallDto(
            effector_name=result.effector_name,
            entity_type=ctx.entity_type,
            entity_id=ctx.entity_id,
            transition=ctx.transition,
            transition_key=build_transition_key(
                ctx.entity_type, ctx.from_state, ctx.to_state
            ),
            status=result.status,
            duration_ms=result.duration_ms,
            error_code=result.error_code,
            detail=result.detail,
            emitted_at=datetime.now(UTC),
        )
        try:
            await self._trace.record_effector_call(ctx.entity_id, dto)
        except Exception:
            logger.exception(
                "effector trace emit failed",
                extra={
                    "effector": result.effector_name,
                    "entity_id": str(ctx.entity_id),
                },
            )


