"""add executor_dispatch_result to webhook_events.event_type check (FEAT-009 / T-216)

Revision ID: 55dc4d883bd8
Revises: b18627b561ef
Create Date: 2026-04-26

The remote-executor webhook (``/hooks/executors/<executor_id>``) persists
inbound deliveries to ``webhook_events`` for forensics — same persist-first
discipline as ``/hooks/engine/*``. The CHECK constraint on ``event_type``
needs to admit the new value.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "55dc4d883bd8"
down_revision: Union[str, None] = "b18627b561ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PREV_EVENT_TYPES = (
    "'node_started', 'node_finished', 'node_failed', 'flow_terminated', "
    "'github_pr_opened', 'github_pr_closed', 'lifecycle_item_transitioned'"
)
_NEW_EVENT_TYPES = _PREV_EVENT_TYPES + ", 'executor_dispatch_result'"


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
