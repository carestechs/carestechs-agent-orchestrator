"""AC-8: locks the Checks API contract with exact call counts.

Parametrized across PAT and App auth:

* Approval cycle: 1 POST create + 1 PATCH ``conclusion=success``.
* Rejection cycle: 1 POST create + 1 PATCH ``conclusion=failure``.
* App-auth path: installation-token cache is hit on the second cycle.

All GitHub endpoints are mocked with ``respx``; no network.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.ai.dependencies import get_github_checks_client_dep
from app.modules.ai.enums import AssigneeType, TaskStatus
from app.modules.ai.github.auth import AppAuthStrategy, PatAuthStrategy
from app.modules.ai.github.checks import HttpxGitHubChecksClient
from app.modules.ai.models import Task, TaskAssignment, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


def _future_iso(*, seconds: int = 3600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@pytest.fixture(autouse=True)
def _force_solo_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLO_DEV_MODE", "true")
    for var in ("GITHUB_PAT", "GITHUB_APP_ID", "GITHUB_PRIVATE_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


@pytest_asyncio.fixture(loop_scope="function")
async def github_http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10.0) as http:
        yield http


async def _seed_task(db: AsyncSession, ref: str) -> Task:
    wi = WorkItem(
        external_ref=f"FEAT-{ref}",
        type="FEAT",
        title="gh",
        status="in_progress",
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    t = Task(
        work_item_id=wi.id,
        external_ref=ref,
        title="task",
        status=TaskStatus.IMPLEMENTING.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(t)
    await db.flush()
    db.add(
        TaskAssignment(
            task_id=t.id,
            assignee_type=AssigneeType.DEV.value,
            assignee_id="dev-1",
            assigned_by="admin",
        )
    )
    await db.commit()
    await db.refresh(t)
    return t


def _install_strategy(
    app: FastAPI, http: httpx.AsyncClient, *, mode: str, pem: str | None
) -> None:
    if mode == "pat":
        auth = PatAuthStrategy("ghp_faketoken")
    else:
        assert pem is not None
        auth = AppAuthStrategy(
            app_id="12345", private_key=pem, http=http
        )
    client = HttpxGitHubChecksClient(auth=auth, http=http)
    app.dependency_overrides[get_github_checks_client_dep] = lambda: client


def _mock_app_token(mock: respx.MockRouter) -> respx.Route:
    mock.get("/repos/foo/bar/installation").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    return mock.post("/app/installations/999/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_installation", "expires_at": _future_iso()}
        )
    )


async def _submit_and_approve(
    client: AsyncClient, api_key: str, task_id: uuid.UUID, pr_number: int = 7
) -> None:
    r = await client.post(
        f"/api/v1/tasks/{task_id}/implementation",
        json={
            "prUrl": f"https://github.com/foo/bar/pull/{pr_number}",
            "commitSha": "deadbeef",
            "summary": "done",
        },
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    r = await client.post(
        f"/api/v1/tasks/{task_id}/review/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text


async def _submit_and_reject(
    client: AsyncClient, api_key: str, task_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/tasks/{task_id}/implementation",
        json={
            "prUrl": "https://github.com/foo/bar/pull/8",
            "commitSha": "cafef00d",
            "summary": "done",
        },
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    r = await client.post(
        f"/api/v1/tasks/{task_id}/review/reject",
        json={"feedback": "nope"},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text


# ---------------------------------------------------------------------------
# PAT + App parametrized happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["pat", "app"])
async def test_approval_cycle_posts_and_patches_exactly_once(
    mode: str,
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    db_session: AsyncSession,
    github_http: httpx.AsyncClient,
    fake_rsa_pem: str,
) -> None:
    _install_strategy(
        app, github_http, mode=mode, pem=fake_rsa_pem if mode == "app" else None
    )
    task = await _seed_task(db_session, ref=f"T-int-{mode}-ok")

    with respx.mock(base_url="https://api.github.com") as mock:
        if mode == "app":
            _mock_app_token(mock)
        create = mock.post("/repos/foo/bar/check-runs").mock(
            return_value=httpx.Response(201, json={"id": 77})
        )
        update = mock.patch("/repos/foo/bar/check-runs/77").mock(
            return_value=httpx.Response(200, json={})
        )

        await _submit_and_approve(client, api_key, task.id)

        assert create.call_count == 1
        assert update.call_count == 1

        body = update.calls.last.request.content.decode()
        assert '"status":"completed"' in body
        assert '"conclusion":"success"' in body

        # Auth header format differs per strategy.
        auth_header = create.calls.last.request.headers["Authorization"]
        if mode == "pat":
            assert auth_header == "Bearer ghp_faketoken"
        else:
            assert auth_header == "token ghs_installation"

        for route in (create, update):
            hdrs = route.calls.last.request.headers
            assert hdrs["Accept"] == "application/vnd.github+json"
            assert hdrs["X-GitHub-Api-Version"] == "2022-11-28"


@pytest.mark.parametrize("mode", ["pat", "app"])
async def test_rejection_cycle_patches_failure(
    mode: str,
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    db_session: AsyncSession,
    github_http: httpx.AsyncClient,
    fake_rsa_pem: str,
) -> None:
    _install_strategy(
        app, github_http, mode=mode, pem=fake_rsa_pem if mode == "app" else None
    )
    task = await _seed_task(db_session, ref=f"T-int-{mode}-rej")

    with respx.mock(base_url="https://api.github.com") as mock:
        if mode == "app":
            _mock_app_token(mock)
        create = mock.post("/repos/foo/bar/check-runs").mock(
            return_value=httpx.Response(201, json={"id": 88})
        )
        update = mock.patch("/repos/foo/bar/check-runs/88").mock(
            return_value=httpx.Response(200, json={})
        )

        await _submit_and_reject(client, api_key, task.id)

        assert create.call_count == 1
        assert update.call_count == 1
        assert '"conclusion":"failure"' in update.calls.last.request.content.decode()


# ---------------------------------------------------------------------------
# App-auth token cache
# ---------------------------------------------------------------------------


async def test_app_auth_caches_installation_token_across_cycles(
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    db_session: AsyncSession,
    github_http: httpx.AsyncClient,
    fake_rsa_pem: str,
) -> None:
    _install_strategy(app, github_http, mode="app", pem=fake_rsa_pem)
    task_a = await _seed_task(db_session, ref="T-int-cache-a")
    task_b = await _seed_task(db_session, ref="T-int-cache-b")

    with respx.mock(base_url="https://api.github.com") as mock:
        install_lookup = mock.get("/repos/foo/bar/installation").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )
        token_fetch = mock.post("/app/installations/999/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "ghs_cached", "expires_at": _future_iso()}
            )
        )
        create_a = mock.post("/repos/foo/bar/check-runs").mock(
            side_effect=[
                httpx.Response(201, json={"id": 100}),
                httpx.Response(201, json={"id": 101}),
            ]
        )
        mock.patch("/repos/foo/bar/check-runs/100").mock(
            return_value=httpx.Response(200, json={})
        )
        mock.patch("/repos/foo/bar/check-runs/101").mock(
            return_value=httpx.Response(200, json={})
        )

        await _submit_and_approve(client, api_key, task_a.id, pr_number=7)
        await _submit_and_approve(client, api_key, task_b.id, pr_number=8)

        # App-auth flow fetched the token exactly once — the second cycle
        # reused the cache.
        assert install_lookup.call_count == 1
        assert token_fetch.call_count == 1
        assert create_a.call_count == 2
