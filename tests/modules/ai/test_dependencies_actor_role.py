"""Tests for the X-Actor-Role dependency (FEAT-006 / T-114).

Note: this file intentionally does NOT use ``from __future__ import
annotations`` — FastAPI requires runtime access to ``Depends(...)`` inside
``Annotated[...]`` on the endpoint's parameter list.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.modules.ai.dependencies import require_actor_role
from app.modules.ai.enums import ActorRole


def _make_app(*allowed: ActorRole) -> FastAPI:
    app = FastAPI()

    @app.post("/probe")
    async def _probe(
        role: Annotated[ActorRole, Depends(require_actor_role(*allowed))],
    ) -> dict[str, str]:
        return {"role": role.value}

    return app


@pytest.mark.asyncio
async def test_allowed_role_passes() -> None:
    app = _make_app(ActorRole.ADMIN)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/probe", headers={"X-Actor-Role": "admin"})
    assert r.status_code == 200, r.text
    assert r.json() == {"role": "admin"}


@pytest.mark.asyncio
async def test_unknown_role_returns_400() -> None:
    from app.core.exceptions import register_exception_handlers

    app = _make_app(ActorRole.ADMIN)
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/probe", headers={"X-Actor-Role": "bogus"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_forbidden_role_returns_403() -> None:
    from app.core.exceptions import register_exception_handlers

    app = _make_app(ActorRole.ADMIN)
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/probe", headers={"X-Actor-Role": "dev"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_missing_header_returns_400_with_handler() -> None:
    from app.core.exceptions import register_exception_handlers

    app = _make_app(ActorRole.ADMIN)
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/probe")
    assert r.status_code == 400
