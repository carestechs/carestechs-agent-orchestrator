"""LLM-backed content executor (FEAT-011 / T-252).

The fifth flavour on the executor seam — alongside :class:`LocalExecutor`,
:class:`RemoteExecutor`, :class:`HumanExecutor`, and :class:`EngineExecutor`.
Where :class:`LocalExecutor` wraps an arbitrary callable, this adapter
wraps a single ``core.llm`` provider call: render two prompts against
the dispatch context, ask the provider, validate the structured output
against a Pydantic schema, retry on validation failure up to
``max_retries``.

``mode = "local"`` — the LLM call is in-process; the runtime treats the
dispatch as a synchronous local-mode dispatch with no wake leg, mirroring
:class:`LocalExecutor`.

Constructor injection only: the :class:`LLMProvider` is supplied by the
bootstrap helper.  The module imports the abstraction
(:mod:`app.core.llm`) but never a concrete provider SDK at module scope —
the FEAT-009 / FEAT-010 import-quarantine discipline is preserved so
``runtime_deterministic`` does not transitively pull ``anthropic`` /
``openai`` into ``sys.modules``.

Prompt rendering is intentionally boring: ``str.format_map(...)`` against
a flat dict assembled from ``ctx.intake`` (and the optional
``memorySnapshot`` extra).  Richer templating (Jinja, partials) is a
future FEAT — not this one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel, ValidationError

from app.core.llm import LLMProvider, ToolCall
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope

logger = logging.getLogger(__name__)


class LLMContentExecutor:
    """Local executor that produces a structured artefact via a single LLM call.

    The dispatch envelope returned mirrors :class:`LocalExecutor`'s shape
    (``mode="local"``).  On success the validated payload is the dispatch
    ``result``; on schema-validation failure (after retries are exhausted)
    the envelope is ``failed`` with ``outcome="error"`` and
    ``detail="result_schema_validation_failed"``.
    """

    mode: ClassVar[ExecutorMode] = "local"

    def __init__(
        self,
        ref: str,
        *,
        system_prompt: str,
        user_prompt_template: str,
        result_schema: type[BaseModel],
        llm_provider: LLMProvider,
        max_retries: int = 1,
        model: str | None = None,
    ) -> None:
        self.name = ref
        self._ref = ref
        self._system_prompt = system_prompt
        self._user_prompt_template = user_prompt_template
        self._result_schema = result_schema
        self._llm_provider = llm_provider
        self._max_retries = max_retries
        self._model = model

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        try:
            user_prompt = self._render_prompt(ctx)
        except KeyError as exc:
            # Surface the missing template variable before the LLM call.
            return _envelope(
                ctx,
                ref=self._ref,
                started=started,
                state="failed",
                outcome="error",
                detail=f"prompt_render_failed: missing template variable {exc!s}",
            )

        attempts_total = 1 + max(0, self._max_retries)
        last_error: str | None = None
        for attempt in range(attempts_total):
            try:
                tool_call = await self._llm_provider.chat_with_tools(
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[],
                )
            except Exception as exc:  # provider transient/permanent
                logger.exception(
                    "LLMContentExecutor %s provider call raised on attempt %d",
                    self._ref,
                    attempt + 1,
                    extra={"dispatch_id": str(ctx.dispatch_id)},
                )
                return _envelope(
                    ctx,
                    ref=self._ref,
                    started=started,
                    state="failed",
                    outcome="error",
                    detail=f"provider_error: {type(exc).__name__}: {exc}",
                )

            payload = _payload_from_tool_call(tool_call)
            try:
                validated = self._result_schema.model_validate(payload)
            except ValidationError as exc:
                last_error = str(exc)
                logger.warning(
                    "LLMContentExecutor %s schema validation failed on attempt %d/%d: %s",
                    self._ref,
                    attempt + 1,
                    attempts_total,
                    exc,
                    extra={"dispatch_id": str(ctx.dispatch_id)},
                )
                continue
            return _envelope(
                ctx,
                ref=self._ref,
                started=started,
                state="completed",
                outcome="ok",
                result=validated.model_dump(mode="json"),
            )

        return _envelope(
            ctx,
            ref=self._ref,
            started=started,
            state="failed",
            outcome="error",
            detail=("result_schema_validation_failed: " f"{attempts_total} attempt(s); last_error={last_error!s}"),
        )

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(self, ctx: DispatchContext) -> str:
        bindings: dict[str, Any] = {}
        bindings.update(dict(ctx.intake))
        memory_snapshot = ctx.extras.get("memorySnapshot")
        if isinstance(memory_snapshot, Mapping):
            bindings.update(cast(Mapping[str, Any], memory_snapshot))
        return self._user_prompt_template.format_map(_StrictMap(bindings))


class _StrictMap(dict[str, Any]):
    """``format_map`` mapping that raises ``KeyError`` on missing keys.

    ``str.format_map`` already does this when the underlying mapping is a
    plain ``dict``; subclassing keeps the contract explicit and makes the
    intent searchable.
    """

    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


def _payload_from_tool_call(tool_call: ToolCall) -> Mapping[str, Any]:
    """Extract the structured payload the executor will validate.

    The :class:`ToolCall` shape exposes ``arguments`` — the structured
    dict the provider produced for the (only) tool the executor passes.
    A future provider extension that surfaces a free-form JSON response
    can land here without touching the executor's contract.
    """
    return tool_call.arguments


def _envelope(
    ctx: DispatchContext,
    *,
    ref: str,
    started: datetime,
    state: str,
    outcome: str,
    result: dict[str, Any] | None = None,
    detail: str | None = None,
) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=ctx.dispatch_id,
        step_id=ctx.step_id,
        run_id=ctx.run_id,
        executor_ref=ref,
        mode="local",  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        intake=dict(ctx.intake),
        result=result,
        outcome=outcome,  # type: ignore[arg-type]
        detail=detail,
        started_at=started,
        finished_at=datetime.now(UTC),
    )


__all__ = ["LLMContentExecutor"]
