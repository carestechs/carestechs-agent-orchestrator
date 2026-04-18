"""Parameterized tests for every /api/v1/* control-plane route.

For each endpoint we assert three branches:

- **Unauthenticated** → 401 Problem Details (the Bearer check short-circuits
  before any service logic runs, even for stubs).
- **Authenticated + valid input** → 501 Problem Details with the
  ``.../problems/not-implemented`` type URI (service still raises
  ``NotImplementedYet``).
- **Authenticated + invalid input** → 400 Problem Details with a per-field
  ``errors`` dict (POST bodies only).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

_RUN_ID = "00000000-0000-0000-0000-000000000001"

# (method, path, json_body) — json_body is only used for POST endpoints.
_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/api/v1/runs", None),
    ("POST", "/api/v1/runs", {"agentRef": "demo@1.0", "intake": {"brief": "hi"}}),
    ("GET", f"/api/v1/runs/{_RUN_ID}", None),
    ("POST", f"/api/v1/runs/{_RUN_ID}/cancel", {"reason": "test"}),
    ("GET", f"/api/v1/runs/{_RUN_ID}/steps", None),
    ("GET", f"/api/v1/runs/{_RUN_ID}/policy-calls", None),
    ("GET", f"/api/v1/runs/{_RUN_ID}/trace", None),
    ("GET", "/api/v1/agents", None),
]

# Endpoints that are still stubbed and return 501 for the "authenticated +
# valid input" case.  The full control plane is now live (last entry,
# ``/runs/{id}/trace``, graduated in T-080 / FEAT-004).  Keep the list as
# an empty regression guard — any future stub reintroduced must re-populate
# this list to be tested here.
_STUBBED_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = []


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        _ENDPOINTS,
        ids=[f"{m} {p}" for m, p, _ in _ENDPOINTS],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_bearer_returns_401(
        self,
        client: AsyncClient,
        method: str,
        path: str,
        body: dict[str, object] | None,
    ) -> None:
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 401, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        problem = resp.json()
        assert problem["status"] == 401
        assert problem["type"].endswith("/unauthorized")


# ---------------------------------------------------------------------------
# Authenticated + valid → 501
# ---------------------------------------------------------------------------


class TestAuthenticatedStub501:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        _STUBBED_ENDPOINTS,
        ids=[f"{m} {p}" for m, p, _ in _STUBBED_ENDPOINTS],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_501_not_implemented(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        method: str,
        path: str,
        body: dict[str, object] | None,
    ) -> None:
        resp = await client.request(method, path, json=body, headers=auth_headers)
        assert resp.status_code == 501, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        problem = resp.json()
        assert problem["status"] == 501
        assert problem["type"].endswith("/not-implemented")


# ---------------------------------------------------------------------------
# Validation errors → 400
# ---------------------------------------------------------------------------


_INVALID_BODIES: list[tuple[str, str, dict[str, object]]] = [
    # POST /runs missing required agentRef + intake
    ("POST", "/api/v1/runs", {}),
    # POST /runs with wrong type
    ("POST", "/api/v1/runs", {"agentRef": 123, "intake": "not-a-dict"}),
]


class TestValidationErrors:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        _INVALID_BODIES,
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_body_returns_400_with_errors(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        method: str,
        path: str,
        body: dict[str, object],
    ) -> None:
        resp = await client.request(method, path, json=body, headers=auth_headers)
        assert resp.status_code == 400, resp.text
        problem = resp.json()
        assert problem["status"] == 400
        assert problem["type"].endswith("/validation-error")
        assert "errors" in problem
        assert isinstance(problem["errors"], dict)


# ---------------------------------------------------------------------------
# Path-parameter validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_uuid_path_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.get("/api/v1/runs/not-a-uuid", headers=auth_headers)
        assert resp.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_uuid_reaches_service(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        valid = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/runs/{valid}", headers=auth_headers)
        # Service is now live (T-041); unknown id → 404 Not Found.
        assert resp.status_code == 404
