"""LLMContentExecutor unit tests (FEAT-011 / T-252).

Sibling to ``tests/modules/ai/executors/test_local.py``: each test
exercises one slice of the executor contract against a controlled stub
provider — never the real Anthropic SDK.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.llm import ToolCall, ToolDefinition, Usage
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.llm_content import LLMContentExecutor

_async = pytest.mark.asyncio(loop_scope="function")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """A minimal LLM provider that returns scripted ``ToolCall`` payloads.

    Each entry in ``script`` is the dict that becomes ``ToolCall.arguments``
    on the next call.  ``calls`` records every invocation so tests can
    assert call count + observed prompts.
    """

    name: str = "scripted-test"
    model: str = "scripted-v1"

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self._index = 0
        self.calls: list[tuple[str, str]] = []  # (system, user) per call

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall:
        del tools
        # Single-turn user message — the executor builds it from the template.
        user = ""
        for message in messages:
            if message.get("role") == "user":
                user = str(message.get("content", ""))
                break
        self.calls.append((system, user))
        if self._index >= len(self._script):
            raise AssertionError("scripted provider exhausted")
        payload = self._script[self._index]
        self._index += 1
        return ToolCall(
            name="content",
            arguments=dict(payload),
            usage=Usage(input_tokens=0, output_tokens=0, latency_ms=0),
            raw_response=None,
        )


class _BriefResult(BaseModel):
    title: str
    summary: str


def _ctx(intake: dict[str, Any] | None = None, extras: dict[str, Any] | None = None) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.3.0",
        node_name="load_work_item",
        intake=intake or {},
        extras=extras or {},
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@_async
class TestSuccessPath:
    async def test_valid_payload_yields_completed_envelope(self) -> None:
        provider = _ScriptedProvider(script=[{"title": "Some title", "summary": "Some summary"}])
        executor = LLMContentExecutor(
            ref="local:load_work_item",
            system_prompt="be helpful",
            user_prompt_template="Brief: {workItemPath}",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
        )

        env = await executor.dispatch(_ctx({"workItemPath": "docs/work-items/FEAT-099.md"}))

        assert env.state.value == "completed"
        assert env.outcome is not None
        assert env.outcome.value == "ok"
        assert env.result == {"title": "Some title", "summary": "Some summary"}
        assert env.mode.value == "local"
        assert len(provider.calls) == 1
        observed_system, observed_user = provider.calls[0]
        assert observed_system == "be helpful"
        assert "docs/work-items/FEAT-099.md" in observed_user


# ---------------------------------------------------------------------------
# Schema validation + retry
# ---------------------------------------------------------------------------


@_async
class TestSchemaValidationRetry:
    async def test_failure_then_success_within_retry_budget(self) -> None:
        # First attempt: missing 'summary' -> ValidationError; second succeeds.
        provider = _ScriptedProvider(
            script=[
                {"title": "T"},  # invalid
                {"title": "T", "summary": "S"},  # valid
            ]
        )
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="user",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
            max_retries=1,
        )

        env = await executor.dispatch(_ctx())

        assert env.state.value == "completed"
        assert env.result == {"title": "T", "summary": "S"}
        assert len(provider.calls) == 2

    async def test_validation_failure_exhausted_yields_failed_envelope(self) -> None:
        # Both attempts invalid -> failed envelope with structured detail.
        provider = _ScriptedProvider(
            script=[
                {"title": "only"},
                {"title": "still only"},
            ]
        )
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="user",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
            max_retries=1,
        )

        env = await executor.dispatch(_ctx())

        assert env.state.value == "failed"
        assert env.outcome is not None
        assert env.outcome.value == "error"
        assert env.detail is not None
        assert env.detail.startswith("result_schema_validation_failed")
        assert len(provider.calls) == 2

    async def test_max_retries_zero_means_one_attempt(self) -> None:
        provider = _ScriptedProvider(script=[{"title": "x"}])
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="user",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
            max_retries=0,
        )

        env = await executor.dispatch(_ctx())

        assert env.state.value == "failed"
        assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# Prompt rendering — intake + memory snapshot
# ---------------------------------------------------------------------------


@_async
class TestPromptRendering:
    async def test_intake_fields_substitute_into_template(self) -> None:
        provider = _ScriptedProvider(script=[{"title": "x", "summary": "y"}])
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="path={workItemPath} task={taskId}",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
        )

        await executor.dispatch(_ctx({"workItemPath": "/tmp/wi.md", "taskId": "T-7"}))

        _system, user = provider.calls[0]
        assert user == "path=/tmp/wi.md task=T-7"

    async def test_memory_snapshot_extras_override_for_template(self) -> None:
        provider = _ScriptedProvider(script=[{"title": "x", "summary": "y"}])
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="phase={phase} path={workItemPath}",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
        )

        await executor.dispatch(
            _ctx(
                {"workItemPath": "/tmp/x.md"},
                extras={"memorySnapshot": {"phase": "planning"}},
            )
        )

        _system, user = provider.calls[0]
        assert user == "phase=planning path=/tmp/x.md"

    async def test_missing_template_variable_yields_failed_envelope_before_call(
        self,
    ) -> None:
        provider = _ScriptedProvider(script=[{"title": "x", "summary": "y"}])
        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="path={workItemPath}",
            result_schema=_BriefResult,
            llm_provider=provider,  # type: ignore[arg-type]
        )

        env = await executor.dispatch(_ctx({}))  # workItemPath missing

        assert env.state.value == "failed"
        assert env.detail is not None
        assert "prompt_render_failed" in env.detail
        assert "workItemPath" in env.detail
        assert provider.calls == []  # provider never invoked


# ---------------------------------------------------------------------------
# Provider error handling
# ---------------------------------------------------------------------------


@_async
class TestProviderErrors:
    async def test_provider_exception_yields_failed_envelope(self) -> None:
        class _BoomProvider:
            name = "boom"
            model = "boom-1"

            async def chat_with_tools(  # type: ignore[no-untyped-def]
                self, *, system, messages, tools
            ):
                raise RuntimeError("upstream offline")

        executor = LLMContentExecutor(
            ref="local:r",
            system_prompt="sys",
            user_prompt_template="user",
            result_schema=_BriefResult,
            llm_provider=_BoomProvider(),  # type: ignore[arg-type]
        )
        env = await executor.dispatch(_ctx())
        assert env.state.value == "failed"
        assert env.detail is not None
        assert env.detail.startswith("provider_error")
        assert "RuntimeError" in env.detail


# ---------------------------------------------------------------------------
# Module-scope import quarantine — no provider SDK pulled by the executor file.
# ---------------------------------------------------------------------------


def test_no_provider_sdk_at_module_scope() -> None:
    """File-level string check: the executor must not import a provider SDK."""
    path = Path(__file__).resolve().parents[4] / "src" / "app" / "modules" / "ai" / "executors" / "llm_content.py"
    source = path.read_text(encoding="utf-8")
    forbidden_patterns = [
        re.compile(r"^\s*import\s+anthropic\b", re.MULTILINE),
        re.compile(r"^\s*from\s+anthropic\b", re.MULTILINE),
        re.compile(r"^\s*import\s+openai\b", re.MULTILINE),
        re.compile(r"^\s*from\s+openai\b", re.MULTILINE),
        re.compile(r"^\s*from\s+app\.core\.llm_anthropic\b", re.MULTILINE),
    ]
    offenders = [pat.pattern for pat in forbidden_patterns if pat.search(source)]
    assert not offenders, f"executors/llm_content.py imports a provider SDK at module scope: {offenders}"
