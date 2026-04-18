"""Multi-turn ``tool_use`` / ``tool_result`` threading invariant (T-071).

The runtime loop today sends one user-content message per iteration; this
test pins the provider's "no transformation" contract on the ``messages``
parameter so a future feature that plumbs real conversations through the
runtime can rely on it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.llm import ToolDefinition
from app.core.llm_anthropic import AnthropicLLMProvider

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}


def _settings() -> Settings:
    return Settings(
        **_BASE_KW,  # type: ignore[arg-type]
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-xxx",
    )


def _tool_use_response(
    *, tool_use_id: str, name: str, tool_input: dict[str, Any] | None = None
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
                "name": name,
                "input": tool_input or {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


_ANALYZE = ToolDefinition(
    name="analyze_brief",
    description="analyze",
    parameters={"type": "object", "properties": {}},
)
_DRAFT = ToolDefinition(
    name="draft_plan",
    description="draft",
    parameters={"type": "object", "properties": {}},
)


class TestToolResultThreading:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_two_turns_thread_tool_use_and_tool_result(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json=_tool_use_response(
                            tool_use_id="tu_1", name="analyze_brief"
                        ),
                    ),
                    httpx.Response(
                        200,
                        json=_tool_use_response(
                            tool_use_id="tu_2", name="draft_plan"
                        ),
                    ),
                ]
            )

            # Turn 1: no prior turns.
            turn1 = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "start"}],
                tools=[_ANALYZE, _DRAFT],
            )
            assert turn1.name == "analyze_brief"
            turn1_content = turn1.raw_response["content"] if turn1.raw_response else []
            turn1_tool_use_id = turn1_content[0]["id"]
            assert turn1_tool_use_id == "tu_1"

            # Turn 2: caller threads the prior tool_use + a tool_result.
            messages_turn2 = [
                {"role": "user", "content": "start"},
                {"role": "assistant", "content": turn1_content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": turn1_tool_use_id,
                            "content": '{"ok": true}',
                        }
                    ],
                },
            ]
            await provider.chat_with_tools(
                system="SYS",
                messages=messages_turn2,
                tools=[_ANALYZE, _DRAFT],
            )

        # The second outbound body must contain our messages verbatim.
        second_body = json.loads(route.calls[1].request.content)
        assert second_body["messages"] == messages_turn2

        # And the tool_result's tool_use_id matches turn 1's tool_use id.
        last_user_msg = second_body["messages"][-1]
        assert last_user_msg["content"][0]["tool_use_id"] == "tu_1"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_provider_does_not_add_terminate_tool(self) -> None:
        """Only caller-supplied tools appear in the outbound payload."""
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_tool_use_response(tool_use_id="tu_1", name="analyze_brief"),
                )
            )
            await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "go"}],
                tools=[_ANALYZE],  # No terminate, no draft.
            )
        body = json.loads(route.calls.last.request.content)
        names = [t["name"] for t in body["tools"]]
        assert names == ["analyze_brief"]
        assert "terminate" not in names
