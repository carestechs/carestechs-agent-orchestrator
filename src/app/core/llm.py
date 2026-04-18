"""Provider-agnostic LLM client factory and Protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.exceptions import ProviderError

# ---------------------------------------------------------------------------
# Public types — the only shapes service code touches
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A tool the policy can invoke (maps 1:1 to a candidate node)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True, slots=True)
class Usage:
    """Token / latency accounting for a single LLM call."""

    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    """The LLM's decision: which tool (node) to invoke and with what args."""

    name: str
    arguments: dict[str, Any]
    usage: Usage
    raw_response: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

_ZERO_USAGE = Usage(input_tokens=0, output_tokens=0, latency_ms=0)


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract for policy-via-tool-calling."""

    name: str
    model: str

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall: ...


# ---------------------------------------------------------------------------
# Scripted call type
# ---------------------------------------------------------------------------

ScriptedCall = tuple[str, dict[str, Any]] | Callable[[Sequence[ToolDefinition]], tuple[str, dict[str, Any]]]

# ---------------------------------------------------------------------------
# Stub provider
# ---------------------------------------------------------------------------

_MAX_PICK_FIRST = 10_000


class StubLLMProvider:
    """Deterministic provider that replays a scripted sequence of tool calls.

    One instance per run in tests — do not share across concurrent tasks.
    """

    name: str = "stub"
    model: str = "stub-v1"

    def __init__(self, script: Sequence[ScriptedCall]) -> None:
        self._script = list(script)
        self._index = 0

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall:
        if self._index >= len(self._script):
            raise ProviderError("stub-policy-exhausted")

        entry = self._script[self._index]
        self._index += 1

        if callable(entry):
            tool_name, arguments = entry(tools)
        else:
            tool_name, arguments = entry

        # Validate the scripted tool is in the available set
        available = {t.name for t in tools}
        if tool_name not in available:
            raise ProviderError(f"stub-tool-not-available: {tool_name} not in {available}")

        return ToolCall(
            name=tool_name,
            arguments=arguments,
            usage=_ZERO_USAGE,
            raw_response=None,
        )

    @classmethod
    def pick_first_available(cls) -> StubLLMProvider:
        """Return a stub that always picks the first available tool."""

        def _pick_first(tools: Sequence[ToolDefinition]) -> tuple[str, dict[str, Any]]:
            return (tools[0].name, {})

        return cls(script=[_pick_first] * _MAX_PICK_FIRST)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_provider(settings: object) -> LLMProvider:
    """Dispatch on ``Settings.llm_provider`` to build a provider instance.

    Accepts ``settings`` as ``object`` to avoid importing ``Settings`` at the
    type level — callers pass a real ``Settings`` at runtime.
    """
    provider: str = getattr(settings, "llm_provider", "stub")

    match provider:
        case "stub":
            return StubLLMProvider(script=[])
        case "anthropic":
            # Deferred import keeps the ``anthropic`` SDK out of the default
            # import graph for stub-only deployments.
            from app.config import Settings as _SettingsType
            from app.core.llm_anthropic import AnthropicLLMProvider

            assert isinstance(settings, _SettingsType)
            return AnthropicLLMProvider(settings)
        case _:
            raise ProviderError(f"unknown llm_provider: {provider}")
