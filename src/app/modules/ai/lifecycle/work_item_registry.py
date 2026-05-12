"""FEAT-014 / T-284 — work-item registration service.

Single chokepoint that enforces *briefs are immutable*: given a parsed
``RunIntakeWorkItem`` (id + kind + optional content), find-or-insert the
matching ``WorkItem`` row keyed on ``external_ref`` and reject any
re-upload whose content body diverges.

State machine — seven branches:

1. No row + no content                  → ``WorkItemNotRegisteredError`` (400).
2. No row + content                     → INSERT new row; bridge concurrent
                                          ``IntegrityError`` via re-read.
3. Row + no content                     → reuse row.
4. Row + content, ``kind`` mismatch     → ``WorkItemKindConflictError`` (409).
5. Row + content, sha256 matches stored → idempotent reuse (no UPDATE).
6. Row + content, stored sha is ``NULL``→ backfill (pre-FEAT-014 row).
7. Row + content, sha256 mismatch       → ``WorkItemContentConflictError`` (409).

The function uses ``session.flush()`` so the *caller* owns the
transaction boundary — matching the FEAT-008 reactor discipline.  No
filesystem access; ``hashlib.sha256`` is computed over
``content.encode("utf-8")`` bytes-as-received with **no normalization**.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    WorkItemContentConflictError,
    WorkItemKindConflictError,
    WorkItemNotRegisteredError,
)
from app.modules.ai.enums import WorkItemStatus
from app.modules.ai.models import WorkItem
from app.modules.ai.schemas import RunIntakeWorkItem


def _derive_title(body_md: str, *, fallback: str) -> str:
    """Return the first H1 in *body_md*, stripped — or *fallback* if none.

    Leading blank lines are tolerated.  ``"# "`` followed by at least one
    non-whitespace character counts; ``"##"`` and below do not.
    """
    for line in body_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()
    return fallback


async def register_work_item(
    session: AsyncSession,
    dto: RunIntakeWorkItem,
    *,
    opened_by: str = "upload",
) -> WorkItem:
    """Find-or-insert a ``WorkItem`` row matching *dto*.

    Raises ``WorkItemNotRegisteredError`` if the row is unknown and no
    content was supplied; ``WorkItemContentConflictError`` /
    ``WorkItemKindConflictError`` (both 409) on immutability violations.
    """
    incoming_sha: str | None = (
        hashlib.sha256(dto.content.encode("utf-8")).hexdigest()
        if dto.content is not None
        else None
    )

    existing = await session.scalar(
        select(WorkItem).where(WorkItem.external_ref == dto.id)
    )

    if existing is None:
        if incoming_sha is None or dto.content is None:
            raise WorkItemNotRegisteredError(f"work-item not registered: {dto.id}")
        wi = WorkItem(
            external_ref=dto.id,
            type=dto.kind.value,
            title=_derive_title(dto.content, fallback=dto.id),
            body_md=dto.content,
            body_sha256=incoming_sha,
            opened_by=opened_by,
            status=WorkItemStatus.OPEN.value,
        )
        session.add(wi)
        try:
            await session.flush()
        except IntegrityError:
            # Concurrent INSERT — another coroutine inserted the same
            # external_ref between our SELECT and our flush.  Roll back
            # the failed INSERT, re-read, and reconcile.
            await session.rollback()
            existing = await session.scalar(
                select(WorkItem).where(WorkItem.external_ref == dto.id)
            )
            assert existing is not None, "IntegrityError implies row exists"
            return _reconcile(existing, dto, incoming_sha)
        return wi

    return _reconcile(existing, dto, incoming_sha)


def _reconcile(
    row: WorkItem,
    dto: RunIntakeWorkItem,
    incoming_sha: str | None,
) -> WorkItem:
    """Apply branches 3-7 of the state machine."""
    if row.type != dto.kind.value:
        raise WorkItemKindConflictError(
            stored_kind=row.type, uploaded_kind=dto.kind.value
        )
    if incoming_sha is None:
        return row
    if row.body_sha256 is None:
        # Branch 6: pre-FEAT-014 row — backfill body + sha256.
        row.body_md = dto.content
        row.body_sha256 = incoming_sha
        return row
    if row.body_sha256 != incoming_sha:
        raise WorkItemContentConflictError(
            stored_sha256=row.body_sha256,
            uploaded_sha256=incoming_sha,
        )
    return row
