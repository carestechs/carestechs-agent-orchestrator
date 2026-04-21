"""GitHub Checks client protocol + Httpx/Noop implementations.

``CHECK_NAME`` is the canonical gate name we register with GitHub.  It
MUST stay stable — GitHub's branch protection rulesets bind to the name,
so renaming it silently un-gates every protected branch.

Error discipline: failures are surfaced via ``ProviderError`` so the
service layer can choose whether to proceed (typical: log + continue;
the state machine remains authoritative per AD-1).  4xx errors carry the
``provider_http_status`` + ``original_body``; transport errors are
mapped to ``ProviderError`` with ``provider_http_status=None``.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, runtime_checkable

import httpx

from app.core.exceptions import AuthError, ProviderError
from app.modules.ai.github.auth import AuthStrategy

logger = logging.getLogger(__name__)

CheckConclusion = Literal["success", "failure"]
"""Conclusions we ever set.  ``neutral``/``cancelled`` are not used."""

CHECK_NAME = "orchestrator/impl-review"
"""The single, repo-agnostic name FEAT-007 posts for every task PR."""

_GITHUB_API = "https://api.github.com"

# Sentinel returned by the no-op client.  Service code stores this on
# ``TaskImplementation.github_check_id`` so the later ``update_check``
# call can short-circuit without a DB lookup on the auth strategy.
NOOP_CHECK_ID = "noop"

# Module-level flag so ``NoopGitHubChecksClient`` warns exactly once per
# process regardless of how many runs trip through it.
_noop_warned = False


def reset_noop_warning() -> None:
    """Test helper: clear the once-per-process noop warning flag."""
    global _noop_warned
    _noop_warned = False


def noop_warning_was_emitted() -> bool:
    """Test helper: whether the once-per-process noop warning has fired."""
    return _noop_warned


@runtime_checkable
class GitHubChecksClient(Protocol):
    """Registers and resolves the ``orchestrator/impl-review`` check."""

    async def create_check(
        self,
        *,
        owner: str,
        repo: str,
        head_sha: str,
        name: str = CHECK_NAME,
    ) -> str:
        """Register a ``status=in_progress`` check; return the check-run id."""
        ...

    async def update_check(
        self,
        *,
        owner: str,
        repo: str,
        check_id: str,
        conclusion: CheckConclusion,
    ) -> None:
        """Flip an existing check to ``status=completed`` with *conclusion*."""
        ...


# ---------------------------------------------------------------------------
# Httpx client
# ---------------------------------------------------------------------------


class HttpxGitHubChecksClient:
    """GitHub-backed implementation of ``GitHubChecksClient``."""

    def __init__(self, *, auth: AuthStrategy, http: httpx.AsyncClient) -> None:
        self._auth = auth
        self._http = http

    async def create_check(
        self,
        *,
        owner: str,
        repo: str,
        head_sha: str,
        name: str = CHECK_NAME,
    ) -> str:
        headers = await self._auth.headers_for(owner=owner, repo=repo)
        body = {"name": name, "head_sha": head_sha, "status": "in_progress"}
        try:
            resp = await self._http.post(
                f"{_GITHUB_API}/repos/{owner}/{repo}/check-runs",
                headers=headers,
                json=body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"github check-runs create transport error: {exc}",
                provider_http_status=None,
            ) from exc
        self._raise_for_status(resp, action="create_check")
        check_id = str(resp.json()["id"])
        logger.info(
            "github check created",
            extra={
                "owner": owner,
                "repo": repo,
                "check_id": check_id,
                "check_name": name,
            },
        )
        return check_id

    async def update_check(
        self,
        *,
        owner: str,
        repo: str,
        check_id: str,
        conclusion: CheckConclusion,
    ) -> None:
        headers = await self._auth.headers_for(owner=owner, repo=repo)
        body = {"status": "completed", "conclusion": conclusion}
        try:
            resp = await self._http.patch(
                f"{_GITHUB_API}/repos/{owner}/{repo}/check-runs/{check_id}",
                headers=headers,
                json=body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"github check-runs update transport error: {exc}",
                provider_http_status=None,
            ) from exc
        self._raise_for_status(resp, action="update_check")
        logger.info(
            "github check updated",
            extra={
                "owner": owner,
                "repo": repo,
                "check_id": check_id,
                "conclusion": conclusion,
            },
        )

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, action: str) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:2000]
        if resp.status_code == 401:
            raise AuthError(
                f"github {action} unauthorized: {body}",
            )
        raise ProviderError(
            f"github {action} failed with status {resp.status_code}",
            provider_http_status=resp.status_code,
            original_body=body,
        )


# ---------------------------------------------------------------------------
# Noop client
# ---------------------------------------------------------------------------


class NoopGitHubChecksClient:
    """Degrades merge-gating to a pair of no-ops.

    Used when neither a PAT nor App credentials are configured — FEAT-006
    endpoints keep working, they just don't flip a GitHub check.  AD-9
    composition integrity.
    """

    async def create_check(
        self,
        *,
        owner: str,
        repo: str,
        head_sha: str,
        name: str = CHECK_NAME,
    ) -> str:
        global _noop_warned
        if not _noop_warned:
            logger.warning(
                "github merge-gating disabled: no credentials configured; "
                "check-runs are no-ops"
            )
            _noop_warned = True
        return NOOP_CHECK_ID

    async def update_check(
        self,
        *,
        owner: str,
        repo: str,
        check_id: str,
        conclusion: CheckConclusion,
    ) -> None:
        return None


__all__ = [
    "CHECK_NAME",
    "NOOP_CHECK_ID",
    "CheckConclusion",
    "GitHubChecksClient",
    "HttpxGitHubChecksClient",
    "NoopGitHubChecksClient",
    "noop_warning_was_emitted",
    "reset_noop_warning",
]
