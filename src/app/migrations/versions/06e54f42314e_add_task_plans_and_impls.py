"""add task_plans and task_implementations

Revision ID: 06e54f42314e
Revises: 6b5c3d34c0c5
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "06e54f42314e"
down_revision: Union[str, None] = "6b5c3d34c0c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("plan_path", sa.Text(), nullable=False),
        sa.Column("plan_sha", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_plans_task_submitted",
        "task_plans",
        ["task_id", "submitted_at"],
        unique=False,
    )

    op.create_table(
        "task_implementations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_implementations_task_submitted",
        "task_implementations",
        ["task_id", "submitted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_implementations_task_submitted",
        table_name="task_implementations",
    )
    op.drop_table("task_implementations")
    op.drop_index("ix_task_plans_task_submitted", table_name="task_plans")
    op.drop_table("task_plans")
