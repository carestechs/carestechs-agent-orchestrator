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
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel, ValidationError

from app.core.exceptions import PolicyError
from app.core.llm import LLMProvider, ToolCall, ToolDefinition
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope

PromptContextLoader = Callable[[DispatchContext], Awaitable[Mapping[str, Any]]]
"""Augment ``user_prompt_template`` bindings at dispatch time.

When set, the loader is awaited before ``str.format_map``, and its
result is merged on top of the intake-derived bindings.  Useful for
binding-specific work the runtime can't do generically — for example
the ``load_work_item`` binding installs a loader that reads the brief
file from disk and exposes it as ``{workItemBrief}``.
"""


MemoryPatchBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
"""Callable that converts the validated LLM result dict into a memory patch.

When supplied, the executor merges the patch into the envelope's
``result`` under the ``__memory_patch`` key so the runtime's standard
``__memory_patch`` merge writes it into ``RunMemory.data`` after a
successful dispatch.  Standalone LLM-content nodes (e.g. ``load_work_item``
in the v0.3.0 split-node shape) use this to persist the brief / task
list / plan into the typed lifecycle memory; nodes that do not need
memory writes (e.g. nodes wrapped in :class:`CompositeLLMEngineExecutor`,
which builds its own patch path) leave it unset.
"""

logger = logging.getLogger(__name__)


class LLMContentExecutor:
    """Local executor that produces a structured artefact via a single LLM call.

    The dispatch envelope returned mirrors :class:`LocalExecutor`'s shape
    (``mode="local"``).  On success the validated payload is the dispatch
    ``result``; on schema-validation failure (after retries are exhausted)
    the envelope is ``failed`` with ``outcome="error"`` and
    ``detail="llm_content_retries_exhausted: …"`` (carries
    ``last_error=validation_error: …`` or ``policy_error: …`` so
    operators can tell which retry path exhausted).
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
        memory_patch_builder: MemoryPatchBuilder | None = None,
        prompt_context_loader: PromptContextLoader | None = None,
    ) -> None:
        self.name = ref
        self._ref = ref
        self._system_prompt = system_prompt
        self._user_prompt_template = user_prompt_template
        self._result_schema = result_schema
        self._llm_provider = llm_provider
        self._max_retries = max_retries
        self._model = model
        self._tool = _tool_from_result_schema(ref, result_schema)
        self._memory_patch_builder = memory_patch_builder
        self._prompt_context_loader = prompt_context_loader

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)

        extras: Mapping[str, Any] = {}
        if self._prompt_context_loader is not None:
            try:
                extras = await self._prompt_context_loader(ctx)
            except Exception as exc:
                return _envelope(
                    ctx,
                    ref=self._ref,
                    started=started,
                    state="failed",
                    outcome="error",
                    detail=f"prompt_context_loader_failed: {type(exc).__name__}: {exc}",
                )

        try:
            user_prompt = self._render_prompt(ctx, extra_bindings=extras)
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
        # Force the (single) tool call.  The schema-or-die contract
        # makes "model chats instead" an unrecoverable failure mode for
        # this executor, so disabling that branch entirely is the right
        # default.  See the protocol's ``tool_choice`` parameter.
        tool_choice: dict[str, Any] = {"type": "tool", "name": self._tool.name}
        for attempt in range(attempts_total):
            try:
                tool_call = await self._llm_provider.chat_with_tools(
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[self._tool],
                    tool_choice=tool_choice,
                )
            except PolicyError as exc:
                # Provider raised PolicyError because the response had
                # zero or multiple tool_use blocks.  Same class of "model
                # misbehaved on this turn" failure as a ValidationError;
                # retry within the budget rather than terminating.
                last_error = f"policy_error: {exc}"
                logger.warning(
                    "LLMContentExecutor %s policy_error on attempt %d/%d (retrying within budget): %s",
                    self._ref,
                    attempt + 1,
                    attempts_total,
                    exc,
                    extra={"dispatch_id": str(ctx.dispatch_id)},
                )
                continue
            except Exception as exc:  # provider transient/permanent (non-policy)
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
                last_error = f"validation_error: {exc}"
                logger.warning(
                    "LLMContentExecutor %s schema validation failed on attempt %d/%d: %s",
                    self._ref,
                    attempt + 1,
                    attempts_total,
                    exc,
                    extra={"dispatch_id": str(ctx.dispatch_id)},
                )
                continue
            result_dict: dict[str, Any] = validated.model_dump(mode="json")
            if self._memory_patch_builder is not None:
                try:
                    patch = self._memory_patch_builder(result_dict)
                except Exception as exc:
                    return _envelope(
                        ctx,
                        ref=self._ref,
                        started=started,
                        state="failed",
                        outcome="error",
                        detail=f"memory_patch_builder_failed: {type(exc).__name__}: {exc}",
                    )
                result_dict["__memory_patch"] = patch
            return _envelope(
                ctx,
                ref=self._ref,
                started=started,
                state="completed",
                outcome="ok",
                result=result_dict,
            )

        # ``last_error`` is prefixed with the failure kind
        # (``validation_error: …`` or ``policy_error: …``) so operators
        # can tell at a glance which retry path exhausted.
        return _envelope(
            ctx,
            ref=self._ref,
            started=started,
            state="failed",
            outcome="error",
            detail=(
                f"llm_content_retries_exhausted: {attempts_total} attempt(s); "
                f"last_error={last_error!s}"
            ),
        )

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        ctx: DispatchContext,
        *,
        extra_bindings: Mapping[str, Any] = {},
    ) -> str:
        bindings: dict[str, Any] = {}
        bindings.update(dict(ctx.intake))
        memory_snapshot = ctx.extras.get("memorySnapshot")
        if isinstance(memory_snapshot, Mapping):
            bindings.update(cast(Mapping[str, Any], memory_snapshot))
        if extra_bindings:
            bindings.update(dict(extra_bindings))
        return self._user_prompt_template.format_map(_StrictMap(bindings))


class _StrictMap(dict[str, Any]):
    """``format_map`` mapping that raises ``KeyError`` on missing keys.

    ``str.format_map`` already does this when the underlying mapping is a
    plain ``dict``; subclassing keeps the contract explicit and makes the
    intent searchable.
    """

    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


_TOOL_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _tool_from_result_schema(ref: str, result_schema: type[BaseModel]) -> ToolDefinition:
    """Build a single Anthropic-compatible tool spec from the Pydantic schema.

    The model has exactly one tool to call; its arguments are the structured
    payload the executor validates. Without this, the provider raises
    ``policy selected no tool`` because ``tools=[]`` cannot satisfy a
    tool-calling response.
    """
    sanitized = _TOOL_NAME_SAFE.sub("_", ref).strip("_") or "emit"
    name = f"emit_{sanitized}"[:64]
    return ToolDefinition(
        name=name,
        description=f"Emit a structured {result_schema.__name__} payload.",
        parameters=result_schema.model_json_schema(),
    )


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


__all__ = ["LLMContentExecutor", "MemoryPatchBuilder", "PromptContextLoader"]
