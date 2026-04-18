"""Async SQLAlchemy engine, sessionmaker, and declarative Base."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Declarative base — single metadata for all models + Alembic
# ---------------------------------------------------------------------------


class Base(AsyncAttrs, DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def make_engine(settings: Settings) -> AsyncEngine:
    """Create an ``AsyncEngine`` from application settings."""
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        echo=False,
    )


# ---------------------------------------------------------------------------
# Lazy engine singleton — never built at import time
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the singleton engine, creating it lazily on first call."""
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings())
    return _engine


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an ``async_sessionmaker`` bound to *engine*."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` with commit/rollback/close semantics.

    Routes inject via ``Annotated[AsyncSession, Depends(get_db_session)]``.
    Tests override via ``app.dependency_overrides[get_db_session]``.
    """
    session = make_sessionmaker(get_engine())()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
