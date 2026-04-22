"""Effector registry (FEAT-008).

Effectors are the first-class outbound-action seam: a named, pluggable
action fired on a state transition. The reactor dispatches them via
:class:`EffectorRegistry` on every engine ``item.transitioned`` webhook.

Public surface:

* :class:`Effector` — protocol every effector implements.
* :class:`EffectorContext` — immutable carrier passed to :meth:`Effector.fire`.
* :class:`EffectorResult` — structured return shape.
* :class:`EffectorRegistry` — dispatcher.
* :func:`build_transition_key` — canonical key scheme.
* :func:`no_effector` — marker decorator for acknowledged silent transitions.
* :func:`iter_no_effector_exemptions` — used by T-171's startup validator.
"""

from app.modules.ai.lifecycle.effectors.base import (
    Effector,
    iter_no_effector_exemptions,
    no_effector,
)
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
    EffectorStatus,
)
from app.modules.ai.lifecycle.effectors.registry import (
    EffectorRegistry,
    build_transition_key,
    dispatch_effector,
)

__all__ = [
    "Effector",
    "EffectorContext",
    "EffectorRegistry",
    "EffectorResult",
    "EffectorStatus",
    "build_transition_key",
    "dispatch_effector",
    "iter_no_effector_exemptions",
    "no_effector",
]
