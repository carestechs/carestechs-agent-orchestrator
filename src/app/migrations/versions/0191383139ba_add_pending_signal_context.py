"""add pending_signal_context (FEAT-006 rc2 phase 2 / T-133)

Revision ID: 0191383139ba
Revises: 6fa336cb4b0c
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0191383139ba"
down_revision: Union[str, None] = "6fa336cb4b0c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_signal_context",
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("signal_name", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("correlation_id"),
    )


def downgrade() -> None:
    op.drop_table("pending_signal_context")
