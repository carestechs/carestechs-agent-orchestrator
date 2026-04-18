"""Typed exception hierarchy and RFC 7807 Problem Details handler."""

from __future__ import annotations

import logging
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Problem-type URI builder
# ---------------------------------------------------------------------------

_PROBLEM_BASE = "https://orchestrator.local/problems"


def problem_type(code: str) -> str:
    """Return the canonical RFC 7807 ``type`` URI for *code*."""
    return f"{_PROBLEM_BASE}/{code}"


# ---------------------------------------------------------------------------
# AppError hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base for all application-level errors.

    Subclasses set *code*, *http_status*, and *title* as ``ClassVar`` fields.
    The global exception handler converts these into RFC 7807 responses.
    """

    code: ClassVar[str]
    http_status: ClassVar[int]
    title: ClassVar[str]

    def __init__(self, detail: str, *, errors: dict[str, list[str]] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, list[str]] = errors or {}


class ValidationError(AppError):
    code = "validation-error"
    http_status = 400
    title = "Validation error"


class NotFoundError(AppError):
    code = "not-found"
    http_status = 404
    title = "Not found"


class ConflictError(AppError):
    code = "conflict"
    http_status = 409
    title = "Conflict"


class AuthError(AppError):
    code = "unauthorized"
    http_status = 401
    title = "Unauthorized"


class PolicyError(AppError):
    code = "policy-error"
    http_status = 500
    title = "Policy error"


class EngineError(AppError):
    code = "engine-error"
    http_status = 502
    title = "Flow engine error"

    def __init__(
        self,
        detail: str,
        *,
        errors: dict[str, list[str]] | None = None,
        engine_http_status: int | None = None,
        engine_correlation_id: str | None = None,
        original_body: str | None = None,
    ) -> None:
        super().__init__(detail, errors=errors)
        self.engine_http_status = engine_http_status
        self.engine_correlation_id = engine_correlation_id
        self.original_body = original_body


class ProviderError(AppError):
    code = "provider-error"
    http_status = 502
    title = "LLM provider error"

    def __init__(
        self,
        detail: str,
        *,
        errors: dict[str, list[str]] | None = None,
        provider_http_status: int | None = None,
        provider_request_id: str | None = None,
        original_body: str | None = None,
    ) -> None:
        super().__init__(detail, errors=errors)
        self.provider_http_status = provider_http_status
        self.provider_request_id = provider_request_id
        self.original_body = original_body


class NotImplementedYet(AppError):
    code = "not-implemented"
    http_status = 501
    title = "Not implemented"


# ---------------------------------------------------------------------------
# Problem Details Pydantic schema
# ---------------------------------------------------------------------------


class ProblemDetails(BaseModel):
    """RFC 7807 Problem Details response body."""

    type: str
    title: str
    status: int
    detail: str
    errors: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

_PROBLEM_MEDIA_TYPE = "application/problem+json"


async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    body = ProblemDetails(
        type=problem_type(exc.code),
        title=exc.title,
        status=exc.http_status,
        detail=exc.detail,
        errors=exc.errors or None,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=body.model_dump(exclude_none=True),
        media_type=_PROBLEM_MEDIA_TYPE,
    )


async def _request_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    field_errors: dict[str, list[str]] = {}
    for err in exc.errors():
        loc_parts: list[str] = []
        for part in err.get("loc", ()):
            # Skip the leading "body" / "query" / "path" sentinel
            if isinstance(part, str) and part in {"body", "query", "path", "header", "cookie"}:
                continue
            loc_parts.append(str(part))
        key = ".".join(loc_parts) if loc_parts else "_root_"
        field_errors.setdefault(key, []).append(str(err.get("msg", "invalid")))

    body = ProblemDetails(
        type=problem_type("validation-error"),
        title="Validation error",
        status=400,
        detail="Request validation failed",
        errors=field_errors or None,
    )
    return JSONResponse(
        status_code=400,
        content=body.model_dump(exclude_none=True),
        media_type=_PROBLEM_MEDIA_TYPE,
    )


class _CatchAllMiddleware:
    """ASGI middleware that catches any unhandled exception and returns
    an RFC 7807 Problem Details 500 response.

    Must be the outermost middleware so it wraps Starlette's
    ``ServerErrorMiddleware`` which otherwise re-raises in debug mode.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            logger.error("Unhandled exception", exc_info=exc)
            response = JSONResponse(
                status_code=500,
                content=ProblemDetails(
                    type=problem_type("internal-error"),
                    title="Internal error",
                    status=500,
                    detail="internal error",
                ).model_dump(exclude_none=True),
                media_type=_PROBLEM_MEDIA_TYPE,
            )
            await response(scope, receive, send)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all global exception handlers to *app*."""
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _request_validation_error_handler)  # type: ignore[arg-type]
    # Outermost ASGI wrapper — catches anything Starlette's ServerErrorMiddleware re-raises.
    app.add_middleware(_CatchAllMiddleware)


# ---------------------------------------------------------------------------
# Convenience: collect all AppError subclasses for parameterized tests
# ---------------------------------------------------------------------------

ALL_APP_ERRORS: list[type[AppError]] = [
    ValidationError,
    NotFoundError,
    ConflictError,
    AuthError,
    PolicyError,
    EngineError,
    ProviderError,
    NotImplementedYet,
]
