"""Tests for app.core.webhook_auth: HMAC signing, verification, dependency."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.middleware import RawBodyMiddleware
from app.core.webhook_auth import require_engine_signature, sign_body, verify_signature

_SECRET = "test-secret"


# ---------------------------------------------------------------------------
# Unit tests: sign_body / verify_signature
# ---------------------------------------------------------------------------


class TestSignBody:
    def test_format(self) -> None:
        sig = sign_body(b"hello", _SECRET)
        assert sig.startswith("sha256=")
        assert len(sig) == len("sha256=") + 64  # hex SHA-256


class TestVerifySignature:
    def test_roundtrip(self) -> None:
        body = b'{"event": "node_finished"}'
        sig = sign_body(body, _SECRET)
        assert verify_signature(body, sig, _SECRET) is True

    def test_missing_header(self) -> None:
        assert verify_signature(b"x", None, _SECRET) is False

    def test_empty_header(self) -> None:
        assert verify_signature(b"x", "", _SECRET) is False

    def test_wrong_prefix(self) -> None:
        assert verify_signature(b"x", "md5=abc", _SECRET) is False

    def test_wrong_digest(self) -> None:
        body = b"payload"
        sig = sign_body(body, _SECRET)
        # Flip a character in the hex
        bad_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        assert verify_signature(body, bad_sig, _SECRET) is False

    def test_wrong_secret(self) -> None:
        body = b"payload"
        sig = sign_body(body, _SECRET)
        assert verify_signature(body, sig, "other-secret") is False

    def test_same_length_different_content(self) -> None:
        """Constant-time compare correctness: same-length but different strings."""
        sig_a = sign_body(b"a", _SECRET)
        sig_b = sign_body(b"b", _SECRET)
        assert verify_signature(b"a", sig_b, _SECRET) is False
        assert verify_signature(b"a", sig_a, _SECRET) is True


# ---------------------------------------------------------------------------
# Integration: require_engine_signature with RawBodyMiddleware
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_app() -> FastAPI:
    from app.config import Settings
    from app.core.dependencies import get_settings_dep

    app = FastAPI()
    app.add_middleware(RawBodyMiddleware, prefix="/hooks/")

    test_settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        orchestrator_api_key="k",  # type: ignore[arg-type]
        engine_webhook_secret=_SECRET,  # type: ignore[arg-type]
        engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_settings_dep] = lambda: test_settings

    @app.post("/hooks/engine/events")
    async def _hook(
        request: Request,
        sig_ok: bool = Depends(require_engine_signature),
    ) -> dict[str, object]:
        body = await request.body()
        return {
            "signature_ok": sig_ok,
            "body_len": len(body),
            "raw_body_len": len(request.state.raw_body),
        }

    return app


@pytest.fixture
def webhook_client(webhook_app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=webhook_app),
        base_url="http://test",
    )


class TestRequireEngineSignature:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_signature(self, webhook_client: AsyncClient) -> None:
        body = b'{"event": "test"}'
        sig = sign_body(body, _SECRET)
        resp = await webhook_client.post(
            "/hooks/engine/events",
            content=body,
            headers={"x-engine-signature": sig, "content-type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["signature_ok"] is True
        assert data["body_len"] == len(body)
        assert data["raw_body_len"] == len(body)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_signature(self, webhook_client: AsyncClient) -> None:
        body = b'{"event": "test"}'
        resp = await webhook_client.post(
            "/hooks/engine/events",
            content=body,
            headers={"x-engine-signature": "sha256=bad", "content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["signature_ok"] is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_signature(self, webhook_client: AsyncClient) -> None:
        body = b'{"event": "test"}'
        resp = await webhook_client.post(
            "/hooks/engine/events",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["signature_ok"] is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_case_insensitive_header(self, webhook_client: AsyncClient) -> None:
        """Starlette normalizes headers to lowercase; PascalCase must work."""
        body = b'{"event": "test"}'
        sig = sign_body(body, _SECRET)
        resp = await webhook_client.post(
            "/hooks/engine/events",
            content=body,
            headers={"X-Engine-Signature": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["signature_ok"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_body_still_readable(self, webhook_client: AsyncClient) -> None:
        """After middleware stashes raw_body, the route can still read the body."""
        body = b'{"event": "test"}'
        sig = sign_body(body, _SECRET)
        resp = await webhook_client.post(
            "/hooks/engine/events",
            content=body,
            headers={"x-engine-signature": sig, "content-type": "application/json"},
        )
        data = resp.json()
        assert data["body_len"] == data["raw_body_len"]
