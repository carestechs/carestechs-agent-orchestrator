"""Tests for app.core.llm: StubLLMProvider, factory, types."""

from __future__ import annotations

import pytest

from app.core.exceptions import ProviderError
from app.core.llm import (
    LLMProvider,
    StubLLMProvider,
    ToolCall,
    ToolDefinition,
    get_llm_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOLS = [
    ToolDefinition(name="do_x", description="Does X", parameters={}),
    ToolDefinition(name="do_y", description="Does Y", parameters={"k": {"type": "int"}}),
    ToolDefinition(name="do_z", description="Does Z", parameters={}),
]

_CALL_KWARGS = {"system": "", "messages": [], "tools": _TOOLS}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScriptedTuple:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_scripted_call(self) -> None:
        stub = StubLLMProvider(script=[("do_x", {"k": 1})])
        result = await stub.chat_with_tools(**_CALL_KWARGS)
        assert isinstance(result, ToolCall)
        assert result.name == "do_x"
        assert result.arguments == {"k": 1}
        assert result.usage.input_tokens == 0
        assert result.raw_response is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exhaustion_raises(self) -> None:
        stub = StubLLMProvider(script=[("do_x", {})])
        await stub.chat_with_tools(**_CALL_KWARGS)
        with pytest.raises(ProviderError, match="stub-policy-exhausted"):
            await stub.chat_with_tools(**_CALL_KWARGS)


class TestCallableEntry:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_callable_inspects_tools(self) -> None:
        stub = StubLLMProvider(script=[lambda tools: (tools[1].name, {"a": 2})])
        result = await stub.chat_with_tools(**_CALL_KWARGS)
        assert result.name == "do_y"
        assert result.arguments == {"a": 2}


class TestToolGating:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_tool_raises(self) -> None:
        stub = StubLLMProvider(script=[("nonexistent", {})])
        with pytest.raises(ProviderError, match="stub-tool-not-available"):
            await stub.chat_with_tools(**_CALL_KWARGS)


class TestPickFirstAvailable:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_picks_first_tool(self) -> None:
        stub = StubLLMProvider.pick_first_available()
        for _ in range(3):
            result = await stub.chat_with_tools(**_CALL_KWARGS)
            assert result.name == "do_x"
            assert result.arguments == {}


class TestProtocol:
    def test_stub_satisfies_protocol(self) -> None:
        stub = StubLLMProvider(script=[])
        assert isinstance(stub, LLMProvider)


class TestFactory:
    def test_stub_provider(self) -> None:
        class FakeSettings:
            llm_provider = "stub"

        provider = get_llm_provider(FakeSettings())
        assert isinstance(provider, StubLLMProvider)

    def test_unknown_provider_raises(self) -> None:
        class FakeSettings:
            llm_provider = "openai"

        with pytest.raises(ProviderError, match="unknown llm_provider"):
            get_llm_provider(FakeSettings())

    # Note: anthropic factory dispatch is covered by
    # ``tests/modules/core/test_llm_anthropic_factory.py``.  The structural
    # guarantee that ``app.core.llm`` does not pull in the anthropic SDK
    # (deferred import inside the ``case`` branch) is enforced by the
    # adapter-thin quarantine test in ``tests/test_adapters_are_thin.py``.
