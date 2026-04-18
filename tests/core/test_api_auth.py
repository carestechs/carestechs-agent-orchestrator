"""Tests for app.core.api_auth: Bearer API-key dependency."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api_auth import require_api_key
from app.core.exceptions import AuthError, register_exception_handlers

_API_KEY = "test-api-key-12345"


# ---------------------------------------------------------------------------
# Unit tests: require_api_key raises / succeeds
# ---------------------------------------------------------------------------


class TestRequireApiKeyUnit:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_header(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
            orchestrator_api_key=_API_KEY,  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        with pytest.raises(AuthError, match="missing bearer token"):
            await require_api_key(None, settings=settings)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_malformed_header(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
            orchestrator_api_key=_API_KEY,  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        with pytest.raises(AuthError, match="missing bearer token"):
            await require_api_key("Token foo", settings=settings)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wrong_token(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
            orchestrator_api_key=_API_KEY,  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        with pytest.raises(AuthError, match="invalid api key"):
            await require_api_key("Bearer wrong-key", settings=settings)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_correct_token(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
            orchestrator_api_key=_API_KEY,  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        # Should not raise
        await require_api_key(f"Bearer {_API_KEY}", settings=settings)


# ---------------------------------------------------------------------------
# Integration: attach to a route
# ---------------------------------------------------------------------------


@pytest.fixture
def api_app() -> FastAPI:
    from app.config import Settings
    from app.core.dependencies import get_settings_dep

    app = FastAPI()
    register_exception_handlers(app)

    test_settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        orchestrator_api_key=_API_KEY,  # type: ignore[arg-type]
        engine_webhook_secret="s",  # type: ignore[arg-type]
        engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_settings_dep] = lambda: test_settings

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def _protected() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def api_client(api_app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://test",
    )


class TestRequireApiKeyIntegration:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_header_returns_401(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/protected")
        assert resp.status_code == 401
        body = resp.json()
        assert body["type"].endswith("/unauthorized")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wrong_key_returns_401(self, api_client: AsyncClient) -> None:
        resp = await api_client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_correct_key_returns_200(self, api_client: AsyncClient) -> None:
        resp = await api_client.get(
            "/protected",
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
