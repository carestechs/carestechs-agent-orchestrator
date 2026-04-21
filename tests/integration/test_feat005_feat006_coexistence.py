"""AC-7: FEAT-005 and FEAT-006 routes coexist in a single process.

FEAT-007 adds a new FastAPI dependency (``get_github_checks_client_dep``)
and wires it into three FEAT-006 routes.  A DI collision between the
GitHub dep and FEAT-005's supervisor dep would manifest as a 500 on
either surface.  This test drives both surfaces against the same ``app``
fixture and asserts neither regresses.

It does *not* drive a full FEAT-005 agent run — that's already covered
by ``test_run_end_to_end.py`` with its own isolated environment.  Here
we just prove the combined router + lifespan can hand out runs *and*
accept lifecycle signals in the same process.
"""

from __future__ import annotations

import pytest
import respx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.ai.dependencies import get_github_checks_client_dep
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.github.checks import NoopGitHubChecksClient
from app.modules.ai.models import Task
from tests.integration._reactor_helpers import await_work_item_status

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


@pytest.fixture(autouse=True)
def _force_solo_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLO_DEV_MODE", "true")
    for var in ("GITHUB_PAT", "GITHUB_APP_ID", "GITHUB_PRIVATE_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


async def test_feat005_routes_still_reachable_after_github_di(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    """FEAT-005 list-runs endpoint keeps working alongside the new DI."""
    app.dependency_overrides[get_github_checks_client_dep] = (
        lambda: NoopGitHubChecksClient()
    )
    r = await client.get("/api/v1/runs", headers=_h(api_key))
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    # No runs seeded — empty list is correct; the point is the route
    # resolves its deps without 500'ing.
    assert isinstance(body["data"], list)


async def test_feat005_and_feat006_routes_together(
    app: FastAPI, client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    """Both API surfaces respond cleanly in the same app instance."""
    app.dependency_overrides[get_github_checks_client_dep] = (
        lambda: NoopGitHubChecksClient()
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="api.github.com").mock(
            side_effect=AssertionError("noop must not call GitHub")
        )

        # FEAT-005 surface — list runs (empty).
        r = await client.get("/api/v1/runs?pageSize=5", headers=_h(api_key))
        assert r.status_code == 200

        # FEAT-006 surface — open a work item, approve a seeded task.
        r = await client.post(
            "/api/v1/work-items",
            json={
                "externalRef": "FEAT-COEX",
                "type": "FEAT",
                "title": "coexistence",
            },
            headers=_h(api_key),
        )
        assert r.status_code == 202, r.text
        wi_id = r.json()["data"]["id"]

        task = Task(
            work_item_id=wi_id,
            external_ref="T-coex-1",
            title="t",
            status=TaskStatus.PROPOSED.value,
            proposer_type="admin",
            proposer_id="admin",
        )
        db_session.add(task)
        await db_session.commit()
        await db_session.refresh(task)

        r = await client.post(
            f"/api/v1/tasks/{task.id}/approve",
            json={},
            headers=_h(api_key),
        )
        assert r.status_code == 202, r.text

        # FEAT-005 surface still reachable — list runs again.
        r = await client.get("/api/v1/runs", headers=_h(api_key))
        assert r.status_code == 200

        # No GitHub traffic from either surface.
        hosts = [c.request.url.host for c in mock.calls]  # type: ignore[attr-defined]
        assert "api.github.com" not in hosts

    # Final state sanity: approval fired W2, work item is in_progress.
    wi = await await_work_item_status(
        db_session, wi_id, WorkItemStatus.IN_PROGRESS.value
    )
    assert wi is not None
