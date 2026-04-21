"""Tests for FEAT-007 merge-gating — service wiring on submit/approve/reject.

Focuses on behaviour at the signal seam:

* happy paths register + resolve the check,
* noop path stores the sentinel and does not hit GitHub,
* missing ``prUrl`` leaves ``github_check_id`` NULL,
* transient GitHub failures do not fail the signal (state machine wins),
* malformed ``prUrl`` with a real client returns 400.

Tests drive the real HTTP boundary via the test client, override the
``get_github_checks_client_dep`` FastAPI dependency to pick the client,
and mock ``api.github.com`` with ``respx``.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.dependencies import get_github_checks_client_dep
from app.modules.ai.enums import AssigneeType, TaskStatus
from app.modules.ai.github.auth import PatAuthStrategy
from app.modules.ai.github.checks import (
    NOOP_CHECK_ID,
    HttpxGitHubChecksClient,
    NoopGitHubChecksClient,
)
from app.modules.ai.models import Task, TaskAssignment, TaskImplementation, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")

_PR_URL = "https://github.com/foo/bar/pull/7"


@pytest.fixture(autouse=True)
def force_solo_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin solo_dev_mode=true so admin approves impl-review.

    Avoids drift from a local ``.env`` that sets ``SOLO_DEV_MODE=false``
    (which would require a second dev and break these tests).
    """
    from app.config import get_settings

    monkeypatch.setenv("SOLO_DEV_MODE", "true")
    get_settings.cache_clear()


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


async def _seed_task_in_impl(
    db: AsyncSession, ref: str = "T-GH1"
) -> Task:
    """Seed a task sitting at ``implementing`` with a dev assignment."""
    wi = WorkItem(
        external_ref=f"FEAT-{ref}",
        type="FEAT",
        title="gh",
        status="in_progress",
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=ref,
        title="do",
        status=TaskStatus.IMPLEMENTING.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.flush()
    db.add(
        TaskAssignment(
            task_id=task.id,
            assignee_type=AssigneeType.DEV.value,
            assignee_id="dev-1",
            assigned_by="admin",
        )
    )
    await db.commit()
    await db.refresh(task)
    return task


async def _latest_impl(
    db: AsyncSession, task_id: uuid.UUID
) -> TaskImplementation | None:
    return await db.scalar(
        select(TaskImplementation)
        .where(TaskImplementation.task_id == task_id)
        .order_by(TaskImplementation.submitted_at.desc())
    )


def _override_github(app: FastAPI, http: httpx.AsyncClient) -> None:
    client = HttpxGitHubChecksClient(auth=PatAuthStrategy("ghp_test"), http=http)
    app.dependency_overrides[get_github_checks_client_dep] = lambda: client


def _override_noop(app: FastAPI) -> None:
    app.dependency_overrides[get_github_checks_client_dep] = lambda: (
        NoopGitHubChecksClient()
    )


# ---------------------------------------------------------------------------
# Happy paths — approve
# ---------------------------------------------------------------------------


async def test_submit_then_approve_posts_check_and_resolves_success(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        _override_github(app, http)
        task = await _seed_task_in_impl(db_session, ref="T-GH-ok-approve")

        with respx.mock(base_url="https://api.github.com") as mock:
            create = mock.post("/repos/foo/bar/check-runs").mock(
                return_value=httpx.Response(201, json={"id": 999})
            )
            update = mock.patch("/repos/foo/bar/check-runs/999").mock(
                return_value=httpx.Response(200, json={})
            )

            r = await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={
                    "prUrl": _PR_URL,
                    "commitSha": "abc123",
                    "summary": "done",
                },
                headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

            r = await client.post(
                f"/api/v1/tasks/{task.id}/review/approve",
                json={},
                headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

        assert create.call_count == 1
        assert update.call_count == 1
        assert '"conclusion":"success"' in update.calls.last.request.content.decode()

        impl = await _latest_impl(db_session, task.id)
        assert impl is not None
        assert impl.github_check_id == "999"


async def test_submit_then_reject_posts_failure(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        _override_github(app, http)
        task = await _seed_task_in_impl(db_session, ref="T-GH-ok-reject")

        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/repos/foo/bar/check-runs").mock(
                return_value=httpx.Response(201, json={"id": 42})
            )
            update = mock.patch("/repos/foo/bar/check-runs/42").mock(
                return_value=httpx.Response(200, json={})
            )

            await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={"prUrl": _PR_URL, "commitSha": "abc", "summary": "x"},
                headers=_h(api_key),
            )
            r = await client.post(
                f"/api/v1/tasks/{task.id}/review/reject",
                json={"feedback": "needs work"},
                headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

        assert update.call_count == 1
        assert '"conclusion":"failure"' in update.calls.last.request.content.decode()


# ---------------------------------------------------------------------------
# Noop + missing PR URL paths
# ---------------------------------------------------------------------------


async def test_noop_client_stores_sentinel_and_calls_nothing(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    _override_noop(app)
    task = await _seed_task_in_impl(db_session, ref="T-GH-noop")
    task_id = task.id

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="api.github.com").mock(
            side_effect=AssertionError("noop path must not call GitHub")
        )
        await client.post(
            f"/api/v1/tasks/{task_id}/implementation",
            json={"prUrl": _PR_URL, "commitSha": "abc", "summary": "x"},
            headers=_h(api_key),
        )
        await client.post(
            f"/api/v1/tasks/{task_id}/review/approve",
            json={},
            headers=_h(api_key),
        )
        hosts = [c.request.url.host for c in mock.calls]  # type: ignore[attr-defined]
        assert "api.github.com" not in hosts

    impl = await _latest_impl(db_session, task_id)
    assert impl is not None
    assert impl.github_check_id == NOOP_CHECK_ID


async def test_missing_pr_url_leaves_check_id_null(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        _override_github(app, http)
        task = await _seed_task_in_impl(db_session, ref="T-GH-no-url")

        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="api.github.com").mock(
                side_effect=AssertionError("create must not fire without prUrl")
            )
            r = await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={"commitSha": "abc", "summary": "x"},  # no prUrl
                headers=_h(api_key),
            )
            assert r.status_code == 202

        impl = await _latest_impl(db_session, task.id)
        assert impl is not None
        assert impl.github_check_id is None


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_transient_5xx_on_create_does_not_fail_signal(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        _override_github(app, http)
        task = await _seed_task_in_impl(db_session, ref="T-GH-5xx")

        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/repos/foo/bar/check-runs").mock(
                return_value=httpx.Response(503, text="unavailable")
            )
            r = await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={"prUrl": _PR_URL, "commitSha": "abc", "summary": "x"},
                headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

        impl = await _latest_impl(db_session, task.id)
        # The row landed (state machine ran) but no check_id was stored.
        assert impl is not None
        assert impl.github_check_id is None


async def test_invalid_pr_url_with_real_client_returns_400(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        _override_github(app, http)
        task = await _seed_task_in_impl(db_session, ref="T-GH-bad-url")

        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="api.github.com").mock(
                side_effect=AssertionError("create must not fire on bad URL")
            )
            r = await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={
                    "prUrl": "https://gitlab.com/foo/bar/pull/1",
                    "commitSha": "abc",
                    "summary": "x",
                },
                headers=_h(api_key),
            )
            # Transition committed first, then GitHub call failed with 400.
            # AD-1: state machine wins — the rollback catch in router maps
            # ValidationError to 400 even though the state did advance.
            assert r.status_code == 400
            assert "invalid PR URL" in r.text
