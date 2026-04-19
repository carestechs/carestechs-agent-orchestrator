"""add task_assignments

Revision ID: ca26e2932b47
Revises: 0f7df742fc81
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ca26e2932b47"
down_revision: Union[str, None] = "0f7df742fc81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_type", sa.Text(), nullable=False),
        sa.Column("assignee_id", sa.Text(), nullable=False),
        sa.Column("assigned_by", sa.Text(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "assignee_type IN ('dev', 'agent')",
            name="ck_assignee_type",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial-unique: at most one active assignment per task.
    op.create_index(
        "ix_task_assignments_active",
        "task_assignments",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "ix_task_assignments_task_assigned",
        "task_assignments",
        ["task_id", sa.text("assigned_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_task_assignments_task_assigned", table_name="task_assignments")
    op.drop_index("ix_task_assignments_active", table_name="task_assignments")
    op.drop_table("task_assignments")
