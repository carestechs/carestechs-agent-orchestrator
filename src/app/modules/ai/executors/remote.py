"""Remote executor adapter (FEAT-009 / T-215).

POSTs the dispatch payload to a configured URL and returns immediately
with a ``dispatched`` envelope; the terminal envelope arrives later via
the ``/hooks/executors/<executor_id>`` webhook (T-216) which calls
:meth:`RunSupervisor.deliver_dispatch` to wake the awaiting loop iter.

The adapter never awaits the terminal state itself — that's the runtime
loop's job (with a configurable timeout).  Adapter responsibilities:

1. Sign the body via the shared HMAC helper.
2. POST with bounded retry on 5xx/connection/timeout (3 attempts,
   500 ms → 4 s backoff + jitter — same shape as the FEAT-003
   Anthropic adapter); 4xx never retries.
3. Return either ``dispatched`` (executor accepted) or ``failed``
   (exhausted retries / 4xx / wrong-shape response).

Once the executor accepts the dispatch, recovery is the runtime loop's
problem — the timeout-and-mark-failed path is centralized there so
local/remote/human modes share one code path.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.core.webhook_auth import sign_body
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope

logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 4.0


class RemoteExecutor:
    """Out-of-process executor reachable via HTTP."""

    mode: ClassVar[ExecutorMode] = "remote"

    def __init__(
        self,
        ref: str,
        url: str,
        *,
        secret: str,
        callback_url: str,
        client: httpx.AsyncClient,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        self.name = ref
        self._ref = ref
        self._url = url
        self._secret = secret
        self._callback_url = callback_url
        self._client = client
        self._connect_timeout = connect_timeout_seconds

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        body = _build_body(ctx, callback_url=self._callback_url)
        body_bytes = _serialize(body)
        signature = sign_body(body_bytes, self._secret)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._client.post(
                    self._url,
                    content=body_bytes,
                    headers={
                        "content-type": "application/json",
                        "x-executor-signature": signature,
                    },
                    timeout=self._connect_timeout,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                if attempt == _MAX_ATTEMPTS:
                    return _failed(
                        ctx,
                        ref=self._ref,
                        started=started,
                        detail=f"connection: {type(exc).__name__}: {exc}",
                    )
                await _sleep_backoff(attempt)
                continue

            if 500 <= response.status_code < 600:
                if attempt == _MAX_ATTEMPTS:
                    return _failed(
                        ctx,
                        ref=self._ref,
                        started=started,
                        detail=f"remote_error: {response.status_code}: {response.text[:200]!r}",
                    )
                await _sleep_backoff(attempt)
                continue

            if response.status_code == 202:
                return _dispatched(ctx, ref=self._ref, started=started)

            # Anything else (4xx, unexpected 2xx) is an immediate fail —
            # 4xx means the executor rejected the dispatch shape.
            return _failed(
                ctx,
                ref=self._ref,
                started=started,
                detail=f"remote_error: {response.status_code}: {response.text[:200]!r}",
            )

        # Should be unreachable — every branch above either returns or continues.
        return _failed(
            ctx,
            ref=self._ref,
            started=started,
            detail="exhausted retry loop without returning (unreachable)",
        )


def _build_body(ctx: DispatchContext, *, callback_url: str) -> dict[str, Any]:
    return {
        "dispatchId": str(ctx.dispatch_id),
        "runId": str(ctx.run_id),
        "stepId": str(ctx.step_id),
        "agentRef": ctx.agent_ref,
        "nodeName": ctx.node_name,
        "intake": dict(ctx.intake),
        "callbackUrl": callback_url,
    }


def _serialize(body: dict[str, Any]) -> bytes:
    import json

    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def _sleep_backoff(attempt: int) -> None:
    base = min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_MAX_SECONDS)
    jitter = random.uniform(0, base * 0.25)
    await asyncio.sleep(base + jitter)


def _dispatched(ctx: DispatchContext, *, ref: str, started: datetime) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=ctx.dispatch_id,
        step_id=ctx.step_id,
        run_id=ctx.run_id,
        executor_ref=ref,
        mode="remote",  # type: ignore[arg-type]
        state="dispatched",  # type: ignore[arg-type]
        intake=dict(ctx.intake),
        started_at=started,
        dispatched_at=datetime.now(UTC),
    )


def _failed(ctx: DispatchContext, *, ref: str, started: datetime, detail: str) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=ctx.dispatch_id,
        step_id=ctx.step_id,
        run_id=ctx.run_id,
        executor_ref=ref,
        mode="remote",  # type: ignore[arg-type]
        state="failed",  # type: ignore[arg-type]
        intake=dict(ctx.intake),
        outcome="error",  # type: ignore[arg-type]
        detail=detail,
        started_at=started,
        finished_at=datetime.now(UTC),
    )
