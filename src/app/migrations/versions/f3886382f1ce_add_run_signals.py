"""add run_signals

Revision ID: f3886382f1ce
Revises: 4818d28a0750
Create Date: 2026-04-18 14:45:54.476101

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3886382f1ce"
down_revision: Union[str, None] = "4818d28a0750"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_run_signals_dedupe_key"),
    )
    op.create_index(
        "ix_run_signals_run_id_received_at",
        "run_signals",
        ["run_id", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_run_signals_run_id_received_at", table_name="run_signals")
    op.drop_table("run_signals")
