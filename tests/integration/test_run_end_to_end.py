"""End-to-end composition-integrity test — AC-1 + AC-6 headliner (T-054).

A scripted :class:`StubLLMProvider` drives the sample-linear agent through
three steps — ``analyze_brief → draft_plan → review_plan`` — and the last
node (terminal) triggers ``stop_reason=done_node``.  The engine is mocked
by :class:`EngineEcho`, which fires a webhook back into the same ASGI app
via an in-process client.  No LLM, no real engine, but every other moving
part (webhook receiver, reconciliation, supervisor wake-up, memory merge,
JSONL trace) is the production code.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus, StepStatus, StopReason
from app.modules.ai.models import PolicyCall, RunMemory, Step
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


@pytest.mark.asyncio(loop_scope="function")
async def test_linear_agent_completes_with_done_node(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    script = [
        ("analyze_brief", {"brief": "hi"}),
        ("draft_plan", {}),
        ("review_plan", {}),
    ]

    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=script,
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
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
        assert run.stop_reason == StopReason.DONE_NODE
        assert run.ended_at is not None

        async with env.session_factory() as session:
            steps = list(
                (
                    await session.execute(
                        select(Step)
                        .where(Step.run_id == run_id)
                        .order_by(Step.step_number)
                    )
                ).scalars()
            )
            policy_calls = list(
                (
                    await session.execute(
                        select(PolicyCall).where(PolicyCall.run_id == run_id)
                    )
                ).scalars()
            )
            memory = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run_id)
            )

        assert [s.node_name for s in steps] == [
            "analyze_brief",
            "draft_plan",
            "review_plan",
        ]
        assert all(s.status == StepStatus.COMPLETED for s in steps)
        assert len(policy_calls) == 3
        assert memory is not None
        # Every step wrote its own key into memory.
        assert set(memory.data.keys()) == {"analyze_brief", "draft_plan", "review_plan"}

        # JSONL trace: at minimum 3 step + 3 policy + 3 webhook_event lines.
        trace_path = trace_dir / f"{run_id}.jsonl"
        assert trace_path.is_file()
        lines = trace_path.read_text().splitlines()
        kinds = [json.loads(line)["kind"] for line in lines if line]
        assert kinds.count("step") >= 3
        assert kinds.count("policy_call") >= 3
        assert kinds.count("webhook_event") >= 3


@pytest.mark.asyncio(loop_scope="function")
async def test_exhausted_script_surfaces_error_stop(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """Empty policy script → ProviderError on first call → run fails."""
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[],  # exhausted immediately
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        assert run.status == RunStatus.FAILED
        assert run.stop_reason == StopReason.ERROR
        assert run.final_state is not None
        assert "policy_error" in run.final_state
