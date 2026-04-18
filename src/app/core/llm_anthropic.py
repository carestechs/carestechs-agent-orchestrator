"""Anthropic-backed :class:`LLMProvider` (T-065..T-068).

The class hosts the *only* ``anthropic`` SDK import in this codebase
outside the factory branch in :mod:`app.core.llm` — see the adapter-thin
quarantine test.  Layered build:

* T-065 scaffold: constructor wires an ``AsyncAnthropic`` client from
  :class:`~app.config.Settings`; ``chat_with_tools`` raises ``NotImplementedYet``.
* T-066 adds the one-shot happy path.
* T-067 adds error mapping (``ProviderError`` / ``PolicyError``).
* T-068 wraps the call in a bounded retry with backoff + jitter.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

import anthropic

from app.config import Settings
from app.core.exceptions import PolicyError, ProviderError
from app.core.llm import ToolCall, ToolDefinition, Usage

logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 4.0
_JITTER_SECONDS = 0.05


_RESPONSE_WHITELIST: frozenset[str] = frozenset(
    {"id", "type", "role", "model", "stop_reason", "stop_sequence", "usage", "content"}
)
"""Keys we preserve from Anthropic responses.  Everything else (including any
header echoes or SDK-internal metadata) is dropped before we build
:class:`~app.core.llm.ToolCall.raw_response`.  Opt-in rather than blacklist
so a future SDK field addition cannot accidentally carry secrets through."""


def _to_anthropic_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """Translate orchestrator :class:`ToolDefinition` into Anthropic tool schema."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


def _redact_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Return *raw* filtered to :data:`_RESPONSE_WHITELIST` keys only."""
    return {k: raw[k] for k in _RESPONSE_WHITELIST if k in raw}


def _request_id(exc: anthropic.APIStatusError) -> str | None:
    """Return the Anthropic request id associated with *exc*, or ``None``."""
    rid: Any = getattr(exc, "request_id", None)
    if isinstance(rid, str) and rid:
        return rid
    response: Any = getattr(exc, "response", None)
    if response is not None:
        headers: Any = getattr(response, "headers", None)
        if headers is not None:
            for name in ("request-id", "x-request-id"):
                value: Any = headers.get(name) if hasattr(headers, "get") else None
                if isinstance(value, str) and value:
                    return value
    return None


def _response_text(exc: anthropic.APIStatusError) -> str | None:
    response: Any = getattr(exc, "response", None)
    if response is None:
        return None
    text: Any = getattr(response, "text", None)
    return text if isinstance(text, str) else None


def _raise_from_api_status(exc: anthropic.APIStatusError) -> None:
    raise ProviderError(
        f"Anthropic returned HTTP {exc.status_code}",
        provider_http_status=exc.status_code,
        provider_request_id=_request_id(exc),
        original_body=_response_text(exc),
    ) from exc


def _raise_from_transport(exc: Exception) -> None:
    raise ProviderError(
        f"Anthropic transport failure: {exc}",
        provider_http_status=None,
        provider_request_id=None,
        original_body=None,
    ) from exc


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


class AnthropicLLMProvider:
    """Real LLM policy driven by Anthropic's Messages API."""

    name: str = "anthropic"
    model: str

    def __init__(self, settings: Settings) -> None:
        assert settings.anthropic_api_key is not None, (
            "Settings validator should have required anthropic_api_key "
            "when llm_provider='anthropic'"
        )

        self.model = settings.llm_model or "claude-opus-4-7"
        self._max_tokens = settings.anthropic_max_tokens
        self._timeout = settings.anthropic_timeout_seconds
        # ``max_retries=0`` disables the SDK's internal retry loop so our
        # bounded retry below is the single source of truth for retry policy.
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=float(self._timeout),
            max_retries=0,
        )
        # Tests override ``_rng`` with a seeded ``random.Random(seed)`` so
        # jitter is deterministic under assertion.  Production uses the
        # default unseeded generator.
        self._rng = random.Random()

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall:
        """Return a policy decision by calling Anthropic's Messages API.

        Maps SDK exceptions to orchestrator-level typed errors:

        * :class:`anthropic.APIStatusError` → :class:`ProviderError` with the
          HTTP status, request id, and original body populated.
        * :class:`anthropic.APIConnectionError` / :class:`anthropic.APITimeoutError`
          → :class:`ProviderError` with ``provider_http_status=None``.
        * Response with zero or multiple ``tool_use`` blocks → :class:`PolicyError`
          (terminates the run with ``stop_reason=error``).
        """
        tool_schemas = _to_anthropic_tools(tools)

        response = None
        cumulative_latency_ms = 0
        for attempt in range(_MAX_ATTEMPTS):
            attempt_start = time.perf_counter()
            try:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=list(messages),  # type: ignore[arg-type]
                    tools=tool_schemas,  # type: ignore[arg-type]
                    tool_choice={"type": "auto"},
                )
                cumulative_latency_ms += int(
                    (time.perf_counter() - attempt_start) * 1000
                )
                break
            except (
                anthropic.APIStatusError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            ) as exc:
                cumulative_latency_ms += int(
                    (time.perf_counter() - attempt_start) * 1000
                )
                is_last_attempt = attempt == _MAX_ATTEMPTS - 1
                if not _is_transient(exc) or is_last_attempt:
                    if isinstance(exc, anthropic.APIStatusError):
                        _raise_from_api_status(exc)
                    else:
                        _raise_from_transport(exc)
                backoff = min(
                    _BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**attempt)
                )
                jitter = self._rng.uniform(-_JITTER_SECONDS, _JITTER_SECONDS)
                sleep_for = max(0.0, backoff + jitter)
                logger.warning(
                    "anthropic retry",
                    extra={
                        "attempt": attempt + 1,
                        "backoff_s": sleep_for,
                        "request_id": (
                            _request_id(exc)
                            if isinstance(exc, anthropic.APIStatusError)
                            else None
                        ),
                    },
                )
                await asyncio.sleep(sleep_for)

        assert response is not None  # loop either broke on success or raised
        latency_ms = cumulative_latency_ms

        raw = response.model_dump(mode="json")
        redacted = _redact_response(raw)

        content = cast("list[dict[str, Any]]", redacted.get("content") or [])
        tool_uses = [b for b in content if b.get("type") == "tool_use"]

        if not tool_uses:
            stop_reason = redacted.get("stop_reason")
            if stop_reason == "max_tokens":
                raise PolicyError(
                    "policy selected no tool (stop_reason=max_tokens — raise "
                    "anthropic_max_tokens or tighten the prompt)"
                )
            raise PolicyError("policy selected no tool")
        if len(tool_uses) > 1:
            names = [str(t.get("name")) for t in tool_uses]
            raise PolicyError(f"policy selected multiple tools: {names}")

        first = tool_uses[0]
        usage_raw = cast("dict[str, Any]", redacted.get("usage") or {})
        usage = Usage(
            input_tokens=int(usage_raw.get("input_tokens", 0)),
            output_tokens=int(usage_raw.get("output_tokens", 0)),
            latency_ms=latency_ms,
        )

        return ToolCall(
            name=str(first["name"]),
            arguments=cast("dict[str, Any]", first.get("input") or {}),
            usage=usage,
            raw_response=redacted,
        )
