"""add approvals

Revision ID: 0e79cc47b28c
Revises: ca26e2932b47
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e79cc47b28c"
down_revision: Union[str, None] = "ca26e2932b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decided_by_role", sa.Text(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('proposed', 'plan', 'impl')",
            name="ck_stage",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_decision",
        ),
        sa.CheckConstraint(
            "decided_by_role IN ('admin', 'dev')",
            name="ck_decided_by_role",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approvals_task_stage_time",
        "approvals",
        ["task_id", "stage", "decided_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_task_stage_time", table_name="approvals")
    op.drop_table("approvals")
