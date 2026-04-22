"""Unit tests for effector-coverage validation (FEAT-008/T-171)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.modules.ai.lifecycle.effectors import (
    EffectorRegistry,
    no_effector,
)
from app.modules.ai.lifecycle.effectors.base import _reset_exemptions_for_tests
from app.modules.ai.lifecycle.effectors.validation import (
    DeclaredTransition,
    enumerate_transitions,
    format_uncovered_error,
    validate_effector_coverage,
)


class _TrivialEffector:
    name = "trivial"

    async def fire(self, ctx: Any) -> Any:
        return None


def _registry(*keys: str) -> EffectorRegistry:
    reg = EffectorRegistry(trace=cast("Any", MagicMock()))
    for k in keys:
        reg.register(k, _TrivialEffector())
    return reg


@pytest.fixture(autouse=True)
def reset_exemptions() -> None:
    _reset_exemptions_for_tests()


# ---------------------------------------------------------------------------
# enumerate_transitions
# ---------------------------------------------------------------------------


def test_enumerate_yields_every_declared_transition() -> None:
    transitions = enumerate_transitions()
    # Sanity: both workflows' transitions show up.
    keys = {t.transition_key for t in transitions}
    assert "work_item:open->in_progress" in keys
    assert "task:proposed->approved" in keys
    assert "task:impl_review->done" in keys
    # Defer edges present.
    assert "task:proposed->deferred" in keys


def test_declared_transition_entry_key_shape() -> None:
    t = DeclaredTransition(
        entity_type="task", from_state="proposed", to_state="approved", name="approve"
    )
    assert t.transition_key == "task:proposed->approved"
    assert t.entry_key == "task:entry:approved"


# ---------------------------------------------------------------------------
# validate_effector_coverage buckets
# ---------------------------------------------------------------------------


def test_all_covered_returns_empty_uncovered() -> None:
    # Register an effector against every declared transition's state-transition key.
    transitions = enumerate_transitions()
    reg = _registry(*(t.transition_key for t in transitions))

    result = validate_effector_coverage(reg)

    assert len(result.covered) == len(transitions)
    assert result.exempt == []
    assert result.uncovered == []


def test_entry_only_registration_counts_as_covered() -> None:
    transitions = enumerate_transitions()
    # Pick a transition; register only its entry-state key.
    target = transitions[0]
    reg = _registry(target.entry_key)

    result = validate_effector_coverage(reg)

    assert target in result.covered


def test_no_effector_exemption_moves_to_exempt_bucket() -> None:
    transitions = enumerate_transitions()
    target = transitions[0]
    no_effector(target.transition_key, "legit reason for v1 silence")

    reg = _registry()
    result = validate_effector_coverage(reg)

    matched = [t for t, _ in result.exempt if t == target]
    assert matched, "exempt target must appear in the exempt bucket"
    # And not in uncovered.
    assert target not in result.uncovered


def test_uncovered_transitions_are_flagged() -> None:
    # Empty registry + no exemptions → every declared transition uncovered.
    reg = _registry()
    result = validate_effector_coverage(reg)
    assert len(result.uncovered) == len(enumerate_transitions())
    assert result.covered == []


def test_format_uncovered_error_lists_gaps() -> None:
    reg = _registry()
    result = validate_effector_coverage(reg)
    msg = format_uncovered_error(result)
    assert "Effector coverage incomplete" in msg
    assert "work_item:open->in_progress" in msg
    assert "no_effector" in msg  # fix-it hint present


# ---------------------------------------------------------------------------
# no_effector validation
# ---------------------------------------------------------------------------


def test_no_effector_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match=r"≥10 chars"):
        no_effector("task:x->y", "")


def test_no_effector_rejects_too_short_reason() -> None:
    with pytest.raises(ValueError, match=r"≥10 chars"):
        no_effector("task:x->y", "TODO")


def test_no_effector_accepts_at_ten_chars() -> None:
    no_effector("task:x->y", "0123456789")  # exactly 10 chars
