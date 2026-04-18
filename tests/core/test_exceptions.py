"""Tests for app.core.exceptions: AppError hierarchy + RFC 7807 handlers."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.exceptions import (
    ALL_APP_ERRORS,
    AppError,
    AuthError,
    ConflictError,
    EngineError,
    NotFoundError,
    NotImplementedYet,
    PolicyError,
    ProviderError,
    ValidationError,
    problem_type,
    register_exception_handlers,
)

# ---------------------------------------------------------------------------
# Fixtures: a tiny FastAPI app with handlers registered
# ---------------------------------------------------------------------------


@pytest.fixture
def error_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-not-found")
    async def _raise_not_found() -> None:
        raise NotFoundError("x missing")

    @app.get("/raise-unhandled")
    async def _raise_unhandled() -> None:
        raise RuntimeError("boom")

    class Body(BaseModel):
        name: str
        age: int

    @app.post("/validate")
    async def _validate(body: Body) -> Body:
        return body

    return app


@pytest.fixture
def client(error_app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=error_app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Per-subclass mapping
# ---------------------------------------------------------------------------


_SUBCLASS_CASES = [
    (ValidationError, "validation-error", 400, "Validation error"),
    (NotFoundError, "not-found", 404, "Not found"),
    (ConflictError, "conflict", 409, "Conflict"),
    (AuthError, "unauthorized", 401, "Unauthorized"),
    (PolicyError, "policy-error", 500, "Policy error"),
    (EngineError, "engine-error", 502, "Flow engine error"),
    (ProviderError, "provider-error", 502, "LLM provider error"),
    (NotImplementedYet, "not-implemented", 501, "Not implemented"),
]


class TestAppErrorSubclasses:
    @pytest.mark.parametrize(
        ("cls", "code", "status", "title"),
        _SUBCLASS_CASES,
        ids=[e[0].__name__ for e in _SUBCLASS_CASES],
    )
    def test_class_vars(self, cls: type[AppError], code: str, status: int, title: str) -> None:
        assert cls.code == code
        assert cls.http_status == status
        assert cls.title == title

    def test_all_app_errors_list_complete(self) -> None:
        assert len(ALL_APP_ERRORS) == 8

    def test_detail_and_errors(self) -> None:
        exc = ValidationError("bad input", errors={"name": ["required"]})
        assert exc.detail == "bad input"
        assert exc.errors == {"name": ["required"]}

    def test_problem_type_uri(self) -> None:
        assert problem_type("not-found") == "https://orchestrator.local/problems/not-found"


# ---------------------------------------------------------------------------
# Handler round-trips
# ---------------------------------------------------------------------------


class TestAppErrorHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_not_found_response(self, client: AsyncClient) -> None:
        resp = await client.get("/raise-not-found")
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "https://orchestrator.local/problems/not-found"
        assert body["title"] == "Not found"
        assert body["status"] == 404
        assert body["detail"] == "x missing"
        assert "errors" not in body  # omitted when None


class TestRequestValidationErrorHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_body(self, client: AsyncClient) -> None:
        resp = await client.post("/validate", json={"name": 123})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == "https://orchestrator.local/problems/validation-error"
        assert body["status"] == 400
        assert "errors" in body
        # At least one field-level error
        assert len(body["errors"]) > 0


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_unhandled_returns_500(self, client: AsyncClient) -> None:
        resp = await client.get("/raise-unhandled")
        assert resp.status_code == 500
        body = resp.json()
        assert body["type"] == "https://orchestrator.local/problems/internal-error"
        assert body["detail"] == "internal error"
        # No traceback leak
        assert "Traceback" not in str(body)
        assert "boom" not in str(body)
