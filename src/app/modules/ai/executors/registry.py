"""In-process executor registry (FEAT-009 / T-213).

Mirrors the FEAT-008 ``EffectorRegistry`` shape: composition root
instantiates one, registers concrete executors against
``(agent_ref, node_name)`` keys, and the runtime loop calls
:meth:`resolve` to get the binding for the node it just selected.

A given key can be bound *once* — duplicate registrations raise rather
than silently shadow.  The trace kind ``"executor_call"`` is emitted by
the runtime loop on dispatch terminal state, not by the registry
itself; the registry is a pure lookup.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from app.modules.ai.executors.base import Executor
from app.modules.ai.executors.binding import ExecutorBinding


class ExecutorRegistryError(RuntimeError):
    """Raised on duplicate registration or unresolved lookup."""


class ExecutorRegistry:
    """Per-process executor registry keyed on ``(agent_ref, node_name)``."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], ExecutorBinding] = {}

    # -- Registration ------------------------------------------------------

    def register(
        self,
        agent_ref: str,
        node_name: str,
        executor: Executor,
        *,
        timeout_seconds: float | None = None,
        extras: Mapping[str, Any] | None = None,
    ) -> ExecutorBinding:
        """Bind ``executor`` to ``(agent_ref, node_name)`` and return the binding.

        Raises :class:`ExecutorRegistryError` on duplicate registration —
        the bootstrap is the single source of truth and shadowing would
        make misconfiguration silent.
        """
        key = (agent_ref, node_name)
        if key in self._bindings:
            raise ExecutorRegistryError(
                f"executor already registered for {key!r}: "
                f"existing={self._bindings[key].executor.name!r}, "
                f"new={executor.name!r}"
            )
        binding = ExecutorBinding(
            agent_ref=agent_ref,
            node_name=node_name,
            executor=executor,
            timeout_seconds=timeout_seconds,
            extras=extras or {},
        )
        self._bindings[key] = binding
        return binding

    # -- Lookup ------------------------------------------------------------

    def resolve(self, agent_ref: str, node_name: str) -> ExecutorBinding:
        """Return the binding for ``(agent_ref, node_name)``.

        Raises :class:`ExecutorRegistryError` if no binding exists —
        coverage is enforced at lifespan startup, so a runtime miss is
        either a bootstrap regression or a flow that wasn't validated.
        """
        try:
            return self._bindings[(agent_ref, node_name)]
        except KeyError as exc:
            raise ExecutorRegistryError(f"no executor registered for ({agent_ref!r}, {node_name!r})") from exc

    def has(self, agent_ref: str, node_name: str) -> bool:
        return (agent_ref, node_name) in self._bindings

    # -- Inspection --------------------------------------------------------

    def registered_keys(self) -> frozenset[tuple[str, str]]:
        """Return ``{(agent_ref, node_name)}`` for every registered binding."""
        return frozenset(self._bindings)

    def bindings(self) -> Iterator[ExecutorBinding]:
        yield from self._bindings.values()
