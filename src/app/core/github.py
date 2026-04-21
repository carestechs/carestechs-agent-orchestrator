"""FEAT-007 composition root for the GitHub Checks client.

Owns the shared ``httpx.AsyncClient`` used by both the App-auth strategy
and the Httpx-backed client, plus the factory that resolves
``App > PAT > Noop`` based on ``Settings``.  The lifespan is responsible
for closing the shared client on shutdown.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.modules.ai.github.auth import AppAuthStrategy, PatAuthStrategy
from app.modules.ai.github.checks import (
    GitHubChecksClient,
    HttpxGitHubChecksClient,
    NoopGitHubChecksClient,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 30.0


def make_shared_http_client() -> httpx.AsyncClient:
    """Build the ``AsyncClient`` used for every GitHub REST call."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)


def get_github_checks_client(
    settings: Settings, http: httpx.AsyncClient
) -> GitHubChecksClient:
    """Resolve the active Checks client: App > PAT > Noop."""
    if settings.github_app_id and settings.github_private_key:
        auth = AppAuthStrategy(
            app_id=settings.github_app_id,
            private_key=settings.github_private_key.get_secret_value(),
            http=http,
        )
        logger.info("github checks: using App-auth strategy")
        return HttpxGitHubChecksClient(auth=auth, http=http)

    if settings.github_pat is not None:
        logger.info("github checks: using PAT-auth strategy")
        return HttpxGitHubChecksClient(
            auth=PatAuthStrategy(settings.github_pat.get_secret_value()),
            http=http,
        )

    logger.info("github checks: no credentials; using noop client")
    return NoopGitHubChecksClient()


def resolved_strategy(client: GitHubChecksClient) -> str:
    """Return ``"app" | "pat" | "noop"`` for an already-built client."""
    if isinstance(client, NoopGitHubChecksClient):
        return "noop"
    assert isinstance(client, HttpxGitHubChecksClient)
    auth = getattr(client, "_auth", None)
    if isinstance(auth, AppAuthStrategy):
        return "app"
    return "pat"


__all__ = [
    "get_github_checks_client",
    "make_shared_http_client",
    "resolved_strategy",
]
