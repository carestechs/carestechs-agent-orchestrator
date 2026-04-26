"""engine_workflows: add tenant_id, change PK to (tenant_id, name) (BUG-002)

Revision ID: 180ccaa84946
Revises: a1e4d58c9033
Create Date: 2026-04-25 21:29:09.744421

The cache key was tenant-blind, so switching ``FLOW_ENGINE_TENANT_API_KEY``
caused the orchestrator to return the prior tenant's ``engine_workflow_id``
on every lookup. See ``docs/work-items/BUG-002-engine-workflows-tenant-scope.md``.

Pre-flight: this migration is destructive in the operator-facing sense —
it refuses to run while ``engine_workflows`` has rows. Operators must
``TRUNCATE engine_workflows`` before upgrading. Truncation has no
functional cost: the orchestrator's lifespan re-bootstraps the cache
against the current tenant on next boot via one engine round-trip per
declared workflow.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql


revision: str = "180ccaa84946"
down_revision: Union[str, None] = "a1e4d58c9033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _refuse_if_populated(direction: str) -> None:
    conn = op.get_bind()
    count = conn.execute(text("SELECT COUNT(*) FROM engine_workflows")).scalar()
    if count:
        raise RuntimeError(
            f"BUG-002 pre-flight: engine_workflows has {count} row(s); "
            f"TRUNCATE engine_workflows before {direction}-applying this "
            "migration so the orchestrator re-bootstraps against the current "
            "tenant. See docs/work-items/BUG-002-engine-workflows-tenant-scope.md."
        )


def upgrade() -> None:
    _refuse_if_populated("up")
    op.drop_constraint("engine_workflows_pkey", "engine_workflows", type_="primary")
    op.add_column(
        "engine_workflows",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_primary_key(
        "engine_workflows_pkey",
        "engine_workflows",
        ["tenant_id", "name"],
    )


def downgrade() -> None:
    _refuse_if_populated("down")
    op.drop_constraint("engine_workflows_pkey", "engine_workflows", type_="primary")
    op.drop_column("engine_workflows", "tenant_id")
    op.create_primary_key(
        "engine_workflows_pkey",
        "engine_workflows",
        ["name"],
    )
