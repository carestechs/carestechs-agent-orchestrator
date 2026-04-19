"""add work_items

Revision ID: 8e11685902fe
Revises: f3886382f1ce
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e11685902fe"
down_revision: Union[str, None] = "f3886382f1ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("locked_from", sa.Text(), nullable=True),
        sa.Column("opened_by", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Text(), nullable=True),
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
            "status IN ('open', 'in_progress', 'locked', 'ready', 'closed')",
            name="ck_status",
        ),
        sa.CheckConstraint(
            "type IN ('FEAT', 'BUG', 'IMP')",
            name="ck_type",
        ),
        sa.CheckConstraint(
            "locked_from IS NULL OR locked_from IN ('open', 'in_progress', 'locked', 'ready', 'closed')",
            name="ck_work_items_locked_from",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_ref", name="uq_work_items_external_ref"),
    )
    op.create_index(
        "ix_work_items_status_updated_at",
        "work_items",
        ["status", sa.text("updated_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_items_status_updated_at", table_name="work_items")
    op.drop_table("work_items")
