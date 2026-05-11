"""Add ``effector_calls`` and ``executor_calls`` for FEAT-013 / T-272.

Two append-only tables that back the Postgres trace store.  The other
four trace kinds (``step``, ``policy_call``, ``webhook_event``,
``operator_signal``) already live in their respective tables and are
*read* by ``PostgresTraceStore`` — see ``Decision 2`` in
``docs/design/feat-013-trace-tail-mechanism.md`` (no double-write).

Forward-only.  ``downgrade()`` drops the tables; production runs do
not roll back FEAT-013 (the data is the trace history).  Mirrors the
"destructive downgrade" warning from earlier FEATs.

Revision ID: 7a3f1d2c9e4b
Revises: c2a8e0f10a01
Create Date: 2026-05-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a3f1d2c9e4b"
down_revision: Union[str, None] = "c2a8e0f10a01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "effector_calls",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("transition_key", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_effector_calls_entity_created",
        "effector_calls",
        ["entity_id", "created_at"],
    )
    op.create_index(
        "ix_effector_calls_transition_key",
        "effector_calls",
        ["transition_key"],
    )

    op.create_table(
        "executor_calls",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column(
            "dispatch_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_executor_calls_run_created",
        "executor_calls",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_executor_calls_node_name",
        "executor_calls",
        ["node_name"],
    )


def downgrade() -> None:
    """Destructive — production runs do not roll back FEAT-013.

    Dropping these tables erases the historical trace rows they hold.
    The four other trace kinds are not affected (they live in
    ``steps`` / ``policy_calls`` / ``webhook_events`` / ``run_signals``
    and are not owned by this migration).
    """
    op.drop_index("ix_executor_calls_node_name", table_name="executor_calls")
    op.drop_index("ix_executor_calls_run_created", table_name="executor_calls")
    op.drop_table("executor_calls")
    op.drop_index("ix_effector_calls_transition_key", table_name="effector_calls")
    op.drop_index("ix_effector_calls_entity_created", table_name="effector_calls")
    op.drop_table("effector_calls")
