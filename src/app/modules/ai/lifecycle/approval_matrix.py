"""Pure approval-matrix for FEAT-006.

``approval_matrix(task, assignment, stage, solo_dev)`` returns the
:class:`ActorRole` required to approve at *stage*.  No DB access; no side
effects.  Callers (the signal endpoints) load the task + active assignment
inside ``SELECT ... FOR UPDATE`` and consult this function before comparing
to the request's ``X-Actor-Role``.
"""

from __future__ import annotations

from app.modules.ai.enums import ActorRole, ApprovalStage, AssigneeType
from app.modules.ai.models import Task, TaskAssignment


def approval_matrix(
    task: Task,
    assignment: TaskAssignment | None,
    stage: ApprovalStage,
    *,
    solo_dev: bool,
) -> ActorRole:
    """Return the role required to approve at *stage*.

    Matrix:

    - ``proposed``: always ``ADMIN``.
    - ``plan``: ``DEV`` when assigned to a dev (self-signal); ``ADMIN``
      otherwise (agent-assigned or unassigned).
    - ``impl``: ``ADMIN`` in solo-dev mode; ``DEV`` otherwise (a dev other
      than the implementer — v1 does not pick a specific reviewer).
    """
    del task  # unused; passed for symmetry and future stages
    if stage is ApprovalStage.PROPOSED:
        return ActorRole.ADMIN
    if stage is ApprovalStage.PLAN:
        if assignment is not None and assignment.assignee_type == AssigneeType.DEV.value:
            return ActorRole.DEV
        return ActorRole.ADMIN
    if stage is ApprovalStage.IMPL:
        return ActorRole.ADMIN if solo_dev else ActorRole.DEV
    raise ValueError(f"unknown stage: {stage}")
