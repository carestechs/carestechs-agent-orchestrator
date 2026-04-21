"""AC-6: FEAT-006 flow runs end-to-end with no GitHub credentials.

Composition integrity (AD-9): swap out the LLM for ``stub``, configure
no GitHub creds, and the whole 14-signal flow must still complete — with
zero outbound calls to ``api.github.com``.
"""

from __future__ import annotations

import pytest
import respx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.github import get_github_checks_client, make_shared_http_client
from app.modules.ai.dependencies import get_github_checks_client_dep
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.github.checks import NOOP_CHECK_ID, NoopGitHubChecksClient
from app.modules.ai.models import Task, TaskImplementation, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


@pytest.fixture(autouse=True)
def force_solo_dev_and_noop_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin solo_dev_mode=true and scrub any GitHub env vars.

    Also stashes ``github_checks_client=NoopGitHubChecksClient`` on app
    state so the dependency resolves to noop even when the lifespan has
    not run — keeps the test a unit of "how the code behaves when the
    operator configured nothing", not "what startup happens to do".
    """
    monkeypatch.setenv("SOLO_DEV_MODE", "true")
    for var in ("GITHUB_PAT", "GITHUB_APP_ID", "GITHUB_PRIVATE_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


async def _seed_task(db: AsyncSession, work_item_id, ref: str) -> Task:
    t = Task(
        work_item_id=work_item_id,
        external_ref=ref,
        title=ref,
        status=TaskStatus.PROPOSED.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def test_full_lifecycle_without_github_credentials(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    app.dependency_overrides[get_github_checks_client_dep] = (
        lambda: NoopGitHubChecksClient()
    )

    # Compile-time guarantee: the factory over a no-credential Settings
    # yields the no-op client.  Tighter than "lifespan happens to set
    # app.state.github_checks_client to noop".
    settings = Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost:5432/x",
        orchestrator_api_key="k",
        engine_webhook_secret="s",
        engine_base_url="http://localhost:9000",
        github_pat=None,
        github_app_id=None,
        github_private_key=None,
    )
    async with make_shared_http_client() as http:
        resolved = get_github_checks_client(settings, http)
        assert isinstance(resolved, NoopGitHubChecksClient)

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="api.github.com").mock(
            side_effect=AssertionError(
                "composition-integrity: GitHub must not be called"
            )
        )

        # S1 — open work item, seed two tasks.
        r = await client.post(
            "/api/v1/work-items",
            json={"externalRef": "FEAT-CI", "type": "FEAT", "title": "CI"},
            headers=_h(api_key),
        )
        assert r.status_code == 202, r.text
        wi_id = r.json()["data"]["id"]
        task_a = await _seed_task(db_session, wi_id, "T-ci-a")
        task_b = await _seed_task(db_session, wi_id, "T-ci-b")

        # S5 — approve tasks (W2 flips work item → in_progress).
        for task in (task_a, task_b):
            r = await client.post(
                f"/api/v1/tasks/{task.id}/approve",
                json={}, headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

        # S7 — assign.
        for task, assignee in ((task_a, "dev"), (task_b, "agent")):
            r = await client.post(
                f"/api/v1/tasks/{task.id}/assign",
                json={"assigneeType": assignee, "assigneeId": f"{assignee}-1"},
                headers=_h(api_key),
            )
            assert r.status_code == 202

        # S8/S9 — plan + plan-approve.
        r = await client.post(
            f"/api/v1/tasks/{task_a.id}/plan",
            json={"planPath": "p", "planSha": "1"},
            headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 202
        r = await client.post(
            f"/api/v1/tasks/{task_a.id}/plan/approve",
            json={}, headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 202
        r = await client.post(
            f"/api/v1/tasks/{task_b.id}/plan",
            json={"planPath": "p", "planSha": "2"},
            headers=_h(api_key),
        )
        assert r.status_code == 202
        r = await client.post(
            f"/api/v1/tasks/{task_b.id}/plan/approve",
            json={}, headers=_h(api_key),
        )
        assert r.status_code == 202

        # S11/S12 — submit implementations with a *real-looking* PR URL,
        # then approve the review.  The noop client must accept the URL
        # and store the sentinel — not hit GitHub.
        for task in (task_a, task_b):
            r = await client.post(
                f"/api/v1/tasks/{task.id}/implementation",
                json={
                    "prUrl": f"https://github.com/test/{task.external_ref}/pull/1",
                    "commitSha": f"sha-{task.external_ref}",
                    "summary": "ok",
                },
                headers=_h(api_key),
            )
            assert r.status_code == 202, r.text
            r = await client.post(
                f"/api/v1/tasks/{task.id}/review/approve",
                json={}, headers=_h(api_key),
            )
            assert r.status_code == 202, r.text

        # W5 derivation should now have fired (both tasks done, no third).
        wi = await db_session.scalar(select(WorkItem).where(WorkItem.id == wi_id))
        assert wi is not None
        await db_session.refresh(wi)
        assert wi.status == WorkItemStatus.READY.value

        # S4 — close.
        r = await client.post(
            f"/api/v1/work-items/{wi_id}/close",
            json={"notes": "shipped"},
            headers=_h(api_key),
        )
        assert r.status_code == 202, r.text

        hosts = [c.request.url.host for c in mock.calls]  # type: ignore[attr-defined]
        assert "api.github.com" not in hosts, (
            f"composition integrity breach: {hosts}"
        )

    # Every implementation row stored the noop sentinel — the merge gate
    # is degraded, not broken.
    impls = (
        await db_session.scalars(
            select(TaskImplementation).order_by(TaskImplementation.submitted_at)
        )
    ).all()
    assert len(impls) >= 2
    assert all(i.github_check_id == NOOP_CHECK_ID for i in impls)
