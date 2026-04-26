"""Executor Protocol + per-dispatch context (FEAT-009 / T-213).

An ``Executor`` is invoked by the runtime loop with a ``DispatchContext``
and returns a ``DispatchEnvelope`` describing the outcome.  All three
modes (local / remote / human) implement the same contract:

* ``local`` — synchronous Python callable wrapped to return an envelope
  in the local case (T-214).
* ``remote`` — POSTs the dispatch to a configured URL and returns the
  ``dispatched`` envelope; the terminal envelope arrives later via the
  ``/hooks/executors/<id>`` webhook (T-215, T-216).
* ``human`` — registers the dispatch as ``dispatched`` and waits for
  ``POST /api/v1/runs/<id>/signals`` to deliver the result (T-217).

The Protocol is deliberately small — adapters share **no** concrete base
class.  Anything an adapter needs (DB session factory, HTTP client,
secret) is supplied at construction by the bootstrap module.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from app.modules.ai.schemas import DispatchEnvelope

ExecutorMode = Literal["local", "remote", "human"]


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Immutable per-dispatch context passed into ``Executor.dispatch``.

    The runtime loop builds one of these per loop iteration before
    invoking the bound executor.  The executor reads it (never mutates)
    and returns a ``DispatchEnvelope``.
    """

    dispatch_id: uuid.UUID
    run_id: uuid.UUID
    step_id: uuid.UUID
    agent_ref: str
    node_name: str
    intake: Mapping[str, Any]
    # Free-form extras the bootstrap module may attach (e.g. system prompt
    # for an LLM-backed local executor).  Adapters that don't care can
    # ignore the field.
    extras: Mapping[str, Any] = field(default_factory=dict[str, Any])


@runtime_checkable
class Executor(Protocol):
    """A registered executor for one ``(agent_ref, node_name)`` binding.

    Implementations MUST NOT raise on expected failure modes — they
    return a ``failed`` envelope instead.  The registry catches
    unexpected exceptions defensively, but raising is a contract bug
    (mirrors the FEAT-008 ``Effector`` discipline).
    """

    # ``name`` is per-instance (e.g. ``local:request_plan``,
    # ``remote:claude-code``). ``mode`` is per-class because all
    # instances of a given adapter share the same transport.
    name: str
    mode: ClassVar[ExecutorMode]

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope: ...
