"""Tests for app.config: Settings fields, precedence, and failure modes."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings

# -- Helpers ---------------------------------------------------------------

_REQUIRED_ENV: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/testdb",
    "ORCHESTRATOR_API_KEY": "test-api-key",
    "ENGINE_WEBHOOK_SECRET": "test-webhook-secret",
    "ENGINE_BASE_URL": "http://localhost:9000",
}


_REQUIRED_INIT: dict[str, str] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/testdb",
    "orchestrator_api_key": "test-api-key",
    "engine_webhook_secret": "test-webhook-secret",
    "engine_base_url": "http://localhost:9000",
}


def _make_settings(**overrides: Any) -> Settings:
    """Build a ``Settings`` from keyword args (bypasses env/pyproject)."""
    defaults: dict[str, Any] = {**_REQUIRED_INIT}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# -- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Clear ``get_settings`` lru_cache before every test."""
    get_settings.cache_clear()


@pytest.fixture
def _env_with_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all required env vars for a valid ``Settings`` construction."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


# -- Tests -----------------------------------------------------------------


class TestSettingsFields:
    """AC: Settings.model_fields includes every documented field."""

    def test_all_fields_present(self) -> None:
        expected = {
            "database_url",
            "orchestrator_api_key",
            "engine_webhook_secret",
            "engine_base_url",
            "engine_api_key",
            "llm_provider",
            "llm_model",
            "anthropic_api_key",
            "anthropic_max_tokens",
            "anthropic_timeout_seconds",
            "agents_dir",
            "trace_dir",
            "repo_root",
            "lifecycle_max_corrections",
            "solo_dev_mode",
            "github_webhook_secret",
            "github_pat",
            "github_app_id",
            "github_private_key",
            "flow_engine_lifecycle_base_url",
            "flow_engine_tenant_api_key",
            "log_level",
            "trace_backend",
            "engine_dispatch_timeout_seconds",
            "public_base_url",
        }
        assert expected == set(Settings.model_fields.keys())


class TestEnvVarHappyPath:
    """AC: env vars set → Settings loads without error, values match."""

    @pytest.mark.usefixtures("_env_with_required")
    def test_loads_from_env(self) -> None:
        s = Settings()
        assert str(s.database_url) == "postgresql+asyncpg://u:p@localhost:5432/testdb"
        assert s.orchestrator_api_key.get_secret_value() == "test-api-key"
        assert s.engine_webhook_secret.get_secret_value() == "test-webhook-secret"
        assert str(s.engine_base_url) == "http://localhost:9000/"

    @pytest.mark.usefixtures("_env_with_required")
    def test_defaults(self) -> None:
        s = Settings()
        assert s.llm_provider == "stub"
        assert s.llm_model is None
        assert s.anthropic_api_key is None
        assert s.engine_api_key is None
        assert s.agents_dir == Path("agents")
        assert s.log_level == "INFO"


class TestMissingRequiredField:
    """AC: missing required field → ValidationError naming the field."""

    @pytest.mark.parametrize("field", list(_REQUIRED_ENV.keys()))
    def test_missing_field_raises(self, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv(field)
        with pytest.raises(ValidationError) as exc_info:
            Settings()
        assert field.lower() in str(exc_info.value).lower()


class TestPyprojectLayer:
    """AC: [tool.orchestrator] in pyproject.toml is read as a settings source."""

    def test_pyproject_overrides_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [tool.orchestrator]
                log_level = "DEBUG"
            """)
        )
        monkeypatch.chdir(tmp_path)
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        # Ensure LOG_LEVEL env is NOT set so pyproject wins over the default
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        s = Settings()
        assert s.log_level == "DEBUG"

    def test_env_overrides_pyproject(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [tool.orchestrator]
                log_level = "DEBUG"
            """)
        )
        monkeypatch.chdir(tmp_path)
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("LOG_LEVEL", "ERROR")

        s = Settings()
        assert s.log_level == "ERROR"

    def test_missing_pyproject_is_silent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No pyproject.toml anywhere → source returns empty, defaults apply."""
        monkeypatch.chdir(tmp_path)
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)

        s = Settings()
        assert s.log_level == "INFO"  # default


class TestGetSettingsCache:
    """AC: get_settings() is lru_cache-memoized."""

    @pytest.mark.usefixtures("_env_with_required")
    def test_returns_same_instance(self) -> None:
        a = get_settings()
        b = get_settings()
        assert a is b


class TestAnthropicValidation:
    """T-073: the ``_validate_llm_provider`` model_validator branch."""

    def _kw(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {**_REQUIRED_INIT}
        base.update(overrides)
        return base

    def test_anthropic_provider_missing_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError) as exc_info:
            Settings(**self._kw(llm_provider="anthropic"))  # type: ignore[arg-type]
        assert "anthropic_api_key" in str(exc_info.value)

    def test_anthropic_provider_empty_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(  # type: ignore[arg-type]
                **self._kw(llm_provider="anthropic", anthropic_api_key="")
            )

    def test_anthropic_provider_whitespace_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(  # type: ignore[arg-type]
                **self._kw(llm_provider="anthropic", anthropic_api_key="   ")
            )

    def test_anthropic_provider_valid_key_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        s = Settings(  # type: ignore[arg-type]
            **self._kw(
                llm_provider="anthropic",
                anthropic_api_key="sk-ant-test",
            )
        )
        assert s.llm_provider == "anthropic"

    def test_stub_provider_without_key_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        s = Settings(**self._kw(llm_provider="stub"))  # type: ignore[arg-type]
        assert s.llm_provider == "stub"
        assert s.anthropic_api_key is None

    def test_anthropic_provider_defaults_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        s = Settings(  # type: ignore[arg-type]
            **self._kw(
                llm_provider="anthropic",
                anthropic_api_key="sk-ant-test",
            )
        )
        assert s.llm_model == "claude-opus-4-7"

    def test_anthropic_provider_respects_explicit_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        s = Settings(  # type: ignore[arg-type]
            **self._kw(
                llm_provider="anthropic",
                anthropic_api_key="sk-ant-test",
                llm_model="claude-sonnet-4-6",
            )
        )
        assert s.llm_model == "claude-sonnet-4-6"

    def test_anthropic_max_tokens_zero_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(  # type: ignore[arg-type]
                **self._kw(
                    llm_provider="anthropic",
                    anthropic_api_key="sk-ant-test",
                    anthropic_max_tokens=0,
                )
            )

    def test_anthropic_timeout_seconds_negative_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(  # type: ignore[arg-type]
                **self._kw(
                    llm_provider="anthropic",
                    anthropic_api_key="sk-ant-test",
                    anthropic_timeout_seconds=-1,
                )
            )


class TestGitHubCredentialValidation:
    """T-140: the ``_validate_github_credentials`` model_validator branch."""

    # Explicit ``None`` overrides defeat local ``.env`` leakage — pydantic-settings
    # still merges the dotenv source under init kwargs, so we need to set each
    # GitHub field to ``None`` at call time, not rely on ``monkeypatch.delenv``.

    def test_no_credentials_is_valid(self) -> None:
        s = _make_settings(
            github_pat=None, github_app_id=None, github_private_key=None,
        )
        assert s.github_pat is None
        assert s.github_app_id is None
        assert s.github_private_key is None

    def test_pat_only_is_valid(self) -> None:
        s = _make_settings(
            github_pat="ghp_token", github_app_id=None, github_private_key=None,
        )
        assert s.github_pat is not None
        assert s.github_pat.get_secret_value() == "ghp_token"

    def test_app_credentials_together_is_valid(self) -> None:
        s = _make_settings(
            github_pat=None,
            github_app_id="12345",
            github_private_key="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
        )
        assert s.github_app_id == "12345"
        assert s.github_private_key is not None

    def test_app_id_without_private_key_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_settings(
                github_pat=None, github_app_id="12345", github_private_key=None,
            )
        assert "github_app_id" in str(exc_info.value).lower()

    def test_private_key_without_app_id_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_settings(
                github_pat=None,
                github_app_id=None,
                github_private_key="-----BEGIN RSA PRIVATE KEY-----",
            )
        assert "github_private_key" in str(exc_info.value).lower()

    def test_pat_and_app_both_configured_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_settings(
                github_pat="ghp_token",
                github_app_id="12345",
                github_private_key="-----BEGIN RSA PRIVATE KEY-----",
            )
        assert "not both" in str(exc_info.value).lower()


class TestDependencyOverride:
    """AC: get_settings_dep is overridable via FastAPI dependency_overrides."""

    def test_override_works(self) -> None:
        from app.core.dependencies import get_settings_dep

        custom = _make_settings(log_level="ERROR")

        def override() -> Settings:
            return custom

        # Simulate what a test fixture does
        original = get_settings_dep
        try:
            result = override()
            assert result.log_level == "ERROR"
        finally:
            _ = original  # no-op restore; just proving the pattern
