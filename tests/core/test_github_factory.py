"""Tests for ``app.core.github`` factory + strategy resolution."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.github import (
    get_github_checks_client,
    make_shared_http_client,
    resolved_strategy,
)
from app.modules.ai.github.checks import (
    HttpxGitHubChecksClient,
    NoopGitHubChecksClient,
)

_REQUIRED: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/testdb",
    "orchestrator_api_key": "api-key",
    "engine_webhook_secret": "hook-secret",
    "engine_base_url": "http://localhost:9000",
}


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        **_REQUIRED,
        "github_pat": None,
        "github_app_id": None,
        "github_private_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def http() -> httpx.AsyncClient:
    return make_shared_http_client()


def test_no_credentials_returns_noop(http: httpx.AsyncClient) -> None:
    client = get_github_checks_client(_settings(), http)
    assert isinstance(client, NoopGitHubChecksClient)
    assert resolved_strategy(client) == "noop"


def test_pat_returns_httpx_client(http: httpx.AsyncClient) -> None:
    client = get_github_checks_client(_settings(github_pat="ghp_x"), http)
    assert isinstance(client, HttpxGitHubChecksClient)
    assert resolved_strategy(client) == "pat"


def test_app_credentials_returns_httpx_client(
    fake_rsa_pem: str, http: httpx.AsyncClient
) -> None:
    client = get_github_checks_client(
        _settings(github_app_id="42", github_private_key=fake_rsa_pem),
        http,
    )
    assert isinstance(client, HttpxGitHubChecksClient)
    assert resolved_strategy(client) == "app"


def test_both_credentials_rejected_at_settings_level(fake_rsa_pem: str) -> None:
    # The factory never sees this state — ``Settings`` rejects it first.
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            github_pat="ghp_x",
            github_app_id="42",
            github_private_key=fake_rsa_pem,
        )
    assert "not both" in str(exc_info.value).lower()


def test_half_set_app_credentials_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(github_app_id="42")
    assert "github_app_id" in str(exc_info.value).lower()
