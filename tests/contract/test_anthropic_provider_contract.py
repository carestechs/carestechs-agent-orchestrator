"""Live Anthropic provider contract test (T-075).

Skipped by default — opt in with ``--run-live`` AND a reachable
``ANTHROPIC_API_KEY``.  Exists so a scheduled CI job can validate our
provider against the real API without forcing it into every local run.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.core.llm import ToolDefinition
from app.core.llm_anthropic import AnthropicLLMProvider


def _skip_if_no_live() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "set ANTHROPIC_API_KEY to run the live Anthropic contract test"
        )


@pytest.mark.live
@pytest.mark.asyncio(loop_scope="function")
async def test_chat_with_tools_roundtrip() -> None:
    """Happy-path round-trip: a single ``echo`` tool invocation against the
    real API returns a well-formed :class:`ToolCall` with populated
    telemetry.
    """
    _skip_if_no_live()

    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/unused",  # type: ignore[arg-type]
        orchestrator_api_key="unused",  # type: ignore[arg-type]
        engine_webhook_secret="unused",  # type: ignore[arg-type]
        engine_base_url="http://unused.test",  # type: ignore[arg-type]
        llm_provider="anthropic",
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],  # type: ignore[arg-type]
    )
    provider = AnthropicLLMProvider(settings)

    echo_tool = ToolDefinition(
        name="echo",
        description="Echo back the provided text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    result = await provider.chat_with_tools(
        system=(
            "You MUST call the echo tool with text='hello'. "
            "Do not call any other tool. Do not emit plain text."
        ),
        messages=[{"role": "user", "content": "Please echo 'hello'."}],
        tools=[echo_tool],
    )

    assert result.name == "echo"
    echoed = str(result.arguments.get("text", "")).strip().strip("'\"").lower()
    assert echoed == "hello", f"unexpected echo: {result.arguments!r}"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.raw_response is not None
    assert "id" in result.raw_response
