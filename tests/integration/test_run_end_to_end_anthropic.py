"""End-to-end runtime driven by AnthropicLLMProvider (T-072).

Symmetric to ``test_run_end_to_end::test_linear_agent_completes_with_done_node``:
same runtime, same webhook receiver, same trace store — only the policy
provider changes.  Anthropic is respx-mocked at the SDK's HTTP boundary,
so there is no real network call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.core.llm_anthropic import AnthropicLLMProvider
from app.modules.ai.enums import RunStatus, StepStatus, StopReason
from app.modules.ai.models import PolicyCall, RunMemory, Step
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}


def _tool_use_response(
    tool_name: str,
    *,
    tool_use_id: str,
    tool_input: dict[str, Any] | None = None,
    input_tokens: int = 25,
    output_tokens: int = 10,
) -> dict[str, Any]:
    return {
        "id": f"msg_{tool_use_id}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": [
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": tool_input if tool_input is not None else {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_linear_agent_completes_under_anthropic_provider(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    settings = Settings(
        **_BASE_KW,  # type: ignore[arg-type]
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-xxx-aaaaaaaaaaaaaaaaaaaa",
    )
    provider = AnthropicLLMProvider(settings)

    with respx.mock(base_url="https://api.anthropic.com") as anthropic_mock:
        anthropic_route = anthropic_mock.post("/v1/messages").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_tool_use_response(
                        "analyze_brief",
                        tool_use_id="tu_1",
                        tool_input={"brief": "hi"},
                    ),
                ),
                httpx.Response(
                    200,
                    json=_tool_use_response("draft_plan", tool_use_id="tu_2"),
                ),
                httpx.Response(
                    200,
                    json=_tool_use_response("review_plan", tool_use_id="tu_3"),
                ),
            ]
        )

        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=[],  # overridden by ``policy`` below
            webhook_signer=webhook_signer,
            api_key=API_KEY,
            policy=provider,
        ) as env:
            resp = await env.client.post(
                "/api/v1/runs",
                json={
                    "agentRef": "sample-linear@1.0",
                    "intake": {"brief": "hi"},
                },
                headers=env.auth_headers,
            )
            assert resp.status_code == 202, resp.text
            run_id = uuid.UUID(resp.json()["data"]["id"])
            env.run_ids.append(run_id)

            run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)

            # Surface the failure reason if the run didn't complete cleanly.
            if run.status != RunStatus.COMPLETED:
                raise AssertionError(
                    f"run {run_id} ended {run.status!r} / stop={run.stop_reason!r}; "
                    f"final_state={run.final_state!r}; "
                    f"anthropic_calls={anthropic_route.call_count}"
                )
            assert run.status == RunStatus.COMPLETED
            assert run.stop_reason == StopReason.DONE_NODE

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
                            select(PolicyCall)
                            .where(PolicyCall.run_id == run_id)
                            .order_by(PolicyCall.created_at)
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
            # Every PolicyCall carries real Anthropic telemetry.
            for call in policy_calls:
                assert call.input_tokens > 0
                assert call.output_tokens > 0
            # ``service.provider`` currently writes ``agent.ref`` into
            # ``PolicyCall.provider``; regardless of that identifier, the
            # raw_response shape proves the real provider was used.
            for call in policy_calls:
                assert call.raw_response is not None
                assert call.raw_response.get("model") == "claude-opus-4-7"

            assert memory is not None

        assert anthropic_route.call_count == 3

        trace_path = trace_dir / f"{run_id}.jsonl"
        assert trace_path.is_file()
        lines = trace_path.read_text().splitlines()
        kinds = [json.loads(line)["kind"] for line in lines if line]
        assert kinds.count("step") >= 3
        assert kinds.count("policy_call") >= 3
        assert kinds.count("webhook_event") >= 3
