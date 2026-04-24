"""Tests for FEAT-006 work-item signal endpoints (T-115)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import WorkItemStatus
from app.modules.ai.models import WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _headers(api_key: str, role: str = "admin") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Actor-Role": role,
    }


async def _seed_wi(
    db: AsyncSession,
    *,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    ref: str = "FEAT-900",
) -> WorkItem:
    wi = WorkItem(
        external_ref=ref,
        type="FEAT",
        title="Demo",
        status=status.value,
        opened_by="admin",
    )
    db.add(wi)
    await db.commit()
    await db.refresh(wi)
    return wi


class TestOpen:
    async def test_happy_path(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        resp = await client.post(
            "/api/v1/work-items",
            json={
                "externalRef": "FEAT-901",
                "type": "FEAT",
                "title": "Demo",
            },
            headers=_headers(api_key),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["externalRef"] == "FEAT-901"
        assert body["data"]["status"] == WorkItemStatus.OPEN.value
        assert body.get("meta") is None

    async def test_missing_role_returns_400(
        self, client: AsyncClient, api_key: str
    ) -> None:
        resp = await client.post(
            "/api/v1/work-items",
            json={"externalRef": "FEAT-902", "type": "FEAT", "title": "x"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert resp.status_code == 400

    async def test_wrong_role_returns_403(
        self, client: AsyncClient, api_key: str
    ) -> None:
        resp = await client.post(
            "/api/v1/work-items",
            json={"externalRef": "FEAT-903", "type": "FEAT", "title": "x"},
            headers=_headers(api_key, role="dev"),
        )
        assert resp.status_code == 403

    async def test_idempotent_replay(
        self, client: AsyncClient, api_key: str
    ) -> None:
        body = {"externalRef": "FEAT-904", "type": "FEAT", "title": "Demo"}
        r1 = await client.post(
            "/api/v1/work-items", json=body, headers=_headers(api_key)
        )
        assert r1.status_code == 202
        r2 = await client.post(
            "/api/v1/work-items", json=body, headers=_headers(api_key)
        )
        assert r2.status_code == 202
        assert r2.json().get("meta", {}).get("alreadyReceived") is True


class TestLockUnlock:
    async def test_lock_from_in_progress(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        wi = await _seed_wi(db_session, status=WorkItemStatus.IN_PROGRESS)
        resp = await client.post(
            f"/api/v1/work-items/{wi.id}/lock",
            json={"reason": "release freeze"},
            headers=_headers(api_key),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["data"]["status"] == WorkItemStatus.LOCKED.value

    async def test_lock_from_open_is_409(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        wi = await _seed_wi(db_session, status=WorkItemStatus.OPEN)
        resp = await client.post(
            f"/api/v1/work-items/{wi.id}/lock",
            json={},
            headers=_headers(api_key),
        )
        assert resp.status_code == 409, resp.text

    async def test_unlock_roundtrip(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        wi = await _seed_wi(db_session, status=WorkItemStatus.LOCKED)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/work-items/{wi.id}/unlock",
            json={},
            headers=_headers(api_key),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["data"]["status"] == WorkItemStatus.IN_PROGRESS.value


class TestClose:
    async def test_close_only_from_ready(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        wi = await _seed_wi(db_session, status=WorkItemStatus.IN_PROGRESS)
        r1 = await client.post(
            f"/api/v1/work-items/{wi.id}/close",
            json={},
            headers=_headers(api_key),
        )
        assert r1.status_code == 409

        wi.status = WorkItemStatus.READY.value
        await db_session.commit()

        r2 = await client.post(
            f"/api/v1/work-items/{wi.id}/close",
            json={"notes": "shipping"},
            headers=_headers(api_key),
        )
        assert r2.status_code == 202, r2.text
        assert r2.json()["data"]["status"] == WorkItemStatus.CLOSED.value

    async def test_close_unknown_is_404(
        self, client: AsyncClient, api_key: str
    ) -> None:
        resp = await client.post(
            f"/api/v1/work-items/{uuid.uuid4()}/close",
            json={},
            headers=_headers(api_key),
        )
        assert resp.status_code == 404
