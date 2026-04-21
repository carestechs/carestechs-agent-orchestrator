"""Tests for ``app.modules.ai.github.auth``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import respx

from app.modules.ai.github.auth import AppAuthStrategy, PatAuthStrategy

# ---------------------------------------------------------------------------
# PatAuthStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pat_strategy_returns_static_headers() -> None:
    strategy = PatAuthStrategy("ghp_faketoken")
    headers = await strategy.headers_for(owner="foo", repo="bar")
    assert headers["Authorization"] == "Bearer ghp_faketoken"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


# ---------------------------------------------------------------------------
# AppAuthStrategy
# ---------------------------------------------------------------------------


def _future_iso(*, seconds: int = 3600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


@pytest.mark.asyncio
async def test_app_strategy_fetches_and_caches(
    fake_rsa_pem: str, http: httpx.AsyncClient
) -> None:
    strategy = AppAuthStrategy(app_id="12345", private_key=fake_rsa_pem, http=http)

    with respx.mock(base_url="https://api.github.com") as mock:
        install = mock.get("/repos/foo/bar/installation").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )
        token = mock.post("/app/installations/999/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "ghs_test", "expires_at": _future_iso()}
            )
        )

        h1 = await strategy.headers_for(owner="foo", repo="bar")
        h2 = await strategy.headers_for(owner="foo", repo="bar")

        assert h1["Authorization"] == "token ghs_test"
        assert h1 == h2
        assert install.call_count == 1
        assert token.call_count == 1

    await http.aclose()


@pytest.mark.asyncio
async def test_app_strategy_refetches_on_expiry(
    fake_rsa_pem: str, http: httpx.AsyncClient
) -> None:
    strategy = AppAuthStrategy(app_id="12345", private_key=fake_rsa_pem, http=http)

    # Return a token that is already "expired" (well inside the 10-min
    # TTL buffer), so the second call must refetch.
    expired_iso = (datetime.now(UTC) + timedelta(seconds=60)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/foo/bar/installation").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )
        token_route = mock.post("/app/installations/999/access_tokens").mock(
            side_effect=[
                httpx.Response(
                    201, json={"token": "tok-1", "expires_at": expired_iso}
                ),
                httpx.Response(
                    201, json={"token": "tok-2", "expires_at": _future_iso()}
                ),
            ]
        )

        h1 = await strategy.headers_for(owner="foo", repo="bar")
        h2 = await strategy.headers_for(owner="foo", repo="bar")

        assert h1["Authorization"] == "token tok-1"
        assert h2["Authorization"] == "token tok-2"
        assert token_route.call_count == 2

    await http.aclose()


@pytest.mark.asyncio
async def test_app_strategy_serializes_concurrent_refresh(
    fake_rsa_pem: str, http: httpx.AsyncClient
) -> None:
    """Two concurrent ``headers_for`` for one repo must refetch once."""
    strategy = AppAuthStrategy(app_id="12345", private_key=fake_rsa_pem, http=http)

    with respx.mock(base_url="https://api.github.com") as mock:
        install = mock.get("/repos/foo/bar/installation").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        token = mock.post("/app/installations/1/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "ghs_once", "expires_at": _future_iso()}
            )
        )

        results = await asyncio.gather(
            strategy.headers_for(owner="foo", repo="bar"),
            strategy.headers_for(owner="foo", repo="bar"),
            strategy.headers_for(owner="foo", repo="bar"),
        )
        assert {h["Authorization"] for h in results} == {"token ghs_once"}
        assert install.call_count == 1
        assert token.call_count == 1

    await http.aclose()


@pytest.mark.asyncio
async def test_app_strategy_independent_caches_per_repo(
    fake_rsa_pem: str, http: httpx.AsyncClient
) -> None:
    strategy = AppAuthStrategy(app_id="12345", private_key=fake_rsa_pem, http=http)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/foo/a/installation").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        mock.get("/repos/foo/b/installation").mock(
            return_value=httpx.Response(200, json={"id": 2})
        )
        tok_a = mock.post("/app/installations/1/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "a", "expires_at": _future_iso()}
            )
        )
        tok_b = mock.post("/app/installations/2/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "b", "expires_at": _future_iso()}
            )
        )

        ha = await strategy.headers_for(owner="foo", repo="a")
        hb = await strategy.headers_for(owner="foo", repo="b")
        assert ha["Authorization"] == "token a"
        assert hb["Authorization"] == "token b"
        assert tok_a.call_count == 1
        assert tok_b.call_count == 1

    await http.aclose()


def test_app_strategy_resolves_file_prefix(
    fake_rsa_pem: str, tmp_path: Path, http: httpx.AsyncClient
) -> None:
    key_path = tmp_path / "app.pem"
    key_path.write_text(fake_rsa_pem)
    strategy = AppAuthStrategy(
        app_id="12345", private_key=f"@file:{key_path}", http=http
    )
    # A valid signed JWT proves the PEM was loaded from disk.
    token = strategy._signed_jwt()  # type: ignore[attr-defined]
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == "12345"


def test_app_strategy_jwt_claims(fake_rsa_pem: str, http: httpx.AsyncClient) -> None:
    strategy = AppAuthStrategy(app_id="42", private_key=fake_rsa_pem, http=http)
    token = strategy._signed_jwt()  # type: ignore[attr-defined]

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"

    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == "42"
    assert payload["exp"] - payload["iat"] <= 600
