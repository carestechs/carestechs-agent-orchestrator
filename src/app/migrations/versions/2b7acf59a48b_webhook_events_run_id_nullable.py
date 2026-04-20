"""webhook_events: run_id nullable (FEAT-006: GitHub webhooks)

Revision ID: 2b7acf59a48b
Revises: 06e54f42314e
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2b7acf59a48b"
down_revision: Union[str, None] = "06e54f42314e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "webhook_events",
        "run_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "webhook_events",
        "run_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
