"""Startup registration of effectors + ``no_effector`` exemptions (T-171).

This is the single entry point for telling the app "here is the state
of the outbound surface". Every new effector registers here; every
transition that intentionally has no effector gets an exemption here.

Today the only effectors are the per-request-dispatched GitHub Checks
pair (see ``service._dispatch_github_check_*``). They do not register
against the permanent registry — instead, each affected transition is
exempted with a reason pointing at that dispatch site. Later FEATs
(T-163 assignment, T-164 task-generation, and so on) will flip these
into real registrations.

The exemption reasons are the documentation for "why is this transition
silent in v1?" — review them on the next iteration and decide whether
to grow a real effector or keep them silent.
"""

from __future__ import annotations

import logging

from app.modules.ai.lifecycle.effectors.assignment import (
    RequestAssignmentEffector,
)
from app.modules.ai.lifecycle.effectors.base import no_effector
from app.modules.ai.lifecycle.effectors.registry import EffectorRegistry
from app.modules.ai.lifecycle.effectors.task_generation import (
    GenerateTasksEffector,
)
from app.modules.ai.trace import TraceStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exemption reasons — kept as named constants so review-time scrutiny is cheap
# ---------------------------------------------------------------------------

_GITHUB_PER_REQUEST_DISPATCH = (
    "fires per-request via signal-adapter dispatch (service._dispatch_github_check_*); "
    "move to permanent registration when effectors gain DI-aware factories"
)
_INGRESS_ONLY = (
    "ingress-only transition — the signal itself is the outbound effect, "
    "downstream transitions own the user-visible side effects"
)
_TERMINAL_STATE = "terminal state; post-completion notification surfaces are a separate FEAT"
_ADMIN_ONLY_NO_EXTERNAL = (
    "admin-only transition with no external side effect in v1; "
    "future Slack/audit effector can replace this exemption"
)
_DERIVED_TRANSITION = (
    "derived by W2/W5 derivation logic; the triggering-task effector is the "
    "outbound surface, the derivation itself is state-only"
)


def register_all_effectors(
    registry: EffectorRegistry,
    *,
    trace: TraceStore,
) -> None:
    """Populate the effector registry + no_effector exemptions.

    The *registry* is mutated in place. *trace* is held on the registry
    for every fire_all emission; the startup call passes it once.

    Called exactly once from the lifespan. Idempotent in the sense that
    ``no_effector`` overrides the prior reason for the same key; still,
    don't call this twice — the right place to change registrations is
    this function, and reflecting real state is cheaper than making it
    re-entrant.
    """
    _register_permanent_effectors(registry)
    _register_work_item_exemptions()
    _register_task_exemptions()
    logger.info(
        "effector bootstrap: %d key(s) registered, exemptions recorded",
        len(registry.registered_keys()),
    )
    del trace  # retained by the registry already


def _register_permanent_effectors(registry: EffectorRegistry) -> None:
    # T4 entry (approved → assigning): structured log naming the task that
    # needs an assignee. Pluggable transport — future Slack / email
    # effectors register against the same key.
    registry.register("task:entry:assigning", RequestAssignmentEffector())
    # S1 entry (work-item creation): deterministic seed-task generator.
    # Fires on ``work_item:entry:open``; an LLM-backed generator replaces
    # this under the same key when it lands.
    registry.register("work_item:entry:open", GenerateTasksEffector())


def _register_work_item_exemptions() -> None:
    # W1 — approve-first-task: the task-level approve is the outbound trigger;
    # the work-item side is a state derivation, no additional effect.
    no_effector("work_item:open->in_progress", _DERIVED_TRANSITION)
    # W3/W4 — admin lock/unlock: v1 has no Slack/audit effector for pausing;
    # add one when we grow a cross-tool notification surface.
    no_effector("work_item:in_progress->locked", _ADMIN_ONLY_NO_EXTERNAL)
    no_effector("work_item:locked->in_progress", _ADMIN_ONLY_NO_EXTERNAL)
    # W5 — all tasks terminal → ready: derivation; last-terminal-task's
    # effector is where a "work-item-ready" notification would belong.
    no_effector("work_item:in_progress->ready", _DERIVED_TRANSITION)
    # W6 — close: terminal, no outbound in v1.
    no_effector("work_item:ready->closed", _TERMINAL_STATE)


def _register_task_exemptions() -> None:
    # T2 — approve: ingress; the follow-up T4 (approved→assigning) carries the
    # assignment-request effector (T-163 once registered). Until then, exempt.
    no_effector("task:proposed->approved", _INGRESS_ONLY)
    # T4 — approved→assigning: covered by the RequestAssignmentEffector
    # registered on task:entry:assigning (T-163).
    # T5 — assign (assigning→planning): the signal itself is the action; no
    # downstream notification effector in v1.
    no_effector("task:assigning->planning", _ADMIN_ONLY_NO_EXTERNAL)
    # T6 — submit-plan: the plan-ready effector (notify reviewer) is a later
    # FEAT; admin review today is manual, so no outbound in v1.
    no_effector("task:planning->plan_review", _ADMIN_ONLY_NO_EXTERNAL)
    # T7 — approve-plan (plan_review→implementing): ingress; implementation
    # start has no outbound until an implementation-dispatch effector lands.
    no_effector("task:plan_review->implementing", _INGRESS_ONLY)
    # T8 — reject-plan: feedback goes on the Approval row; no external surface.
    no_effector("task:plan_review->planning", _ADMIN_ONLY_NO_EXTERNAL)
    # T9 — submit-impl: GitHub check-create fires here via signal-adapter
    # dispatch (T-162). Exempt from permanent-registry validation because the
    # dispatch site is per-request, not lifespan-registered.
    no_effector("task:implementing->impl_review", _GITHUB_PER_REQUEST_DISPATCH)
    # T10 — approve-review (impl_review→done): GitHub check-update(success)
    # fires per-request.
    no_effector("task:impl_review->done", _GITHUB_PER_REQUEST_DISPATCH)
    # T11 — reject-review (impl_review→implementing): GitHub check-update(failure)
    # fires per-request.
    no_effector("task:impl_review->implementing", _GITHUB_PER_REQUEST_DISPATCH)
    # T12 — defer: any non-terminal → deferred; a broader defer-notification
    # surface is a separate FEAT.
    for src in (
        "proposed",
        "approved",
        "assigning",
        "planning",
        "plan_review",
        "implementing",
        "impl_review",
    ):
        no_effector(f"task:{src}->deferred", _ADMIN_ONLY_NO_EXTERNAL)
