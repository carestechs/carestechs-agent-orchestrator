"""add tasks

Revision ID: 0f7df742fc81
Revises: 8e11685902fe
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f7df742fc81"
down_revision: Union[str, None] = "8e11685902fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TASK_STATUS_VALUES = (
    "'proposed', 'approved', 'assigning', 'planning', 'plan_review', "
    "'implementing', 'impl_review', 'done', 'deferred'"
)


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposer_type", sa.Text(), nullable=False),
        sa.Column("proposer_id", sa.Text(), nullable=False),
        sa.Column("deferred_from", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_TASK_STATUS_VALUES})",
            name="ck_status",
        ),
        sa.CheckConstraint(
            "proposer_type IN ('admin', 'agent')",
            name="ck_proposer_type",
        ),
        sa.CheckConstraint(
            f"deferred_from IS NULL OR deferred_from IN ({_TASK_STATUS_VALUES})",
            name="ck_tasks_deferred_from",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", "external_ref", name="uq_tasks_work_item_ref"),
    )
    op.create_index(
        "ix_tasks_work_item_status",
        "tasks",
        ["work_item_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_work_item_status", table_name="tasks")
    op.drop_table("tasks")
