"""add engine_workflows (FEAT-006 rc2 / T-129)

Revision ID: b60da02ae693
Revises: 2b7acf59a48b
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b60da02ae693"
down_revision: Union[str, None] = "2b7acf59a48b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engine_workflows",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("engine_workflow_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("engine_workflows")
