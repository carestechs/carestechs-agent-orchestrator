"""Shared FastAPI dependencies: db session, auth, settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.core.database import get_engine, make_sessionmaker
from app.core.llm import LLMProvider, get_llm_provider
from app.modules.ai.supervisor import RunSupervisor


def get_settings_dep() -> Settings:
    """FastAPI dependency returning the cached ``Settings`` singleton.

    Override in tests via ``app.dependency_overrides[get_settings_dep]``.
    """
    return get_settings()


_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached module-level ``async_sessionmaker``.

    The runtime loop opens one :class:`AsyncSession` per iteration via this
    factory so each iteration owns its own transaction lifecycle (per
    CLAUDE.md).  Tests override via ``app.dependency_overrides``.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = make_sessionmaker(get_engine())
    return _session_factory


def _reset_session_factory_cache() -> None:
    """Test hook — drop the cached factory so the next call rebuilds it."""
    global _session_factory
    _session_factory = None


def get_llm_provider_dep(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LLMProvider:
    """FastAPI dependency returning the configured ``LLMProvider``.

    Override in tests via ``app.dependency_overrides[get_llm_provider_dep]``.
    """
    return get_llm_provider(settings)


_default_supervisor: RunSupervisor | None = None


def get_supervisor(request: Request) -> RunSupervisor:
    """Return the process-wide :class:`RunSupervisor` singleton.

    Prefers the one bound in ``request.app.state.supervisor`` by the app
    lifespan (T-045).  Falls back to a module-level singleton for tests
    that skip lifespan wiring.
    """
    state_supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(state_supervisor, RunSupervisor):
        return state_supervisor

    global _default_supervisor
    if _default_supervisor is None:
        _default_supervisor = RunSupervisor()
    return _default_supervisor


def _reset_supervisor_cache() -> None:
    """Test hook — drop the module singleton so tests start fresh."""
    global _default_supervisor
    _default_supervisor = None


__all__ = [
    "_reset_session_factory_cache",
    "_reset_supervisor_cache",
    "get_llm_provider_dep",
    "get_session_factory",
    "get_settings_dep",
    "get_supervisor",
]
