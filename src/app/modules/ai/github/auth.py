"""GitHub Checks API auth strategies: PAT + App installation tokens.

Both strategies produce the ``Authorization`` + ``Accept`` headers needed
for a single REST call.  ``AppAuthStrategy`` additionally talks to GitHub
twice on a cache miss (installation lookup + access-token exchange) and
caches the resulting token per ``owner/repo`` for the lifetime of the
process.

Cache discipline:

* 50-minute effective TTL (GitHub tokens last 60 minutes; we refresh with
  10 minutes of slack).
* Refreshes are serialized per repo with an ``asyncio.Lock`` so two
  concurrent requests against the same repo never double-fetch.
* Different repos refresh independently — no global lock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import jwt

_GITHUB_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"

# JWT window: GitHub requires ``exp - iat <= 600``. We use 540s to stay
# under the ceiling with clock-skew headroom; ``iat`` is shifted 60s into
# the past for the same reason (GitHub documents this).
_JWT_LIFETIME_SECONDS = 540
_JWT_IAT_SKEW_SECONDS = 60

# Refresh ``_TOKEN_TTL_BUFFER`` seconds before the token actually expires
# so a reviewer clicking "approve" at t=59:59 doesn't race against a
# GitHub-side expiry.
_TOKEN_TTL_BUFFER = 600


class AuthStrategy(Protocol):
    """Produces the auth/accept headers for a GitHub REST call."""

    async def headers_for(self, *, owner: str, repo: str) -> dict[str, str]:
        ...


class PatAuthStrategy:
    """Classic personal access token — static Bearer for every call."""

    def __init__(self, pat: str) -> None:
        self._pat = pat

    async def headers_for(self, *, owner: str, repo: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._pat}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }


@dataclass(slots=True)
class _CachedToken:
    token: str
    expires_at: float  # monotonic-ish: unix seconds


class AppAuthStrategy:
    """GitHub App installation tokens, cached per ``owner/repo``.

    Construct with either the raw PEM (as returned by GitHub on App
    creation) or the sentinel ``@file:/absolute/path/to/key.pem`` — the
    latter is kinder to operators who don't want to shove a multi-line
    secret into a single env var.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._app_id = app_id
        self._private_key = self._resolve_key(private_key)
        self._http = http
        self._tokens: dict[str, _CachedToken] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def headers_for(self, *, owner: str, repo: str) -> dict[str, str]:
        token = await self._token_for(owner=owner, repo=repo)
        return {
            "Authorization": f"token {token}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_key(raw: str) -> str:
        """Accept raw PEM or ``@file:/path`` → return the PEM body."""
        stripped = raw.strip()
        if stripped.startswith("@file:"):
            path = Path(stripped[len("@file:"):])
            return path.read_text().strip()
        return stripped

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _token_for(self, *, owner: str, repo: str) -> str:
        key = f"{owner}/{repo}"
        async with self._lock(key):
            now = time.time()
            cached = self._tokens.get(key)
            if cached and cached.expires_at > now:
                return cached.token
            token, expires_at = await self._fetch_installation_token(owner, repo)
            self._tokens[key] = _CachedToken(
                token=token,
                expires_at=expires_at - _TOKEN_TTL_BUFFER,
            )
            return token

    async def _fetch_installation_token(
        self, owner: str, repo: str
    ) -> tuple[str, float]:
        jwt_token = self._signed_jwt()
        jwt_headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }

        install_resp = await self._http.get(
            f"{_GITHUB_API}/repos/{owner}/{repo}/installation",
            headers=jwt_headers,
        )
        install_resp.raise_for_status()
        installation_id = install_resp.json()["id"]

        token_resp = await self._http.post(
            f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers=jwt_headers,
        )
        token_resp.raise_for_status()
        body = token_resp.json()

        expires_at = _parse_iso_seconds(body["expires_at"])
        return body["token"], expires_at

    def _signed_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - _JWT_IAT_SKEW_SECONDS,
            "exp": now + _JWT_LIFETIME_SECONDS - _JWT_IAT_SKEW_SECONDS,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")


def _parse_iso_seconds(iso: str) -> float:
    """Parse an RFC-3339 ``expires_at`` into a unix epoch float."""
    from datetime import datetime

    # GitHub emits trailing ``Z``; ``fromisoformat`` accepts that on 3.11+.
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


__all__ = ["AppAuthStrategy", "AuthStrategy", "PatAuthStrategy"]
