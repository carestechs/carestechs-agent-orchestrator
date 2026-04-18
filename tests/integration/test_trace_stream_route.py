"""Integration tests for ``GET /api/v1/runs/{id}/trace`` (T-083)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus
from app.modules.ai.trace import NoopTraceStore, get_trace_store
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


async def _complete_three_step_run(env: Any) -> uuid.UUID:
    resp = await env.client.post(
        "/api/v1/runs",
        json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
        headers=env.auth_headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = uuid.UUID(resp.json()["data"]["id"])
    env.run_ids.append(run_id)
    run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
    assert run.status == RunStatus.COMPLETED
    return run_id


async def _collect_stream(
    env: Any, run_id: uuid.UUID, **params: Any
) -> list[str]:
    async with env.client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/trace",
        params=params,
        headers=env.auth_headers,
        timeout=5.0,
    ) as resp:
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        return [line async for line in resp.aiter_lines() if line]


_SCRIPT: list[Any] = [
    ("analyze_brief", {"brief": "hi"}),
    ("draft_plan", {}),
    ("review_plan", {}),
]


@pytest.mark.usefixtures("fresh_pool")
class TestTraceStreamRoute:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_completed_run_stream_yields_every_record(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"

        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            lines = await _collect_stream(env, run_id)

        records = [json.loads(line) for line in lines]
        kinds = [r["kind"] for r in records]
        assert kinds.count("step") >= 3
        assert kinds.count("policy_call") >= 3
        assert kinds.count("webhook_event") >= 3
        for r in records:
            assert "data" in r

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_run_returns_404_problem_details(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=[],
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            random_id = uuid.uuid4()
            resp = await env.client.get(
                f"/api/v1/runs/{random_id}/trace",
                headers=env.auth_headers,
            )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["status"] == 404
        assert body["type"].endswith("not-found")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_kind_filter_narrows_to_steps_only(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            lines = await _collect_stream(env, run_id, kind="step")
        kinds = [json.loads(line)["kind"] for line in lines]
        assert kinds
        assert all(k == "step" for k in kinds)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_kind_filter_accepts_multiple(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            async with env.client.stream(
                "GET",
                f"/api/v1/runs/{run_id}/trace",
                params=[("kind", "step"), ("kind", "policy_call")],
                headers=env.auth_headers,
                timeout=5.0,
            ) as resp:
                lines = [line async for line in resp.aiter_lines() if line]
        kinds = [json.loads(line)["kind"] for line in lines]
        assert kinds
        assert set(kinds) <= {"step", "policy_call"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_since_filter_excludes_earlier_records(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            lines = await _collect_stream(env, run_id, since=future)
        # Records without a timestamp pass through (lower-bound semantics),
        # but timestamped records in the past must be excluded.  The sample
        # agent's 3 policy calls carry created_at, so we expect the stream
        # to drop at least those 3 records.
        kinds = [json.loads(line)["kind"] for line in lines]
        assert "policy_call" not in kinds

    @pytest.mark.asyncio(loop_scope="function")
    async def test_noop_backend_returns_empty_stream(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            # Flip the trace-store dep to Noop and stream.
            env.app.dependency_overrides[get_trace_store] = lambda: NoopTraceStore()
            async with env.client.stream(
                "GET",
                f"/api/v1/runs/{run_id}/trace",
                headers=env.auth_headers,
                timeout=3.0,
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith(
                    "application/x-ndjson"
                )
                lines = [line async for line in resp.aiter_lines() if line]
        assert lines == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_follow_on_completed_run_closes_cleanly(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        agents_dir = prepare_agents_dir(tmp_path / "agents")
        trace_dir = tmp_path / "trace"
        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=_SCRIPT,
            webhook_signer=webhook_signer,
            api_key=API_KEY,
        ) as env:
            run_id = await _complete_three_step_run(env)
            lines = await _collect_stream(env, run_id, follow="true")
        # Must have at least the 9 records the runtime wrote, and the
        # stream must have closed (no timeout).
        assert len(lines) >= 9
