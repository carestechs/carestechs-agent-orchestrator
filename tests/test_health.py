"""Integration tests for the /health endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.modules.ai.dependencies import get_engine_client
from app.modules.ai.engine_client import FlowEngineClient


class _StubEngine:
    """Test double for ``FlowEngineClient.health()``."""

    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    async def health(self) -> bool:
        return self._ok

    async def aclose(self) -> None: ...


def _override_engine(app: FastAPI, *, ok: bool) -> None:
    def _factory() -> FlowEngineClient:
        return _StubEngine(ok=ok)  # type: ignore[return-value]

    app.dependency_overrides[get_engine_client] = _factory


class TestHealthHappy:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_200_envelope(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        _override_engine(app, ok=True)
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["status"] == "ok"
        checks = body["data"]["checks"]
        assert checks["database"] == "ok"
        assert checks["llm_provider"] == "ok"
        assert checks["flow_engine"] == "ok"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_auth_required(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        _override_engine(app, ok=True)
        resp = await client.get("/health")  # no Authorization header
        assert resp.status_code == 200


class TestHealthDegraded:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_engine_down_reports_degraded(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        _override_engine(app, ok=False)
        resp = await client.get("/health")
        assert resp.status_code == 200  # /health never 5xxs
        body = resp.json()
        assert body["data"]["status"] == "degraded"
        assert body["data"]["checks"]["flow_engine"] == "down"
