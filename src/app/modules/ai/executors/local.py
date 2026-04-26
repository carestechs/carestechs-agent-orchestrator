"""Local executor adapter (FEAT-009 / T-214).

Wraps an in-process async callable so it satisfies the :class:`Executor`
contract.  Local handlers receive the same :class:`DispatchContext` as
remote/human handlers; the difference is purely transport.

A handler returns a plain ``Mapping[str, Any]`` (the dispatch ``result``
payload) or raises.  Exceptions are caught and converted into a
``failed`` envelope so the runtime sees a uniform shape regardless of
mode.

DB session ownership: a handler that needs a DB session opens its own
via the constructor-injected ``session_factory`` (or its closure).  The
loop's iteration session is intentionally *not* threaded in — a stale
or long-lived session escaping into a handler regresses the
per-iteration-session convention and was one of the load-bearing
decisions called out in ``plans/plan-FEAT-009-…``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope

logger = logging.getLogger(__name__)


LocalHandler = Callable[[DispatchContext], Awaitable[Mapping[str, Any]]]


class LocalExecutor:
    """In-process executor: invoke a callable, wrap result/exception."""

    mode: ClassVar[ExecutorMode] = "local"

    def __init__(self, ref: str, handler: LocalHandler) -> None:
        self.name = ref
        self._ref = ref
        self._handler = handler

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        try:
            result = await self._handler(ctx)
        except Exception as exc:
            logger.exception("local executor %s raised", self._ref, extra={"dispatch_id": str(ctx.dispatch_id)})
            return _envelope(
                ctx,
                ref=self._ref,
                started=started,
                state="failed",
                outcome="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        # Defensive: the handler is user code; the static return type
        # promises ``Mapping[str, Any]`` but we still verify at runtime.
        if not isinstance(result, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            return _envelope(
                ctx,
                ref=self._ref,
                started=started,
                state="failed",
                outcome="error",
                detail=(f"local executor {self._ref} returned " f"{type(result).__name__}, expected Mapping[str, Any]"),
            )
        return _envelope(
            ctx,
            ref=self._ref,
            started=started,
            state="completed",
            outcome="ok",
            result=dict(result),
        )


def _envelope(
    ctx: DispatchContext,
    *,
    ref: str,
    started: datetime,
    state: str,
    outcome: str,
    result: dict[str, Any] | None = None,
    detail: str | None = None,
) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=ctx.dispatch_id,
        step_id=ctx.step_id,
        run_id=ctx.run_id,
        executor_ref=ref,
        mode="local",  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        intake=dict(ctx.intake),
        result=result,
        outcome=outcome,  # type: ignore[arg-type]
        detail=detail,
        started_at=started,
        finished_at=datetime.now(UTC),
    )
