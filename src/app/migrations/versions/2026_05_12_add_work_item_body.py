"""add work_items.body_md + body_sha256 (FEAT-014 / T-282)

Revision ID: 9c4d2e8a1f7b
Revises: 7a3f1d2c9e4b
Create Date: 2026-05-12

FEAT-014 replaces the orchestrator's filesystem read of work-item briefs
with an upload-then-dedupe-by-id flow.  Two new nullable columns:

- ``body_md TEXT`` — uploaded markdown body.
- ``body_sha256 TEXT`` — hex sha256 of the bytes-as-received (no
  normalization).  CHECK constraint enforces the 64-hex-char format.

Both columns are nullable so pre-FEAT-014 rows (registered via the
deprecated ``source_path``) continue to function through the
deprecation window; T-288's compat shim and T-290's
``import-work-items`` backfill them.

**Destructive downgrade — production runs do not roll back.**
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9c4d2e8a1f7b"
down_revision: Union[str, None] = "7a3f1d2c9e4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("body_md", sa.Text(), nullable=True))
    op.add_column("work_items", sa.Column("body_sha256", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_work_items_body_sha256_format",
        "work_items",
        "body_sha256 IS NULL OR body_sha256 ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_work_items_body_sha256_format", "work_items", type_="check")
    op.drop_column("work_items", "body_sha256")
    op.drop_column("work_items", "body_md")
