"""Effector protocol + ``no_effector`` exemption marker."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar, Protocol, runtime_checkable

from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)

_MIN_NO_EFFECTOR_REASON = 10

_exemptions: dict[str, str] = {}


@runtime_checkable
class Effector(Protocol):
    """A named outbound action fired on a state transition.

    Implementations MUST NOT raise — failures are returned as an
    ``EffectorResult`` with ``status="error"``. The registry catches
    unexpected exceptions defensively, but raising is a contract bug.
    """

    name: ClassVar[str]

    async def fire(self, ctx: EffectorContext) -> EffectorResult: ...


def no_effector(transition_key: str, reason: str) -> str:
    """Mark *transition_key* as intentionally firing no effector.

    T-171's startup validator walks the full transition catalog and
    flags any transition with neither a registered effector nor an
    exemption here. Calling this twice with the same key overwrites the
    reason — the last call wins — so the canonical registration site is
    the lifespan wiring module.

    ``reason`` must be at least ten characters of human-readable prose;
    "n/a" or "TODO" is a review blocker waiting to happen.
    """
    if len(reason.strip()) < _MIN_NO_EFFECTOR_REASON:
        raise ValueError(
            f"no_effector reason must be ≥{_MIN_NO_EFFECTOR_REASON} chars: "
            f"{transition_key!r} got {reason!r}"
        )
    _exemptions[transition_key] = reason.strip()
    return transition_key


def iter_no_effector_exemptions() -> Iterator[tuple[str, str]]:
    """Yield ``(transition_key, reason)`` for every registered exemption."""
    yield from _exemptions.items()


def _reset_exemptions_for_tests() -> None:
    """Test hook — clear the module-level exemption dict between tests."""
    _exemptions.clear()


__all__ = [
    "Effector",
    "_reset_exemptions_for_tests",
    "iter_no_effector_exemptions",
    "no_effector",
]
