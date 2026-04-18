"""ASGI-aware flow-engine echo for integration tests (T-054).

``EngineEcho`` implements the same ``dispatch_node`` signature as the real
:class:`~app.modules.ai.engine_client.FlowEngineClient`.  On each call it
schedules an asynchronous ``POST /hooks/engine/events`` back into the same
ASGI app the test is driving — simulating the real flow engine's webhook
round-trip without any external process.

Supports:

* ``delay_seconds`` — wait before firing the webhook (T-055 uses this to
  create mid-flight runs).
* ``fail_on_step_number`` — raise :class:`EngineError` or a transport
  exception on a specific dispatch (T-056).
* ``payload_for`` — override the node_result payload that the webhook
  reports back (used to exercise memory merging).

Test cleanup: call :meth:`aclose` in a teardown hook so the internal
``AsyncClient`` + any pending webhook tasks drain cleanly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import EngineError


class EngineEcho:
    """Stand-in for :class:`FlowEngineClient` that fires webhooks back at us."""

    def __init__(
        self,
        app: FastAPI,
        webhook_signer: Callable[[bytes], str],
        *,
        delay_seconds: float = 0.0,
        fail_on_step_number: int | None = None,
        fail_with: Exception | None = None,
        payload_for: Callable[[int, str], dict[str, Any]] | None = None,
    ) -> None:
        self._app = app
        self._sign = webhook_signer
        self._delay = delay_seconds
        self._fail_on = fail_on_step_number
        self._fail_with = fail_with or EngineError(
            "injected failure",
            engine_http_status=502,
            engine_correlation_id="corr-echo-inject",
            original_body="injected",
        )
        self._payload_for = payload_for or _default_payload
        self._step_counter = 0
        self._pending_tasks: list[asyncio.Task[None]] = []
        self._client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        self.dispatches: list[tuple[uuid.UUID, uuid.UUID, str, dict[str, Any]]] = []

    # ---- FlowEngineClient-compatible surface --------------------------------

    async def dispatch_node(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        agent_ref: str,
        node_name: str,
        node_inputs: dict[str, Any],
    ) -> str:
        self._step_counter += 1
        current_step = self._step_counter

        if self._fail_on is not None and current_step == self._fail_on:
            raise self._fail_with

        engine_run_id = f"eng-{step_id}"
        self.dispatches.append((run_id, step_id, node_name, dict(node_inputs)))

        # Schedule the webhook round-trip but return immediately so the
        # run-loop can resume its await_wake.
        task = asyncio.create_task(
            self._fire_webhook(engine_run_id, node_name, current_step),
            name=f"engine-echo-webhook-{engine_run_id}",
        )
        self._pending_tasks.append(task)
        return engine_run_id

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        await self._client.aclose()

    # ---- Internals ----------------------------------------------------------

    async def _fire_webhook(
        self,
        engine_run_id: str,
        node_name: str,
        step_number: int,
    ) -> None:
        if self._delay > 0:
            await asyncio.sleep(self._delay)

        body_obj: dict[str, Any] = {
            "eventType": "node_finished",
            "engineRunId": engine_run_id,
            "engineEventId": f"evt-{engine_run_id}",
            "occurredAt": datetime.now(UTC).isoformat(),
            "payload": {"result": self._payload_for(step_number, node_name)},
        }
        body_bytes = json.dumps(body_obj).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Engine-Signature": self._sign(body_bytes),
        }

        # The run_loop commits ``engine_run_id`` on the step *after* our
        # dispatch returns, so the first POST can race that commit and get
        # a 404 ("unknown engine_run_id").  Retry a couple of times with
        # short backoff — the real engine sees the committed row every time.
        for attempt in range(5):
            try:
                resp = await self._client.post(
                    "/hooks/engine/events", content=body_bytes, headers=headers
                )
            except httpx.HTTPError:  # pragma: no cover — best-effort
                return
            if resp.status_code != 404:
                return
            await asyncio.sleep(0.01 * (attempt + 1))


def _default_payload(step_number: int, node_name: str) -> dict[str, Any]:
    return {node_name: f"step-{step_number}-output"}
