"""HTTP client for the flow-engine lifecycle surface (FEAT-006 rc2).

Wraps the four endpoints FEAT-006 needs:

- ``POST /api/auth/token`` — exchange API key for a JWT.
- ``POST /api/workflows`` — register a workflow (states + transitions).
- ``POST /api/workflows/{id}/items`` — create an item inside a workflow.
- ``POST /api/items/{id}/transitions`` — transition an item's state.
- ``POST /api/webhook-subscriptions`` — subscribe to state-change events.

JWT is cached with an ``asyncio.Lock`` guarding refresh; 401 triggers a
transparent re-auth + single retry.  5xx / connection / timeout errors use
bounded exponential backoff (500 ms → 4 s, ~15% jitter, capped at 3
attempts).  4xx responses raise :class:`EngineError` with body preserved.

Correlation-ID convention: the engine's ``/transitions`` body takes a
``comment`` and the engine's webhook emits ``triggeredBy``.  Since the
webhook does NOT carry ``comment``, we encode the correlation UUID into
``triggeredBy`` via a ``"orchestrator:<uuid>"`` prefix so the reactor can
parse it back out.  Actor / user info is logged alongside via the
``comment`` field (which is archived on the engine side).
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.exceptions import EngineError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 5.0
_TOKEN_REFRESH_MARGIN = timedelta(seconds=30)


@dataclass
class _TokenCache:
    access_token: str | None = None
    expires_at: datetime | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_fresh(self) -> bool:
        if self.access_token is None or self.expires_at is None:
            return False
        return datetime.now(UTC) + _TOKEN_REFRESH_MARGIN < self.expires_at


class FlowEngineLifecycleClient:
    """Thin async client for the flow-engine lifecycle surface."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
        )
        self._token = _TokenCache()
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._token.lock:
            if not force and self._token.is_fresh():
                assert self._token.access_token is not None
                return self._token.access_token
            resp = await self._client.post(
                "/api/auth/token",
                json={"apiKey": self._api_key},
            )
            if resp.status_code != 200:
                raise EngineError(
                    f"flow-engine auth failed: {resp.status_code} {resp.text}",
                    engine_http_status=resp.status_code,
                    original_body=resp.text,
                )
            payload: dict[str, Any] = resp.json()
            data: dict[str, Any] = payload.get("data") or {}
            token = data.get("accessToken")
            expires_at_raw = data.get("expiresAt")
            if not token or not expires_at_raw:
                raise EngineError(
                    "flow-engine auth response missing accessToken / expiresAt",
                    engine_http_status=resp.status_code,
                    original_body=resp.text,
                )
            self._token.access_token = token
            self._token.expires_at = _parse_iso(expires_at_raw)
            return token

    # ------------------------------------------------------------------
    # Request helper with retries + transparent re-auth
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        reauthed = False
        for attempt in range(self._max_retries):
            try:
                token = await self._ensure_token()
                resp = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
                last_exc = exc
                await self._backoff(attempt)
                continue

            if resp.status_code == 401 and not reauthed:
                reauthed = True
                await self._ensure_token(force=True)
                continue

            if 500 <= resp.status_code < 600:
                last_exc = EngineError(
                    f"engine 5xx: {resp.status_code} {resp.text}",
                    engine_http_status=resp.status_code,
                    original_body=resp.text,
                )
                await self._backoff(attempt)
                continue

            return resp

        assert last_exc is not None
        if isinstance(last_exc, EngineError):
            raise last_exc
        raise EngineError(
            f"engine call failed after {self._max_retries} attempts: {last_exc!r}",
        )

    async def _backoff(self, attempt: int) -> None:
        delay = min(
            _BASE_BACKOFF_SECONDS * (2**attempt),
            _MAX_BACKOFF_SECONDS,
        )
        # ±15% jitter
        jitter = delay * (random.random() * 0.3 - 0.15)
        await asyncio.sleep(max(0.0, delay + jitter))

    # ------------------------------------------------------------------
    # Workflow endpoints
    # ------------------------------------------------------------------

    async def create_workflow(
        self,
        *,
        name: str,
        statuses: list[dict[str, Any]],
        transitions: list[dict[str, Any]],
        initial_status: str,
        description: str | None = None,
    ) -> uuid.UUID:
        body = {
            "name": name,
            "description": description,
            "statuses": statuses,
            "transitions": transitions,
            "initialStatus": initial_status,
        }
        resp = await self._request("POST", "/api/workflows", json=body)
        if resp.status_code == 409:
            raise EngineError(
                "workflow already exists",
                engine_http_status=409,
                original_body=resp.text,
            )
        if resp.status_code != 201:
            _raise_engine_error(resp, where="create_workflow")
        payload: dict[str, Any] = resp.json()
        data: dict[str, Any] = payload.get("data") or {}
        return uuid.UUID(str(data["id"]))

    async def get_workflow_by_name(self, name: str) -> uuid.UUID | None:
        resp = await self._request(
            "GET", "/api/workflows", params={"name": name}
        )
        if resp.status_code != 200:
            _raise_engine_error(resp, where="get_workflow_by_name")
        payload: dict[str, Any] = resp.json() or {}
        data: list[dict[str, Any]] = payload.get("data") or []
        for entry in data:
            if entry.get("name") == name:
                return uuid.UUID(str(entry["id"]))
        return None

    async def create_item(
        self,
        *,
        workflow_id: uuid.UUID,
        title: str,
        external_ref: str,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        body = {
            "title": title,
            "externalRef": external_ref,
            "metadata": metadata or {},
        }
        resp = await self._request(
            "POST",
            f"/api/workflows/{workflow_id}/items",
            json=body,
        )
        if resp.status_code != 201:
            _raise_engine_error(resp, where="create_item")
        payload: dict[str, Any] = resp.json()
        data: dict[str, Any] = payload.get("data") or {}
        return uuid.UUID(str(data["id"]))

    async def transition_item(
        self,
        *,
        item_id: uuid.UUID,
        to_status: str,
        correlation_id: uuid.UUID,
        actor: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Transition an item.  Encodes ``correlation_id`` into the
        comment using ``orchestrator-corr:<uuid>`` so the reactor can parse
        it from the emitted webhook's ``triggeredBy``.
        """
        comment_suffix = f"[actor={actor}]" if actor else ""
        encoded = f"orchestrator-corr:{correlation_id}"
        full_comment = " ".join(filter(None, [encoded, comment, comment_suffix]))
        resp = await self._request(
            "POST",
            f"/api/items/{item_id}/transitions",
            json={"toStatus": to_status, "comment": full_comment},
        )
        if resp.status_code != 200:
            _raise_engine_error(resp, where="transition_item")
        payload: dict[str, Any] = resp.json()
        data: dict[str, Any] = payload.get("data") or {}
        return data

    async def ensure_webhook_subscription(
        self,
        *,
        url: str,
        event_type: str,
        workflow_id: uuid.UUID | None,
        secret: str,
    ) -> uuid.UUID:
        body = {
            "url": url,
            "eventType": event_type,
            "workflowId": str(workflow_id) if workflow_id else None,
            "secret": secret,
        }
        resp = await self._request(
            "POST", "/api/webhook-subscriptions", json=body
        )
        if resp.status_code not in (200, 201):
            _raise_engine_error(resp, where="ensure_webhook_subscription")
        payload: dict[str, Any] = resp.json()
        data: dict[str, Any] = payload.get("data") or {}
        return uuid.UUID(str(data["id"]))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_iso(raw: str) -> datetime:
    # Engine returns ISO-8601 with Z suffix.  ``datetime.fromisoformat`` in
    # 3.11+ handles both ``Z`` and ``+00:00`` variants.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _raise_engine_error(resp: httpx.Response, *, where: str) -> None:
    raise EngineError(
        f"{where}: {resp.status_code} {resp.text}",
        engine_http_status=resp.status_code,
        original_body=resp.text,
    )


def extract_correlation_id(triggered_by: str | None) -> uuid.UUID | None:
    """Parse a correlation id out of the engine webhook's ``triggeredBy``.

    Mirrors the ``orchestrator-corr:<uuid>`` encoding used by
    :meth:`FlowEngineLifecycleClient.transition_item`.  Returns None if
    the prefix is absent or the UUID is malformed.
    """
    if not triggered_by:
        return None
    prefix = "orchestrator-corr:"
    idx = triggered_by.find(prefix)
    if idx < 0:
        return None
    tail = triggered_by[idx + len(prefix) :].strip()
    token = tail.split()[0] if tail else ""
    try:
        return uuid.UUID(token)
    except ValueError:
        return None
