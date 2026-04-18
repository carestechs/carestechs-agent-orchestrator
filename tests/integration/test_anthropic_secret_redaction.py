"""Secret-never-leaks guardrail (T-074).

Runs one iteration with an API key carrying a sentinel marker and asserts
the marker never appears in ``PolicyCall.raw_response``, the JSONL trace
file, or captured logs.
"""

from __future__ import annotations

import json
import logging
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
from app.modules.ai.models import PolicyCall
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}

_SECRET = "sk-ant-SECRET_MARKER_test_only_" + "x" * 40


def _tool_use_response() -> dict[str, Any]:
    return {
        "id": "msg_redact",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": [
            {
                "type": "tool_use",
                "id": "tu_redact",
                "name": "analyze_brief",
                "input": {"brief": "hi"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_api_key_never_appears_in_trace_policy_call_or_logs(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    settings = Settings(
        **_BASE_KW,  # type: ignore[arg-type]
        llm_provider="anthropic",
        anthropic_api_key=_SECRET,
    )
    provider = AnthropicLLMProvider(settings)

    with (
        respx.mock(base_url="https://api.anthropic.com") as anthropic_mock,
        caplog.at_level(logging.DEBUG),
    ):
        anthropic_mock.post("/v1/messages").mock(
            return_value=httpx.Response(200, json=_tool_use_response())
        )

        async with integration_env(
            engine,
            agents_dir=agents_dir,
            trace_dir=trace_dir,
            policy_script=[],  # overridden by ``policy``
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
            run_id = uuid.UUID(resp.json()["data"]["id"])
            env.run_ids.append(run_id)

            # Wait for at least one PolicyCall to be persisted; the run will
            # eventually fail (single-response mock exhausts after turn 1) but
            # that's fine — we only need one PolicyCall to prove redaction.
            await poll_until_terminal(env, run_id, timeout_seconds=5.0)

            async with env.session_factory() as session:
                calls = list(
                    (
                        await session.execute(
                            select(PolicyCall).where(PolicyCall.run_id == run_id)
                        )
                    ).scalars()
                )

        # --- DB: PolicyCall.raw_response ---
        assert len(calls) >= 1
        for call in calls:
            dumped = json.dumps(call.raw_response or {})
            assert _SECRET not in dumped, "API key leaked into PolicyCall.raw_response"

        # --- Trace file ---
        trace_path = trace_dir / f"{run_id}.jsonl"
        assert trace_path.is_file()
        trace_contents = trace_path.read_text()
        assert _SECRET not in trace_contents, "API key leaked into JSONL trace"

        # --- Logs ---
        assert _SECRET not in caplog.text, "API key leaked into logs"
