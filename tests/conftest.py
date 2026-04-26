"""Pytest fixtures for the orchestrator test suite.

Provides:

- Session-scoped DB: creates a unique ``orchestrator_test_<uuid>`` database,
  runs Alembic migrations once, drops the database at session end.
- Function-scoped ``db_session``: SAVEPOINT-wrapped AsyncSession that is
  rolled back at test end — no inter-test leakage even when the code under
  test calls ``session.commit()``.
- Function-scoped ``app`` + ``client``: FastAPI application with
  ``get_db_session`` overridden, plus an ``httpx.AsyncClient`` using the
  ASGI transport.
- ``webhook_signer``: HMAC-SHA256 helper bound to the test secret.
- ``stub_policy_factory``: builds scripted ``StubLLMProvider`` instances.
- ``--run-live`` flag: opt-in for tests marked ``@pytest.mark.live``.

Real Postgres is required — SQLite is not a substitute per CLAUDE.md. If
Postgres is unreachable, the whole session is skipped gracefully.
"""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.llm import StubLLMProvider, ToolDefinition
from app.core.webhook_auth import sign_body

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.resolve()

API_KEY = "test-api-key"
WEBHOOK_SECRET = "test-webhook-secret"
ENGINE_BASE_URL = "http://engine.test"

_ADMIN_URL = os.getenv(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5432/postgres",
)
_PG_HOST = os.getenv("TEST_DATABASE_HOST", "127.0.0.1")
_PG_PORT = os.getenv("TEST_DATABASE_PORT", "5432")
_PG_USER = os.getenv("TEST_DATABASE_USER", "orchestrator")
_PG_PASSWORD = os.getenv("TEST_DATABASE_PASSWORD", "orchestrator")


# ---------------------------------------------------------------------------
# --run-live flag
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (hits real LLM/engine).",
    )
    parser.addoption(
        "--run-requires-engine",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.requires_engine (hits a running flow-engine).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_engine: opt-in tests that require a running carestechs-flow-engine",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_live = pytest.mark.skip(reason="live test: opt in with --run-live")
    skip_engine = pytest.mark.skip(reason="requires_engine: opt in with --run-requires-engine")
    run_live = config.getoption("--run-live")
    run_engine = config.getoption("--run-requires-engine")
    for item in items:
        if "live" in item.keywords and not run_live:
            item.add_marker(skip_live)
        if "requires_engine" in item.keywords and not run_engine:
            item.add_marker(skip_engine)


# ---------------------------------------------------------------------------
# Session-scoped: unique DB + migrations + env vars
# ---------------------------------------------------------------------------


def _run_admin_sql(statement: str) -> None:
    async def _do() -> None:
        eng = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
        try:
            async with eng.connect() as conn:
                await conn.execute(text(statement))
        finally:
            await eng.dispose()

    asyncio.run(_do())


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Create a unique Postgres database for this test session.

    The database is dropped at session teardown regardless of test outcome.
    Skips the whole session if Postgres is unreachable.
    """
    db_name = f"orchestrator_test_{_uuid.uuid4().hex[:12]}"

    try:
        _run_admin_sql(f'CREATE DATABASE "{db_name}"')
    except Exception as exc:
        pytest.skip(f"Postgres unavailable at {_ADMIN_URL}: {exc}")

    url = f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{db_name}"
    try:
        yield url
    finally:
        try:
            _run_admin_sql(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        except Exception:
            pass


# Env vars that the dev ``.env`` commonly sets and that can otherwise leak
# into test ``Settings()`` constructions, masking the values the test
# infrastructure intends. Cleared in ``_test_env`` before any other fixture
# runs.
_LEAKING_ENV_VARS = (
    "LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MAX_TOKENS",
    "ANTHROPIC_TIMEOUT_SECONDS",
    "ENGINE_API_KEY",
    "GITHUB_REPO",
    "GITHUB_PAT",
    "GITHUB_APP_ID",
    "GITHUB_PRIVATE_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "FLOW_ENGINE_LIFECYCLE_BASE_URL",
    "FLOW_ENGINE_TENANT_API_KEY",
    "PUBLIC_BASE_URL",
    "TRACE_BACKEND",
    "TRACE_DIR",
    "AGENTS_DIR",
    "REPO_ROOT",
    "LOG_LEVEL",
    "LIFECYCLE_MAX_CORRECTIONS",
)


@pytest.fixture(scope="session", autouse=True)
def _test_env(test_database_url: str) -> Iterator[None]:
    """Populate env vars + isolate ``Settings()`` from the developer's ``.env``.

    Without ``env_file=None`` the dev ``.env`` overrides any value the test
    infrastructure does not explicitly set — e.g. ``LLM_MODEL`` /
    ``ANTHROPIC_API_KEY`` leak into ``test_config``, and ``SOLO_DEV_MODE``
    leaks into FEAT-006/007 e2e tests. Disabling the env file at the class
    level for the test session is the smallest fix that restores isolation.
    """
    prior: dict[str, str | None] = {}
    overrides = {
        "DATABASE_URL": test_database_url,
        "ORCHESTRATOR_API_KEY": API_KEY,
        "ENGINE_WEBHOOK_SECRET": WEBHOOK_SECRET,
        # FEAT-009 / T-216 — executor webhook secret for /hooks/executors/*.
        "EXECUTOR_DISPATCH_SECRET": WEBHOOK_SECRET,
        "ENGINE_BASE_URL": ENGINE_BASE_URL,
        "LLM_PROVIDER": "stub",
        # Solo mode is the default for integration tests — every signal in
        # FEAT-006 e2e is performed by the same admin actor, so peer-review
        # gating MUST be disabled by default. Test files that exercise the
        # peer-review path can override per-test.
        "SOLO_DEV_MODE": "true",
    }
    for key, value in overrides.items():
        prior[key] = os.environ.get(key)
        os.environ[key] = value
    for key in _LEAKING_ENV_VARS:
        prior[key] = os.environ.get(key)
        os.environ.pop(key, None)
    prior_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
    try:
        yield
    finally:
        Settings.model_config["env_file"] = prior_env_file
        for key, original in prior.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated(test_database_url: str, _test_env: None) -> None:
    """Run Alembic ``upgrade head`` once per session against the test DB."""
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "src" / "app" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# Function-scoped: engine, SAVEPOINT-wrapped session, app, client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def engine(migrated: None, test_database_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(test_database_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose writes are rolled back at function end.

    Uses the SQLAlchemy ``join to external transaction`` recipe: the fixture
    opens a connection + outer transaction, binds the session to that
    connection, and starts a SAVEPOINT. A listener re-opens the SAVEPOINT
    each time the code under test calls ``session.commit()`` — so any
    commits inside the test are local to the SAVEPOINT and disappear on the
    outer rollback.
    """
    async with engine.connect() as conn:
        outer_trans = await conn.begin()
        factory = async_sessionmaker(bind=conn, expire_on_commit=False)
        session = factory()
        await session.begin_nested()

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sess: Any, transaction: Any) -> None:
            if transaction.nested and not transaction._parent.nested:
                sess.begin_nested()

        try:
            yield session
        finally:
            await session.close()
            if outer_trans.is_active:
                await outer_trans.rollback()


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    """Build a fresh FastAPI app with ``get_db_session`` overridden."""
    from app.main import create_app

    application = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _override
    return application


@pytest_asyncio.fixture(loop_scope="function")
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def api_key() -> str:
    return API_KEY


@pytest.fixture
def webhook_secret() -> str:
    return WEBHOOK_SECRET


@pytest.fixture
def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def webhook_signer(webhook_secret: str) -> Callable[[bytes], str]:
    """Return a function that signs a body with the test webhook secret."""

    def _sign(body: bytes) -> str:
        return sign_body(body, webhook_secret)

    return _sign


@pytest.fixture(scope="session")
def fake_rsa_pem() -> str:
    """Generate a throwaway 2048-bit RSA PEM key (FEAT-007 tests).

    Shared across ``tests/core`` and ``tests/modules/ai/github`` so tests
    that exercise App-auth never touch a real GitHub credential.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture
def stub_policy_factory() -> Callable[..., StubLLMProvider]:
    """Factory for scripted ``StubLLMProvider`` instances.

    Usage in a test::

        policy = stub_policy_factory([("my_tool", {"arg": 1})])
    """

    def _factory(
        script: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> StubLLMProvider:
        return StubLLMProvider(script or [])

    return _factory


__all__ = [
    "API_KEY",
    "ENGINE_BASE_URL",
    "WEBHOOK_SECRET",
    "ToolDefinition",
]
