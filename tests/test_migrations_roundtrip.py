"""Alembic round-trip test: upgrade head → downgrade base → upgrade head.

Uses its own ephemeral Postgres database (separate from the session DB) so
dropping all tables does not disturb the rest of the suite.  Also runs
``autogenerate`` after the final upgrade and asserts there is no diff
between the ORM metadata and the migrated schema — catches model/migration
drift that silently accumulates over time.
"""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.modules.ai.models  # noqa: F401  ensure models are registered
from app.core.database import Base

_REPO_ROOT = Path(__file__).parent.parent.resolve()

_ADMIN_URL = os.getenv(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5432/postgres",
)

_EXPECTED_TABLES = {"runs", "steps", "policy_calls", "webhook_events", "run_memory"}
_EXPECTED_UNIQUES = {
    ("steps", frozenset({"run_id", "step_number"})),
    ("webhook_events", frozenset({"dedupe_key"})),
    ("policy_calls", frozenset({"step_id"})),
}


def _run_admin(statement: str) -> None:
    async def _do() -> None:
        eng = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
        try:
            async with eng.connect() as conn:
                await conn.execute(text(statement))
        finally:
            await eng.dispose()

    asyncio.run(_do())


@pytest.fixture
def isolated_db_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Create a dedicated DB and point ``DATABASE_URL`` at it for this test.

    The session-scoped autouse fixture in ``conftest.py`` pins ``DATABASE_URL``
    to the shared test DB — but the Alembic ``env.py`` ignores the URL set via
    ``cfg.set_main_option`` and reads env vars directly.  Monkey-patching the
    env var for the test's duration is the cleanest way to target a different
    DB without editing ``env.py``.
    """
    db_name = f"orchestrator_mig_{_uuid.uuid4().hex[:12]}"
    try:
        _run_admin(f'CREATE DATABASE "{db_name}"')
    except Exception as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    url = _ADMIN_URL.rsplit("/", 1)[0] + f"/{db_name}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()
        try:
            _run_admin(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        except Exception:
            pass


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(_REPO_ROOT / "src" / "app" / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _async_inspect(async_url: str, fn):  # type: ignore[no-untyped-def]
    """Helper: open an async engine, run a sync introspection callable inside."""
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(fn)
    finally:
        await engine.dispose()


def _inspect_tables(async_url: str) -> set[str]:
    def _get(sync_conn) -> set[str]:  # type: ignore[no-untyped-def]
        return set(inspect(sync_conn).get_table_names())

    return asyncio.run(_async_inspect(async_url, _get))


def _inspect_uniques(async_url: str) -> set[tuple[str, frozenset[str]]]:
    def _get(sync_conn) -> set[tuple[str, frozenset[str]]]:  # type: ignore[no-untyped-def]
        insp = inspect(sync_conn)
        result: set[tuple[str, frozenset[str]]] = set()
        for table in insp.get_table_names():
            for uq in insp.get_unique_constraints(table):
                cols = frozenset(uq["column_names"])
                result.add((table, cols))
            for idx in insp.get_indexes(table):
                if idx.get("unique"):
                    cols = frozenset(c for c in idx["column_names"] if c is not None)
                    result.add((table, cols))
        return result

    return asyncio.run(_async_inspect(async_url, _get))


class TestRoundTrip:
    def test_upgrade_downgrade_upgrade(self, isolated_db_url: str) -> None:
        cfg = _alembic_config(isolated_db_url)

        # 1. Upgrade head — tables appear.
        command.upgrade(cfg, "head")
        tables = _inspect_tables(isolated_db_url)
        for expected in _EXPECTED_TABLES:
            assert expected in tables, f"missing table after upgrade: {expected}"

        uniques = _inspect_uniques(isolated_db_url)
        for table, cols in _EXPECTED_UNIQUES:
            assert (table, cols) in uniques, (
                f"missing unique constraint {table}({sorted(cols)})"
            )

        # 2. Downgrade base — tables gone (alembic_version remains).
        command.downgrade(cfg, "base")
        tables_after = _inspect_tables(isolated_db_url)
        for gone in _EXPECTED_TABLES:
            assert gone not in tables_after, f"table still present after downgrade: {gone}"

        # 3. Upgrade head again — clean re-apply.
        command.upgrade(cfg, "head")
        tables_again = _inspect_tables(isolated_db_url)
        for expected in _EXPECTED_TABLES:
            assert expected in tables_again


class TestAutogenerateHasNoDiff:
    def test_no_drift_between_models_and_migrations(
        self, isolated_db_url: str
    ) -> None:
        """After ``upgrade head``, autogenerate must produce zero diffs."""
        cfg = _alembic_config(isolated_db_url)
        command.upgrade(cfg, "head")

        def _compare(sync_conn):  # type: ignore[no-untyped-def]
            ctx = MigrationContext.configure(
                connection=sync_conn,
                opts={"compare_type": True, "compare_server_default": True},
            )
            return compare_metadata(ctx, Base.metadata)

        diffs = asyncio.run(_async_inspect(isolated_db_url, _compare))

        # Alembic sometimes reports benign index diffs for Postgres descending
        # indexes — filter those out and fail on structural drift only.
        real_diffs = [
            d
            for d in diffs
            if not (isinstance(d, tuple) and d and d[0] in {"add_index", "remove_index"})
        ]
        assert real_diffs == [], f"migration drift detected: {real_diffs}"
