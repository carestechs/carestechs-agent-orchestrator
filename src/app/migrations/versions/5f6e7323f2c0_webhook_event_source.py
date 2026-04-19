"""webhook_events: add source column + extend event_type for GitHub

Revision ID: 5f6e7323f2c0
Revises: 0e79cc47b28c
Create Date: 2026-04-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f6e7323f2c0"
down_revision: Union[str, None] = "0e79cc47b28c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_EVENT_TYPES = "'node_started', 'node_finished', 'node_failed', 'flow_terminated'"
_NEW_EVENT_TYPES = (
    "'node_started', 'node_finished', 'node_failed', 'flow_terminated', "
    "'github_pr_opened', 'github_pr_closed'"
)


def upgrade() -> None:
    op.add_column(
        "webhook_events",
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default="engine",
        ),
    )
    op.create_check_constraint(
        "ck_source",
        "webhook_events",
        "source IN ('engine', 'github')",
    )
    op.drop_constraint("ck_event_type", "webhook_events", type_="check")
    op.create_check_constraint(
        "ck_event_type",
        "webhook_events",
        f"event_type IN ({_NEW_EVENT_TYPES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_event_type", "webhook_events", type_="check")
    op.create_check_constraint(
        "ck_event_type",
        "webhook_events",
        f"event_type IN ({_OLD_EVENT_TYPES})",
    )
    op.drop_constraint("ck_source", "webhook_events", type_="check")
    op.drop_column("webhook_events", "source")
