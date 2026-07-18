"""Agent-platform executor adapter.

Submits jobs to ``carestechs-agent-platform`` via its async job contract
(``POST /jobs`` → 202 → ``callbackUrl`` delivery) and registers result
transformers that the ``/hooks/platform`` webhook uses to normalise the
platform's result format before handing off to the standard executor
webhook pipeline.

Architecture:

    AgentPlatformExecutor.dispatch()
        → POST {AGENT_PLATFORM_URL}/jobs  { capability, dispatchId, callbackUrl, input }
        ← 202 { jobId }
        returns DispatchEnvelope(state="dispatched")

    Platform finishes job
        → POST {public_base_url}/hooks/platform/{executor_id}
              { dispatchId, outcome: "success"|"failure", result?, error? }

    /hooks/platform/{executor_id} webhook handler
        1. Looks up registered transformer for executor_id
        2. Loads RunMemory snapshot from DB (needed for memory patches)
        3. Calls transformer(raw_result, current_memory) → (transformed_result, memory_patch)
        4. Injects __memory_patch into transformed_result
        5. Normalises outcome: "success" → "ok", "failure" → "error"
        6. Calls service.handle_executor_webhook (same path as RemoteExecutor)

The runtime loop reads __memory_patch from envelope.result and merges it
into RunMemory — exactly the same convention used by LLMContentExecutor.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 4.0

# Module-level registry mapping executor_id →
# (raw_result, current_memory) → (transformed_result, memory_patch)
# Populated by register_platform_transformer at bootstrap time.
_TRANSFORMER_REGISTRY: dict[
    str,
    Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
] = {}


def register_platform_transformer(
    executor_id: str,
    fn: Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Register a result transformer for an AgentPlatformExecutor binding.

    Called from bootstrap.py at startup.  The transformer receives the
    raw platform result and the current RunMemory snapshot, and returns
    (transformed_result, memory_patch).  The webhook handler injects
    memory_patch as __memory_patch into transformed_result before
    delegating to the standard executor webhook pipeline.
    """
    _TRANSFORMER_REGISTRY[executor_id] = fn


def get_platform_transformer(
    executor_id: str,
) -> Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None:
    return _TRANSFORMER_REGISTRY.get(executor_id)


class AgentPlatformExecutor:
    """Executor adapter for carestechs-agent-platform capabilities."""

    mode: ClassVar[ExecutorMode] = "remote"

    def __init__(
        self,
        ref: str,
        capability: str,
        *,
        platform_url: str,
        callback_url: str,
        input_builder: Callable[[DispatchContext], Awaitable[dict[str, Any]]],
        client: httpx.AsyncClient,
    ) -> None:
        self.name = ref
        self._ref = ref
        self._capability = capability
        self._platform_url = platform_url.rstrip("/")
        self._callback_url = callback_url
        self._input_builder = input_builder
        self._client = client

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        try:
            input_data = await self._input_builder(ctx)
        except Exception as exc:
            return _failed(ctx, ref=self._ref, started=started, detail=f"input_builder: {exc}")

        body = {
            "capability": self._capability,
            "dispatchId": str(ctx.dispatch_id),
            "callbackUrl": self._callback_url,
            "input": input_data,
        }

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._client.post(
                    f"{self._platform_url}/jobs",
                    json=body,
                    timeout=10.0,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                if attempt == _MAX_ATTEMPTS:
                    return _failed(
                        ctx, ref=self._ref, started=started,
                        detail=f"connection: {type(exc).__name__}: {exc}",
                    )
                await _sleep_backoff(attempt)
                continue

            if 500 <= response.status_code < 600:
                if attempt == _MAX_ATTEMPTS:
                    return _failed(
                        ctx, ref=self._ref, started=started,
                        detail=f"platform_error: {response.status_code}: {response.text[:200]!r}",
                    )
                await _sleep_backoff(attempt)
                continue

            if response.status_code == 202:
                data = response.json()
                job_id = data.get("jobId", "unknown")
                logger.info(
                    "platform_executor: dispatched capability=%s jobId=%s dispatchId=%s",
                    self._capability, job_id, ctx.dispatch_id,
                )
                return _dispatched(ctx, ref=self._ref, started=started)

            return _failed(
                ctx, ref=self._ref, started=started,
                detail=f"platform_error: {response.status_code}: {response.text[:200]!r}",
            )

        return _failed(
            ctx, ref=self._ref, started=started,
            detail="exhausted retry loop without returning (unreachable)",
        )


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


def _failed(
    ctx: DispatchContext, *, ref: str, started: datetime, detail: str
) -> DispatchEnvelope:
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


__all__ = [
    "AgentPlatformExecutor",
    "get_platform_transformer",
    "register_platform_transformer",
]
