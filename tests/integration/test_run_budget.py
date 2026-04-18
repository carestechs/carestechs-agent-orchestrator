"""Integration tests for budget exhaustion (T-058)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import PolicyCall, Step
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal

_BUDGETED_AGENT_YAML = """
ref: budgeted-agent
version: "1.0"
description: "agent with tight step budget, no terminal node reachable"
nodes:
  - name: analyze_brief
    description: analyze the brief
    inputSchema: {type: object, properties: {brief: {type: string}}, required: [brief]}
  - name: draft_plan
    description: produce a plan
    inputSchema: {type: object}
  - name: review_plan
    description: review the plan (terminal)
    inputSchema: {type: object}
flow:
  entryNode: analyze_brief
  transitions:
    analyze_brief: [draft_plan]
    draft_plan: [review_plan]
    review_plan: []
terminalNodes: [review_plan]
intakeSchema:
  type: object
  properties: {brief: {type: string}}
  required: [brief]
defaultBudget:
  maxSteps: 2
""".strip()


def _write_budgeted_agent(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "budgeted-agent@1.0.yaml").write_text(_BUDGETED_AGENT_YAML)


@pytest.mark.asyncio(loop_scope="function")
async def test_max_steps_budget_stops_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    agents_dir = tmp_path / "agents"
    _write_budgeted_agent(agents_dir)
    trace_dir = tmp_path / "trace"

    # Script 4 non-terminal steps — never reaches ``review_plan`` so
    # ``done_node`` never fires; the budget trips first.
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
        ],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={
                "agentRef": "budgeted-agent@1.0",
                "intake": {"brief": "hi"},
            },
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        assert run.status == RunStatus.FAILED
        assert run.stop_reason == StopReason.BUDGET_EXCEEDED
        assert run.final_state is not None
        assert run.final_state.get("step_count") == 2
        assert run.final_state.get("max_steps") == 2

        async with env.session_factory() as session:
            steps = list(
                (
                    await session.execute(
                        select(Step).where(Step.run_id == run_id)
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
        assert len(steps) == 2
        assert len(policy_calls) == 2
