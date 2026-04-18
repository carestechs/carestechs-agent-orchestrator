"""Tests for ``orchestrator doctor``."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/testdb",
    "ORCHESTRATOR_API_KEY": "test-api-key",
    "ENGINE_WEBHOOK_SECRET": "test-webhook-secret",
    "ENGINE_BASE_URL": "http://localhost:9000",
}

# The session-scoped autouse ``_test_env`` fixture in conftest.py populates
# ``os.environ`` with working defaults.  Typer's ``runner.invoke(env=...)``
# merges on top of that, so these tests must explicitly unset vars they
# want absent.
_ENV_KEYS = (
    "DATABASE_URL",
    "ORCHESTRATOR_API_KEY",
    "ENGINE_WEBHOOK_SECRET",
    "ENGINE_BASE_URL",
    "LLM_PROVIDER",
)


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.usefixtures("_clean_env")
class TestDoctorHappy:
    def test_all_set_exits_0(self) -> None:
        result = runner.invoke(main, ["doctor"], env=_REQUIRED_ENV)
        assert result.exit_code == 0
        assert "✓" in result.output

    def test_json_output(self) -> None:
        result = runner.invoke(main, ["--json", "doctor"], env=_REQUIRED_ENV)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert all("name" in c and "status" in c for c in data)


@pytest.mark.usefixtures("_clean_env")
class TestDoctorMissingEnv:
    def test_missing_api_key(self) -> None:
        env = {**_REQUIRED_ENV}
        del env["ORCHESTRATOR_API_KEY"]
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "ORCHESTRATOR_API_KEY" in result.output

    def test_missing_webhook_secret(self) -> None:
        env = {**_REQUIRED_ENV}
        del env["ENGINE_WEBHOOK_SECRET"]
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "ENGINE_WEBHOOK_SECRET" in result.output

    def test_missing_db_url(self) -> None:
        env = {**_REQUIRED_ENV}
        del env["DATABASE_URL"]
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "DATABASE_URL" in result.output


@pytest.mark.usefixtures("_clean_env")
class TestDoctorAnthropicKey:
    def test_anthropic_missing_key_fails(self) -> None:
        env = {**_REQUIRED_ENV, "LLM_PROVIDER": "anthropic"}
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "ANTHROPIC_API_KEY" in result.output

    def test_anthropic_malformed_key_fails(self) -> None:
        env = {
            **_REQUIRED_ENV,
            "LLM_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "short",
        }
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "does not look like an Anthropic key" in result.output

    def test_anthropic_valid_key_passes(self) -> None:
        env = {
            **_REQUIRED_ENV,
            "LLM_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40,
        }
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 0
        assert "well-formed" in result.output

    def test_stub_provider_without_key_passes(self) -> None:
        env = {**_REQUIRED_ENV, "LLM_PROVIDER": "stub"}
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 0


@pytest.mark.usefixtures("_clean_env")
class TestDoctorAgentsDir:
    def test_missing_dir_is_warn_not_fail(self, tmp_path: pytest.TempPathFactory) -> None:
        env = {**_REQUIRED_ENV, "AGENTS_DIR": "/nonexistent/path/xyzzy"}
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 0
        assert "⚠" in result.output
        assert "agents_dir" in result.output

    def test_valid_dir_loads_definitions(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import shutil
        from pathlib import Path

        src = Path(__file__).parent / "fixtures" / "agents" / "sample-linear.yaml"
        shutil.copy(src, tmp_path / "sample-linear@1.0.yaml")
        env = {**_REQUIRED_ENV, "AGENTS_DIR": str(tmp_path)}
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 0
        assert "agents_dir" in result.output
        assert "loaded 1 agent" in result.output

    def test_malformed_yaml_is_fail(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "broken@1.0.yaml").write_text("not: {valid: yaml")
        env = {**_REQUIRED_ENV, "AGENTS_DIR": str(tmp_path)}
        result = runner.invoke(main, ["doctor"], env=env)
        assert result.exit_code == 2
        assert "unreadable agent file" in result.output

    def test_json_output_includes_agents_check(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        env = {**_REQUIRED_ENV, "AGENTS_DIR": str(tmp_path)}
        result = runner.invoke(main, ["--json", "doctor"], env=env)
        assert result.exit_code == 0
        data = json.loads(result.output)
        names = [c["name"] for c in data]
        assert "agents_dir" in names
