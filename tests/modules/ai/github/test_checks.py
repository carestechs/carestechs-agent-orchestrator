"""Tests for ``HttpxGitHubChecksClient`` + ``NoopGitHubChecksClient``."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.exceptions import AuthError, ProviderError
from app.modules.ai.github.auth import PatAuthStrategy
from app.modules.ai.github.checks import (
    CHECK_NAME,
    NOOP_CHECK_ID,
    HttpxGitHubChecksClient,
    NoopGitHubChecksClient,
    noop_warning_was_emitted,
    reset_noop_warning,
)


@pytest.fixture
def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


@pytest.fixture
def pat_client(http: httpx.AsyncClient) -> HttpxGitHubChecksClient:
    return HttpxGitHubChecksClient(auth=PatAuthStrategy("ghp_test"), http=http)


# ---------------------------------------------------------------------------
# create_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_check_happy_path(
    pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/repos/foo/bar/check-runs").mock(
            return_value=httpx.Response(201, json={"id": 42})
        )
        check_id = await pat_client.create_check(
            owner="foo", repo="bar", head_sha="deadbeef"
        )
        assert check_id == "42"
        assert route.call_count == 1
        sent = route.calls.last.request
        body = sent.content.decode()
        assert '"name":"orchestrator/impl-review"' in body
        assert '"head_sha":"deadbeef"' in body
        assert '"status":"in_progress"' in body
        assert sent.headers["Authorization"] == "Bearer ghp_test"
        assert sent.headers["Accept"] == "application/vnd.github+json"

    await http.aclose()


@pytest.mark.asyncio
async def test_create_check_5xx_raises_provider(
    pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/repos/foo/bar/check-runs").mock(
            return_value=httpx.Response(500, text="boom")
        )
        with pytest.raises(ProviderError) as exc_info:
            await pat_client.create_check(owner="foo", repo="bar", head_sha="abc")
        assert exc_info.value.provider_http_status == 500
        assert exc_info.value.original_body == "boom"
    await http.aclose()


@pytest.mark.asyncio
async def test_create_check_401_raises_auth(
    pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/repos/foo/bar/check-runs").mock(
            return_value=httpx.Response(401, text="bad creds")
        )
        with pytest.raises(AuthError):
            await pat_client.create_check(owner="foo", repo="bar", head_sha="abc")
    await http.aclose()


@pytest.mark.asyncio
async def test_create_check_timeout_raises_provider(
    pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/repos/foo/bar/check-runs").mock(
            side_effect=httpx.TimeoutException("slow")
        )
        with pytest.raises(ProviderError) as exc_info:
            await pat_client.create_check(owner="foo", repo="bar", head_sha="abc")
        assert exc_info.value.provider_http_status is None
    await http.aclose()


# ---------------------------------------------------------------------------
# update_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("conclusion", ["success", "failure"])
async def test_update_check_serializes_conclusion(
    conclusion: str, pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.patch("/repos/foo/bar/check-runs/42").mock(
            return_value=httpx.Response(200, json={})
        )
        await pat_client.update_check(
            owner="foo", repo="bar", check_id="42", conclusion=conclusion  # type: ignore[arg-type]
        )
        body = route.calls.last.request.content.decode()
        assert f'"conclusion":"{conclusion}"' in body
        assert '"status":"completed"' in body
    await http.aclose()


@pytest.mark.asyncio
async def test_update_check_404_raises_provider(
    pat_client: HttpxGitHubChecksClient, http: httpx.AsyncClient
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.patch("/repos/foo/bar/check-runs/42").mock(
            return_value=httpx.Response(404, text="no such check")
        )
        with pytest.raises(ProviderError) as exc_info:
            await pat_client.update_check(
                owner="foo", repo="bar", check_id="42", conclusion="success"
            )
        assert exc_info.value.provider_http_status == 404
    await http.aclose()


# ---------------------------------------------------------------------------
# Noop client
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_warn_flag() -> None:
    reset_noop_warning()


@pytest.mark.asyncio
async def test_noop_client_returns_sentinel() -> None:
    """Returns the sentinel id and sets the once-per-process warning flag."""
    reset_noop_warning()
    assert noop_warning_was_emitted() is False

    client = NoopGitHubChecksClient()
    r1 = await client.create_check(owner="a", repo="b", head_sha="sha1")
    r2 = await client.create_check(owner="c", repo="d", head_sha="sha2")

    assert r1 == NOOP_CHECK_ID
    assert r2 == NOOP_CHECK_ID
    # After the first call the flag flips and stays True — guarantees the
    # warning logger is invoked exactly once per process.
    assert noop_warning_was_emitted() is True


@pytest.mark.asyncio
async def test_noop_client_update_is_silent() -> None:
    client = NoopGitHubChecksClient()
    result = await client.update_check(
        owner="a", repo="b", check_id=NOOP_CHECK_ID, conclusion="success"
    )
    assert result is None


def test_check_name_constant_is_stable() -> None:
    # Guard against accidental renames — branch-protection bindings
    # would silently break.
    assert CHECK_NAME == "orchestrator/impl-review"
