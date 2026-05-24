"""Human executor adapter (FEAT-009 / T-217).

A node bound to a human executor is one where the runtime simply waits
for an operator to ``POST /api/v1/runs/<id>/signals`` with the result.
The adapter's ``dispatch`` writes a ``dispatched`` envelope and returns
immediately; the runtime loop awaits the matching ``deliver_dispatch``
call which is fired by the signal-endpoint service when a matching
signal arrives.

The wire format of the existing FEAT-005 ``/signals`` endpoint is
preserved bit-for-bit — service code recognizes that a signal whose
``(run_id, task_id)`` matches an in-flight ``Dispatch`` row should
deliver to *both* the legacy signal channel (``deliver_signal``) and
the new dispatch future (``deliver_dispatch``).  Routing the *same*
operator action through both keeps every existing v0.1.0 caller
working while letting the runtime loop transition to dispatch-aware
waiting.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.executors.llm_content import MemoryPatchBuilder
from app.modules.ai.schemas import DispatchEnvelope

IntakeBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
"""Callable that enriches a human-checkpoint step's intake from memory.

Signature ``(current_memory) -> extra_intake``.  The returned dict is
merged into the step's ``node_inputs`` before the ``Step`` row is
persisted, so DevHub (or any trace consumer) can display the artefact
the operator is gating without needing direct access to ``RunMemory``.

Builders MUST be pure functions (no I/O, no session).  They run inside
``_execute_node`` before the dispatch is created — not at
signal-delivery time like ``memory_patch_builder``.
"""


class HumanExecutor:
    """Operator-fulfilled executor.  Returns ``dispatched`` and waits.

    Optional ``memory_patch_builder`` (FEAT-015 / T-296) lets a binding
    declare how an operator-delivered signal payload mutates
    ``RunMemory``.  The builder runs at signal-delivery time in
    ``service._deliver_to_human_dispatch`` — the executor itself only
    *carries* the reference; it does not call the builder during its
    own ``dispatch`` because the payload isn't available there.  The
    runtime then merges the returned patch into ``RunMemory.data`` via
    the standard ``__memory_patch`` hook on the dispatch envelope's
    ``result``.

    Optional ``intake_builder`` enriches the step's ``node_inputs``
    with artefact data from ``RunMemory`` so the operator (via DevHub
    or trace) can see the content they are gating — e.g. the original
    brief body at ``confirm_brief``, the task list at ``confirm_tasks``.
    The builder runs in ``_execute_node`` before the ``Step`` row is
    persisted.

    Keys starting with ``_`` are filtered by ``_write_state`` and must
    not appear in the builder's returned patch.
    """

    mode: ClassVar[ExecutorMode] = "human"

    def __init__(
        self,
        ref: str,
        *,
        expected_signal_name: str,
        memory_patch_builder: MemoryPatchBuilder | None = None,
        intake_builder: IntakeBuilder | None = None,
    ) -> None:
        self.name = ref
        self._ref = ref
        self.expected_signal_name = expected_signal_name
        self.memory_patch_builder = memory_patch_builder
        self.intake_builder = intake_builder

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="human",  # type: ignore[arg-type]
            state="dispatched",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            started_at=started,
            dispatched_at=datetime.now(UTC),
        )
