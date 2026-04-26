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

from datetime import UTC, datetime
from typing import ClassVar

from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.schemas import DispatchEnvelope


class HumanExecutor:
    """Operator-fulfilled executor.  Returns ``dispatched`` and waits."""

    mode: ClassVar[ExecutorMode] = "human"

    def __init__(self, ref: str, *, expected_signal_name: str) -> None:
        self.name = ref
        self._ref = ref
        # Documented for the bootstrap site — the runtime is what
        # actually pairs (run_id, task_id, name) → Dispatch when the
        # signal arrives.  Carried here as a string so an executor
        # binding can be inspected at runtime.
        self.expected_signal_name = expected_signal_name

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
