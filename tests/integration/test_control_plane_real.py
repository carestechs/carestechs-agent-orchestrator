"""End-to-end control-plane real-data tests (AC-5, T-060).

Drives a run to completion through the full stack, then hits every
read-side endpoint.  Asserts envelope shape, pagination meta, camelCase
aliases, and last-step summary correctness.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


def _assert_envelope(
    body: dict[str, Any], *, has_meta: bool, is_collection: bool
) -> None:
    assert "data" in body
    if has_meta:
        assert "meta" in body
        meta_keys = set(body["meta"].keys())
        assert {"totalCount", "page", "pageSize"}.issubset(meta_keys)
    if is_collection:
        assert isinstance(body["data"], list)


@pytest.mark.asyncio(loop_scope="function")
async def test_control_plane_reads_after_real_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """One full happy-path run; then each read endpoint."""
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
            ("review_plan", {}),
        ],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
        # Start + complete the run.
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)
        await poll_until_terminal(env, run_id, timeout_seconds=5.0)

        # ---- GET /api/v1/runs (list + filters) ----------------------------
        resp = await env.client.get("/api/v1/runs", headers=env.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, has_meta=True, is_collection=True)
        assert body["meta"]["totalCount"] >= 1
        assert any(r["id"] == str(run_id) for r in body["data"])
        # camelCase aliases present
        first = body["data"][0]
        assert "agentRef" in first
        assert "startedAt" in first

        resp = await env.client.get(
            "/api/v1/runs",
            params={"status": "completed"},
            headers=env.auth_headers,
        )
        assert resp.status_code == 200
        assert all(r["status"] == "completed" for r in resp.json()["data"])

        resp = await env.client.get(
            "/api/v1/runs",
            params={"agentRef": "sample-linear@1.0"},
            headers=env.auth_headers,
        )
        assert resp.status_code == 200
        assert all(r["agentRef"] == "sample-linear@1.0" for r in resp.json()["data"])

        # ---- GET /api/v1/runs/{id} (detail) -------------------------------
        resp = await env.client.get(
            f"/api/v1/runs/{run_id}", headers=env.auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, has_meta=False, is_collection=False)
        detail = body["data"]
        assert detail["id"] == str(run_id)
        assert detail["status"] == "completed"
        assert detail["stepCount"] == 3
        assert detail["lastStep"] is not None
        assert detail["lastStep"]["nodeName"] == "review_plan"
        assert detail["lastStep"]["stepNumber"] == 3

        # ---- GET /api/v1/runs/{id}/steps (paginated) ----------------------
        resp = await env.client.get(
            f"/api/v1/runs/{run_id}/steps",
            params={"pageSize": 2},
            headers=env.auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, has_meta=True, is_collection=True)
        assert body["meta"]["totalCount"] == 3
        assert body["meta"]["pageSize"] == 2
        assert len(body["data"]) == 2
        assert body["data"][0]["stepNumber"] == 1
        assert "nodeInputs" in body["data"][0]

        resp = await env.client.get(
            f"/api/v1/runs/{run_id}/steps",
            params={"pageSize": 2, "page": 2},
            headers=env.auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["stepNumber"] == 3

        # ---- GET /api/v1/runs/{id}/policy-calls ---------------------------
        resp = await env.client.get(
            f"/api/v1/runs/{run_id}/policy-calls", headers=env.auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, has_meta=True, is_collection=True)
        calls = body["data"]
        assert len(calls) == 3
        assert [c["selectedTool"] for c in calls] == [
            "analyze_brief",
            "draft_plan",
            "review_plan",
        ]
        # ASC order by created_at
        timestamps = [c["createdAt"] for c in calls]
        assert timestamps == sorted(timestamps)

        # ---- GET /api/v1/agents -------------------------------------------
        resp = await env.client.get("/api/v1/agents", headers=env.auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        _assert_envelope(body, has_meta=False, is_collection=True)
        agents = body["data"]
        assert len(agents) == 1
        a = agents[0]
        assert a["ref"] == "sample-linear@1.0"
        assert "definitionHash" in a
        assert "availableNodes" in a
        assert {"analyze_brief", "draft_plan", "review_plan"}.issubset(
            set(a["availableNodes"])
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_list_runs_filter_isolates_cancelled_from_completed(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """Seed a completed run and a cancelled run; ensure status filter returns
    only the expected subset."""
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    # ---- run #1: completes happily ---------------------------------------
    # Deliberately do NOT append completed_id to env.run_ids — the second
    # env needs the row to survive so we can query it by status.
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
            ("review_plan", {}),
        ],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        completed_id = uuid.UUID(resp.json()["data"]["id"])
        await poll_until_terminal(env, completed_id, timeout_seconds=5.0)

    # ---- run #2: cancel mid-flight ---------------------------------------
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[("analyze_brief", {"brief": "hi"})],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        engine_delay_seconds=5.0,
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        cancelled_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(cancelled_id)

        # Wait for dispatch then cancel.
        import asyncio

        for _ in range(100):
            if env.engine_echo.dispatches:
                break
            await asyncio.sleep(0.02)
        await env.client.post(
            f"/api/v1/runs/{cancelled_id}/cancel",
            json={"reason": "abort"},
            headers=env.auth_headers,
        )
        await poll_until_terminal(env, cancelled_id, timeout_seconds=3.0)

        # ---- query back with filter --------------------------------------
        resp = await env.client.get(
            "/api/v1/runs",
            params={"status": "cancelled"},
            headers=env.auth_headers,
        )
        data = resp.json()["data"]
        ids = [r["id"] for r in data]
        assert str(cancelled_id) in ids
        assert str(completed_id) not in ids

        resp = await env.client.get(
            "/api/v1/runs",
            params={"status": "completed"},
            headers=env.auth_headers,
        )
        data = resp.json()["data"]
        ids = [r["id"] for r in data]
        assert str(completed_id) in ids
        assert str(cancelled_id) not in ids

        # Also extend run_ids with the completed run so its rows get cleaned.
        env.run_ids.append(completed_id)
