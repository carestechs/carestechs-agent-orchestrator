"""Startup exhaustiveness validation (FEAT-008/T-171).

Reads the declared transition catalog from ``declarations.py`` and
cross-checks against the effector registry + ``no_effector`` exemption
registry. Every transition must be covered by at least one of:

* a registered effector under the state-transition key (``task:a->b``)
* a registered effector under the entry-state key (``task:entry:b``)
* an explicit ``no_effector(key, reason)`` exemption on either key

Missing coverage is a startup-blocking error — that's the point. The
failure message lists the gaps so the fix is obvious.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.ai.lifecycle import declarations
from app.modules.ai.lifecycle.effectors.base import iter_no_effector_exemptions
from app.modules.ai.lifecycle.effectors.registry import (
    EffectorRegistry,
    build_transition_key,
)


@dataclass(frozen=True, slots=True)
class DeclaredTransition:
    """One transition pulled out of the declarations."""

    entity_type: str
    from_state: str
    to_state: str
    name: str

    @property
    def transition_key(self) -> str:
        return build_transition_key(
            self.entity_type, self.from_state, self.to_state
        )

    @property
    def entry_key(self) -> str:
        return build_transition_key(self.entity_type, None, self.to_state)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Bucketed outcome of :func:`validate_effector_coverage`."""

    covered: list[DeclaredTransition]
    exempt: list[tuple[DeclaredTransition, str]]
    uncovered: list[DeclaredTransition]


def enumerate_transitions() -> list[DeclaredTransition]:
    """Materialize the full declared-transition set for both workflows."""
    out: list[DeclaredTransition] = []
    for raw in declarations.WORK_ITEM_TRANSITIONS:
        out.append(
            DeclaredTransition(
                entity_type="work_item",
                from_state=raw["fromStatus"],
                to_state=raw["toStatus"],
                name=raw["name"],
            )
        )
    for raw in declarations.TASK_TRANSITIONS:
        out.append(
            DeclaredTransition(
                entity_type="task",
                from_state=raw["fromStatus"],
                to_state=raw["toStatus"],
                name=raw["name"],
            )
        )
    return out


def validate_effector_coverage(
    registry: EffectorRegistry,
) -> ValidationResult:
    """Bucket every declared transition into covered / exempt / uncovered.

    "Covered" means at least one effector registered on either the
    state-transition key or the entry-state key. "Exempt" means a
    :func:`~app.modules.ai.lifecycle.effectors.base.no_effector` claim
    on either key (with its reason). "Uncovered" is everything else — a
    startup-blocking gap.
    """
    exemptions = dict(iter_no_effector_exemptions())
    registered = registry.registered_keys()

    covered: list[DeclaredTransition] = []
    exempt: list[tuple[DeclaredTransition, str]] = []
    uncovered: list[DeclaredTransition] = []

    for t in enumerate_transitions():
        if t.transition_key in registered or t.entry_key in registered:
            covered.append(t)
            continue
        reason = exemptions.get(t.transition_key) or exemptions.get(t.entry_key)
        if reason is not None:
            exempt.append((t, reason))
            continue
        uncovered.append(t)

    return ValidationResult(covered=covered, exempt=exempt, uncovered=uncovered)


def format_uncovered_error(result: ValidationResult) -> str:
    """Format the startup-failure message listing uncovered transitions."""
    lines = [
        f"Effector coverage incomplete: {len(result.uncovered)} transition(s) "
        "have no registered effector and no `no_effector` exemption:",
    ]
    for t in result.uncovered:
        lines.append(
            f"  - {t.transition_key}  ({t.entity_type} transition '{t.name}')"
        )
    lines.append("")
    lines.append(
        "Register an effector in `effectors/bootstrap.py` or add a "
        "`no_effector(<key>, <reason>)` call with a justification (reason "
        "must be ≥10 chars)."
    )
    return "\n".join(lines)
