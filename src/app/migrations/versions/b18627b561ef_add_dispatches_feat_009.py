"""Add ``dispatches`` table for the FEAT-009 executor seam (T-212).

The orchestrator's runtime loop dispatches every artifact-producing step
to a registered executor (local / remote / human).  Each invocation gets
one ``dispatches`` row that owns the state machine
``pending → dispatched → completed | failed | cancelled``.

``dispatch_id`` is the correlation key carried into the executor and
echoed back on the webhook reply (``/hooks/executors/<id>``); using it
as the primary key makes the inbound-webhook lookup a trivial PK fetch.

Pre-flight (downgrade): refuse while any non-terminal ``dispatches`` rows
exist — those represent in-flight executor work and dropping the table
would orphan the corresponding runs.  Mirrors the destructive-pre-flight
pattern from BUG-002 (engine_workflows) and FEAT-008 (T-168).

Revision ID: b18627b561ef
Revises: 180ccaa84946
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "b18627b561ef"
down_revision: Union[str, None] = "180ccaa84946"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NON_TERMINAL_STATES = ("pending", "dispatched")


def _refuse_if_in_flight() -> None:
    """Refuse the downgrade while non-terminal dispatches exist."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "dispatches" not in inspector.get_table_names():
        return
    count = conn.execute(text("SELECT COUNT(*) FROM dispatches " "WHERE state IN ('pending', 'dispatched')")).scalar()
    if count:
        raise RuntimeError(
            f"FEAT-009 pre-flight: dispatches has {count} non-terminal row(s) "
            "(state in 'pending' or 'dispatched'). These represent in-flight "
            "executor work; dropping the table would orphan the owning runs. "
            "Cancel or complete the runs before downgrading. See "
            "docs/work-items/FEAT-009-orchestrator-as-pure-orchestrator.md."
        )


def upgrade() -> None:
    op.create_table(
        "dispatches",
        sa.Column(
            "dispatch_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("steps.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id"),
            nullable=False,
        ),
        sa.Column("executor_ref", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        # No server_default for state — the model's Python-side
        # ``default=DispatchState.PENDING`` populates the column at INSERT.
        # Mirrors the Run.status / Step.status convention so autogenerate
        # diffs cleanly against the model.
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("intake", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'dispatched', 'completed', 'failed', 'cancelled')",
            name="ck_state",
        ),
        sa.CheckConstraint(
            "mode IN ('local', 'remote', 'human')",
            name="ck_mode",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('ok', 'error', 'cancelled')",
            name="ck_dispatch_outcome",
        ),
    )
    op.create_index("ix_dispatches_run_id", "dispatches", ["run_id"])
    op.create_index("ix_dispatches_state", "dispatches", ["state"])


def downgrade() -> None:
    _refuse_if_in_flight()
    op.drop_index("ix_dispatches_state", table_name="dispatches")
    op.drop_index("ix_dispatches_run_id", table_name="dispatches")
    op.drop_table("dispatches")
