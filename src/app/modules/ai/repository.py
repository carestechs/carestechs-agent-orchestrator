"""Thin typed query helpers for the AI module's read side.

Keeps ``service.py`` readable by pulling SQL out of control-flow code.  All
helpers take an :class:`AsyncSession` so the caller owns the transaction
lifecycle.  Functions return ORM rows; the service layer adapts them to
DTOs.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import PolicyCall, Run, RunSignal, Step

# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def count_runs(
    db: AsyncSession,
    *,
    status: str | None = None,
    agent_ref: str | None = None,
) -> int:
    """Return the total number of runs matching the provided filters."""
    stmt = select(func.count()).select_from(Run)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    if agent_ref is not None:
        stmt = stmt.where(Run.agent_ref == agent_ref)
    result = await db.scalar(stmt)
    return int(result or 0)


async def select_runs(
    db: AsyncSession,
    *,
    status: str | None = None,
    agent_ref: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> list[Run]:
    """Paginated run rows ordered by ``(started_at DESC, id DESC)``.

    The secondary ordering on ``id`` guarantees stability when multiple
    runs share a timestamp.
    """
    offset = max(page - 1, 0) * page_size
    stmt = select(Run).order_by(Run.started_at.desc(), Run.id.desc())
    if status is not None:
        stmt = stmt.where(Run.status == status)
    if agent_ref is not None:
        stmt = stmt.where(Run.agent_ref == agent_ref)
    stmt = stmt.limit(page_size).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_run_by_id(db: AsyncSession, run_id: uuid.UUID) -> Run | None:
    return await db.scalar(select(Run).where(Run.id == run_id))


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def count_steps(db: AsyncSession, run_id: uuid.UUID) -> int:
    result = await db.scalar(
        select(func.count()).select_from(Step).where(Step.run_id == run_id)
    )
    return int(result or 0)


async def latest_step(db: AsyncSession, run_id: uuid.UUID) -> Step | None:
    return await db.scalar(
        select(Step)
        .where(Step.run_id == run_id)
        .order_by(Step.step_number.desc())
        .limit(1)
    )


async def select_steps(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> list[Step]:
    offset = max(page - 1, 0) * page_size
    result = await db.execute(
        select(Step)
        .where(Step.run_id == run_id)
        .order_by(Step.step_number.asc())
        .limit(page_size)
        .offset(offset)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Policy calls
# ---------------------------------------------------------------------------


async def count_policy_calls(db: AsyncSession, run_id: uuid.UUID) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(PolicyCall)
        .where(PolicyCall.run_id == run_id)
    )
    return int(result or 0)


async def select_policy_calls(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> list[PolicyCall]:
    offset = max(page - 1, 0) * page_size
    result = await db.execute(
        select(PolicyCall)
        .where(PolicyCall.run_id == run_id)
        .order_by(PolicyCall.created_at.asc(), PolicyCall.id.asc())
        .limit(page_size)
        .offset(offset)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Run signals (FEAT-005)
# ---------------------------------------------------------------------------


def compute_github_pr_dedupe_key(pr_number: int, delivery_id: str) -> str:
    """Deterministic dedupe key for a GitHub PR webhook event (FEAT-006).

    Shape: ``github:pr:<pr_number>:<delivery_id>``.  Replayed deliveries from
    GitHub produce the same key and collide on the UNIQUE constraint.
    """
    return f"github:pr:{pr_number}:{delivery_id}"


def compute_signal_dedupe_key(run_id: uuid.UUID, name: str, task_id: str | None) -> str:
    """Deterministic dedupe key for an operator signal.

    Idempotency is scoped to ``(run_id, name, task_id)``: two calls with the
    same tuple produce the same key and collide on the UNIQUE constraint.
    """
    raw = f"{run_id}:{name}:{task_id or ''}".encode()
    return hashlib.sha256(raw).hexdigest()


async def create_run_signal(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    name: str,
    task_id: str | None,
    payload: dict[str, Any],
    dedupe_key: str,
) -> tuple[RunSignal, bool]:
    """Persist a new signal (idempotent on ``dedupe_key``).

    Returns ``(row, created)``.  On UNIQUE conflict returns the existing row
    with ``created=False`` — callers use this to suppress repeat wakes.
    """
    stmt = (
        pg_insert(RunSignal)
        .values(
            run_id=run_id,
            name=name,
            task_id=task_id,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(RunSignal)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row, True
    existing = await db.scalar(
        select(RunSignal).where(RunSignal.dedupe_key == dedupe_key)
    )
    assert existing is not None, "UNIQUE constraint guarantees this row exists"
    return existing, False


async def select_signals_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[RunSignal]:
    """Return all signals for *run_id*, ordered by ``received_at`` ascending."""
    result = await db.execute(
        select(RunSignal)
        .where(RunSignal.run_id == run_id)
        .order_by(RunSignal.received_at.asc(), RunSignal.id.asc())
    )
    return list(result.scalars().all())
