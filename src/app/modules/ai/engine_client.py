"""Typed httpx wrapper for communication with the carestechs-flow-engine."""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

import httpx

from app.config import Settings
from app.core.exceptions import EngineError

logger = logging.getLogger(__name__)


class FlowEngineClient:
    """HTTP client for the carestechs-flow-engine.

    Wraps ``httpx.AsyncClient`` with base URL and auth header derived from
    :class:`~app.config.Settings`.  Every ``httpx`` exception that escapes a
    public method is converted into an :class:`~app.core.exceptions.EngineError`
    so that callers never see raw transport errors.
    """

    def __init__(self, settings: Settings) -> None:
        headers: dict[str, str] = {}
        if settings.engine_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.engine_api_key.get_secret_value()}"

        self._client = httpx.AsyncClient(
            base_url=str(settings.engine_base_url),
            headers=headers,
            timeout=httpx.Timeout(30.0),
        )
        self._dispatch_timeout = settings.engine_dispatch_timeout_seconds
        self._public_base_url = str(settings.public_base_url).rstrip("/")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Shut down the underlying transport."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Return ``True`` when the engine answers with a 2xx on ``/health``.

        **Never raises.**  Any non-2xx status or connection-level error yields
        ``False``.
        """
        try:
            response = await self._client.get("/health")
            return response.is_success
        except Exception:
            logger.debug("Engine health check failed", exc_info=True)
            return False

    async def dispatch_node(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        agent_ref: str,
        node_name: str,
        node_inputs: dict[str, Any],
    ) -> str:
        """POST to ``/nodes/dispatch`` and return the engine's ``engineRunId``.

        Wraps all transport / status errors in :class:`EngineError` with
        correlation metadata.  Raises if the response lacks ``engineRunId``.
        """
        payload = {
            "agentRef": agent_ref,
            "runId": str(run_id),
            "stepId": str(step_id),
            "nodeName": node_name,
            "nodeInputs": node_inputs,
            "callbackUrl": f"{self._public_base_url}/hooks/engine/events",
        }
        response = await self._request(
            "POST", "/nodes/dispatch", json=payload, timeout=self._dispatch_timeout
        )

        try:
            body: Any = response.json()
        except ValueError as exc:
            raise EngineError(
                detail="engine response was not JSON",
                engine_http_status=response.status_code,
                engine_correlation_id=response.headers.get("x-correlation-id"),
                original_body=response.text,
            ) from exc

        engine_run_id: Any = None
        if isinstance(body, dict):
            engine_run_id = cast("dict[str, Any]", body).get("engineRunId")
        if not isinstance(engine_run_id, str) or not engine_run_id:
            raise EngineError(
                detail="engine response missing engineRunId",
                engine_http_status=response.status_code,
                engine_correlation_id=response.headers.get("x-correlation-id"),
                original_body=response.text,
            )
        return engine_run_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_httpx_error(exc: httpx.HTTPStatusError) -> EngineError:
        """Convert an ``httpx.HTTPStatusError`` into an ``EngineError``."""
        correlation_id: str | None = exc.response.headers.get("x-correlation-id")
        body = exc.response.text
        return EngineError(
            detail=f"Flow engine returned HTTP {exc.response.status_code}",
            engine_http_status=exc.response.status_code,
            engine_correlation_id=correlation_id,
            original_body=body,
        )

    @staticmethod
    def _wrap_request_error(exc: httpx.RequestError) -> EngineError:
        """Convert an ``httpx.RequestError`` into an ``EngineError``."""
        return EngineError(
            detail=f"Flow engine request failed: {exc}",
            engine_http_status=None,
            engine_correlation_id=None,
            original_body=None,
        )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request; wrap transport/status errors in ``EngineError``.

        This is the single choke-point for all engine HTTP calls that should
        raise on failure (everything except ``health()``).
        """
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._wrap_httpx_error(exc) from exc
        except httpx.RequestError as exc:
            raise self._wrap_request_error(exc) from exc
        return response
