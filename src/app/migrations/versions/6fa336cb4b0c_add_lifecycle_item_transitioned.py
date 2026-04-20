"""add lifecycle_item_transitioned to event_type check (FEAT-006 rc2 / T-130)

Revision ID: 6fa336cb4b0c
Revises: 3f1f529fdcef
Create Date: 2026-04-19

"""

from typing import Sequence, Union

from alembic import op

revision: str = "6fa336cb4b0c"
down_revision: Union[str, None] = "3f1f529fdcef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PREV_EVENT_TYPES = (
    "'node_started', 'node_finished', 'node_failed', 'flow_terminated', "
    "'github_pr_opened', 'github_pr_closed'"
)
_NEW_EVENT_TYPES = (
    "'node_started', 'node_finished', 'node_failed', 'flow_terminated', "
    "'github_pr_opened', 'github_pr_closed', 'lifecycle_item_transitioned'"
)


def upgrade() -> None:
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
        f"event_type IN ({_PREV_EVENT_TYPES})",
    )
