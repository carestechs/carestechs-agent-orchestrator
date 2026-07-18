"""GitHub Contents API client — idempotent file create/update via PUT.

Used by the FEAT-019 artifact-commit executor nodes to push lifecycle
artefacts (briefs, task lists, plans, reviews, event logs) to the project
repo.  Auth reuses the PAT / App strategy from ``github.auth``.

Writes are idempotent: a file that already exists at the target path is
updated in-place (the current SHA is fetched first, as the API requires it
on updates).  A file that does not exist is created.

Event-log appends (``append_ndjson``) are read-modify-write: the current
file content is fetched, the new line is appended, and the file is
re-written.  Under concurrent orchestrator runs this may produce conflicts;
the caller retries on 409 (one retry, 500 ms backoff) which covers the
dominant single-run case.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

import httpx

from app.core.exceptions import ProviderError

if TYPE_CHECKING:
    from app.modules.ai.github.auth import AuthStrategy

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


class GitHubContentsClient:
    """Thin async wrapper around ``PUT /repos/{owner}/{repo}/contents/{path}``.

    Construct once per run (or per lifespan if the repo is fixed).
    ``owner`` and ``repo`` are resolved per-call so a single client
    instance can serve multiple repos.
    """

    def __init__(
        self,
        *,
        auth: AuthStrategy,
        http: httpx.AsyncClient,
        branch: str = "main",
    ) -> None:
        self._auth = auth
        self._http = http
        self._branch = branch

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def put_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
    ) -> str:
        """Create or update *path* in *owner/repo* on the configured branch.

        Returns the commit SHA.  On update, fetches the current file SHA
        first (GitHub requires it for the PUT body).
        """
        current_sha = await self._get_file_sha(owner=owner, repo=repo, path=path)
        return await self._put(
            owner=owner,
            repo=repo,
            path=path,
            content=content,
            message=message,
            current_sha=current_sha,
        )

    async def append_ndjson(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        line: str,
        message: str,
    ) -> str:
        """Append one NDJSON line to *path*, creating the file if absent.

        Retries once on 409 (concurrent write conflict).  Returns the
        commit SHA.
        """
        for attempt in range(2):
            current_sha, current_text = await self._get_file(
                owner=owner, repo=repo, path=path
            )
            new_text = (current_text or "") + line.rstrip("\n") + "\n"
            try:
                sha = await self._put(
                    owner=owner,
                    repo=repo,
                    path=path,
                    content=new_text,
                    message=message,
                    current_sha=current_sha,
                )
                return sha
            except ProviderError as exc:
                if attempt == 0 and getattr(exc, "provider_http_status", None) == 409:
                    logger.warning(
                        "github contents 409 conflict on %s/%s/%s; retrying",
                        owner,
                        repo,
                        path,
                    )
                    await asyncio.sleep(0.5)
                    continue
                raise
        raise ProviderError("github contents: append_ndjson exhausted retries")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_file(
        self, *, owner: str, repo: str, path: str
    ) -> tuple[str | None, str | None]:
        """Return ``(current_sha, decoded_text)`` or ``(None, None)`` if absent."""
        headers = await self._auth.headers_for(owner=owner, repo=repo)
        try:
            resp = await self._http.get(
                f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                headers=headers,
                params={"ref": self._branch},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"github contents GET transport error: {exc}",
                provider_http_status=None,
            ) from exc

        if resp.status_code == 404:
            return None, None
        if not resp.is_success:
            raise ProviderError(
                f"github contents GET {path} failed: {resp.status_code} {resp.text[:200]}",
                provider_http_status=resp.status_code,
            )
        body = resp.json()
        sha: str = body["sha"]
        decoded = base64.b64decode(body["content"]).decode("utf-8")
        return sha, decoded

    async def _get_file_sha(
        self, *, owner: str, repo: str, path: str
    ) -> str | None:
        sha, _ = await self._get_file(owner=owner, repo=repo, path=path)
        return sha

    async def _put(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        current_sha: str | None,
    ) -> str:
        headers = await self._auth.headers_for(owner=owner, repo=repo)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body: dict[str, object] = {
            "message": message,
            "content": encoded,
            "branch": self._branch,
        }
        if current_sha is not None:
            body["sha"] = current_sha

        try:
            resp = await self._http.put(
                f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                headers=headers,
                json=body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"github contents PUT transport error: {exc}",
                provider_http_status=None,
            ) from exc

        if not resp.is_success:
            raise ProviderError(
                f"github contents PUT {path} failed: {resp.status_code} {resp.text[:300]}",
                provider_http_status=resp.status_code,
            )

        commit_sha: str = resp.json()["commit"]["sha"]
        logger.info(
            "github contents committed",
            extra={"owner": owner, "repo": repo, "path": path, "sha": commit_sha},
        )
        return commit_sha


__all__ = ["GitHubContentsClient"]
