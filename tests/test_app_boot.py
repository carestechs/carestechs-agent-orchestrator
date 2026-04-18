"""Tests for app.main: app factory boots correctly."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def boot_app() -> FastAPI:
    return create_app()


@pytest.fixture
def boot_client(boot_app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=boot_app), base_url="http://test")


class TestAppBoot:
    def test_create_app_returns_fastapi(self) -> None:
        application = create_app()
        assert isinstance(application, FastAPI)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_openapi_json_accessible(self, boot_client: AsyncClient) -> None:
        resp = await boot_client.get("/openapi.json")
        assert resp.status_code == 200
        body = resp.json()
        assert "paths" in body
        assert body["info"]["title"] == "carestechs-agent-orchestrator"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_docs_accessible(self, boot_client: AsyncClient) -> None:
        resp = await boot_client.get("/docs")
        assert resp.status_code == 200
