"""add lifecycle_signals

Revision ID: 6b5c3d34c0c5
Revises: 5f6e7323f2c0
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b5c3d34c0c5"
down_revision: Union[str, None] = "5f6e7323f2c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_signals",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("signal_name", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_lifecycle_signals_entity_name",
        "lifecycle_signals",
        ["entity_id", "signal_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_lifecycle_signals_entity_name", table_name="lifecycle_signals")
    op.drop_table("lifecycle_signals")
