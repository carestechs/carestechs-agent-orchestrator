"""FastAPI app factory and module-level ``app``."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.exceptions import register_exception_handlers
from app.core.middleware import RawBodyMiddleware
from app.health import router as health_router
from app.lifespan import lifespan
from app.modules.ai.router import api_router, hooks_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    # -- Logging (best-effort; settings may not be available in test/CLI) ---
    try:
        from app.config import get_settings
        from app.core.logging import configure_logging

        configure_logging(get_settings().log_level)
    except Exception:
        pass  # Falls back to default logging; acceptable during tests/CLI

    application = FastAPI(
        title="carestechs-agent-orchestrator",
        description="Agent-driven orchestration layer on top of carestechs-flow-engine.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # -- Exception handlers (RFC 7807) -------------------------------------
    register_exception_handlers(application)

    # -- Routers -----------------------------------------------------------
    application.include_router(health_router)
    application.include_router(api_router)
    application.include_router(hooks_router)

    # -- Middleware ---------------------------------------------------------
    application.add_middleware(RawBodyMiddleware, prefix="/hooks/")

    return application


app = create_app()
