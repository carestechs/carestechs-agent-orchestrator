"""add engine_item_id to work_items + tasks (FEAT-006 rc2 / T-131a)

Revision ID: 3f1f529fdcef
Revises: b60da02ae693
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3f1f529fdcef"
down_revision: Union[str, None] = "b60da02ae693"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("engine_item_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_work_items_engine_item_id",
        "work_items",
        ["engine_item_id"],
    )
    op.add_column(
        "tasks",
        sa.Column("engine_item_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_tasks_engine_item_id",
        "tasks",
        ["engine_item_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tasks_engine_item_id", "tasks", type_="unique")
    op.drop_column("tasks", "engine_item_id")
    op.drop_constraint(
        "uq_work_items_engine_item_id", "work_items", type_="unique"
    )
    op.drop_column("work_items", "engine_item_id")
