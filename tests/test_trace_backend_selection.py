"""Backend-selector + single-engine invariant (FEAT-013, AC-1 / AC-6).

* AC-1: ``settings.trace_backend = "postgres"`` is the production
  default; the factory returns a ``PostgresTraceStore`` for that value.
* AC-6: regardless of which backend the factory builds, only one
  ``AsyncEngine`` is ever constructed.  The Postgres backend reuses the
  app's ``async_sessionmaker`` rather than building its own pool.

The factory is cached at module level via ``_trace_store``; the
``_reset_trace_store_cache`` test hook clears it between cases.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import get_settings
from app.modules.ai.trace import (
    NoopTraceStore,
    _reset_trace_store_cache,
    get_trace_store,
)
from app.modules.ai.trace_jsonl import JsonlTraceStore
from app.modules.ai.trace_postgres import PostgresTraceStore

_REQUIRED_ENV: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/testdb",
    "ORCHESTRATOR_API_KEY": "test-api-key",
    "ENGINE_WEBHOOK_SECRET": "test-webhook-secret",
    "ENGINE_BASE_URL": "http://localhost:9000",
}


@pytest.fixture
def _env_with_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _reset() -> None:
    get_settings.cache_clear()
    _reset_trace_store_cache()


@pytest.mark.usefixtures("_env_with_required")
class TestBackendSelection:
    """The factory dispatches on ``Settings.trace_backend``."""

    def test_default_returns_postgres_store(self) -> None:
        get_settings.cache_clear()
        store = get_trace_store()
        assert isinstance(store, PostgresTraceStore)

    def test_jsonl_setting_returns_jsonl_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_BACKEND", "jsonl")
        get_settings.cache_clear()
        store = get_trace_store()
        assert isinstance(store, JsonlTraceStore)

    def test_noop_setting_returns_noop_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_BACKEND", "noop")
        get_settings.cache_clear()
        store = get_trace_store()
        assert isinstance(store, NoopTraceStore)


@pytest.mark.usefixtures("_env_with_required")
class TestSingleEngineInvariant:
    """AC-6 — only one AsyncEngine is ever constructed.

    Patch ``make_engine`` so any second construction is observable.
    """

    def test_postgres_store_reuses_existing_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force a fresh engine on first access; subsequent get_engine calls
        # MUST return the cached singleton.
        import app.core.database as core_db

        core_db._engine = None
        get_settings.cache_clear()

        with patch.object(core_db, "make_engine", wraps=core_db.make_engine) as me:
            # First touch from get_session_factory builds the engine once.
            from app.core.dependencies import (
                _reset_session_factory_cache,
                get_session_factory,
            )

            _reset_session_factory_cache()
            _ = get_session_factory()

            # Building the Postgres trace store must NOT construct a second engine.
            monkeypatch.setenv("TRACE_BACKEND", "postgres")
            get_settings.cache_clear()
            _reset_trace_store_cache()
            store = get_trace_store()
            assert isinstance(store, PostgresTraceStore)

            assert me.call_count == 1, (
                f"AsyncEngine constructed {me.call_count} times — expected 1"
            )
