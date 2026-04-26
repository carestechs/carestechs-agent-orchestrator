"""extend dispatches.mode check constraint to include 'engine' (FEAT-010 / T-231)

Revision ID: c2a8e0f10a01
Revises: 55dc4d883bd8
Create Date: 2026-04-26

FEAT-010 introduces a fourth executor mode (``engine``) alongside
``local``/``remote``/``human``.  The ``ck_mode`` CHECK constraint on
``dispatches.mode`` was originally written with the v0.1 enum members
hard-coded (see ``b18627b561ef_add_dispatches_feat_009.py``); extend it
so engine dispatches can land.  The application-level ``DispatchMode``
enum already includes ``ENGINE``; this migration aligns the database.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c2a8e0f10a01"
down_revision: Union[str, None] = "55dc4d883bd8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_mode", "dispatches", type_="check")
    op.create_check_constraint(
        "ck_mode",
        "dispatches",
        "mode IN ('local', 'remote', 'human', 'engine')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mode", "dispatches", type_="check")
    op.create_check_constraint(
        "ck_mode",
        "dispatches",
        "mode IN ('local', 'remote', 'human')",
    )
