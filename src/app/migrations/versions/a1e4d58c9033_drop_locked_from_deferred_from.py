"""drop locked_from and deferred_from columns (FEAT-008/T-168)

Revision ID: a1e4d58c9033
Revises: 6bd53b01e704
Create Date: 2026-04-24 22:30:00.000000

Under engine-as-authority (FEAT-008), prior-state tracking is owned by the
flow engine's transition history. The local ``work_items.locked_from`` and
``tasks.deferred_from`` columns become redundant and are dropped.

Pre-flight: the upgrade refuses to run if any ``work_items.status =
'locked'`` or ``tasks.status = 'deferred'`` rows are present. Operators
must resolve those before upgrading — silent loss of prior-state data is
worse than a visible failure.

Downgrade restores the columns as nullable text; data is not recovered.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = "a1e4d58c9033"
down_revision: Union[str, None] = "6bd53b01e704"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    locked = conn.execute(text("SELECT id, external_ref FROM work_items WHERE status = 'locked'")).all()
    deferred = conn.execute(text("SELECT id, external_ref FROM tasks WHERE status = 'deferred'")).all()
    if locked or deferred:
        locked_lines = "\n".join(f"  locked work_item {r.external_ref} ({r.id})" for r in locked)
        deferred_lines = "\n".join(f"  deferred task {r.external_ref} ({r.id})" for r in deferred)
        raise RuntimeError(
            "FEAT-008/T-168 pre-flight: refusing to drop "
            "`locked_from` / `deferred_from` — "
            f"{len(locked)} locked work item(s), "
            f"{len(deferred)} deferred task(s) would lose prior-state data. "
            "Resolve before upgrading:\n"
            f"{locked_lines}\n{deferred_lines}".rstrip()
        )

    op.drop_constraint("ck_tasks_deferred_from", "tasks", type_="check")
    op.drop_column("tasks", "deferred_from")
    op.drop_constraint("ck_work_items_locked_from", "work_items", type_="check")
    op.drop_column("work_items", "locked_from")


def downgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("locked_from", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_items_locked_from",
        "work_items",
        "locked_from IS NULL OR locked_from IN " "('open', 'in_progress', 'locked', 'ready', 'closed')",
    )
    op.add_column(
        "tasks",
        sa.Column("deferred_from", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_tasks_deferred_from",
        "tasks",
        "deferred_from IS NULL OR deferred_from IN "
        "('proposed', 'approved', 'assigning', 'planning', "
        "'plan_review', 'implementing', 'impl_review', 'done', 'deferred')",
    )
