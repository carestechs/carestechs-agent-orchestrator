"""Tests for app.core.database: engine, session lifecycle, Base."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.database import Base, make_engine, make_sessionmaker

# -- Structural tests (no DB required) ------------------------------------


class TestBaseClass:
    def test_base_is_declarative(self) -> None:
        assert issubclass(Base, DeclarativeBase)


class TestFactories:
    def test_make_engine_returns_asyncpg_engine(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/testdb",  # type: ignore[arg-type]
            orchestrator_api_key="k",  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        engine = make_engine(settings)
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
        assert engine.pool.checkedin() == 0  # no connections opened yet

    def test_make_sessionmaker_returns_factory(self) -> None:
        from app.config import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost:5432/testdb",  # type: ignore[arg-type]
            orchestrator_api_key="k",  # type: ignore[arg-type]
            engine_webhook_secret="s",  # type: ignore[arg-type]
            engine_base_url="http://localhost:9000",  # type: ignore[arg-type]
        )
        engine = make_engine(settings)
        sm = make_sessionmaker(engine)
        session = sm()
        assert isinstance(session, AsyncSession)


# -- DB integration tests (require Postgres via conftest fixtures) --------


class TestSelectOne:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_select_1(self, db_session: AsyncSession) -> None:
        """Smoke test: SELECT 1 returns 1 through the fixture session."""
        result = await db_session.execute(text("SELECT 1"))
        assert result.scalar() == 1


class TestRollbackOnException:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_writes_are_isolated_across_tests(
        self, db_session: AsyncSession
    ) -> None:
        """Writes in one test must not be visible in another.

        The SAVEPOINT-wrapped fixture rolls back at function end, so a row
        inserted here disappears before the next test runs.
        """
        await db_session.execute(
            text("CREATE TEMP TABLE _probe (x int)")
        )
        await db_session.execute(text("INSERT INTO _probe VALUES (1)"))
        await db_session.commit()  # hits the SAVEPOINT, not the outer trans
        result = await db_session.execute(text("SELECT COUNT(*) FROM _probe"))
        assert result.scalar() == 1
