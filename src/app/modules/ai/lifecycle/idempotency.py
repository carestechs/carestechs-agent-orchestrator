"""Signal idempotency helper (FEAT-006 / T-114).

A stable SHA-256 over the canonical JSON of ``(entity_id, signal_name,
payload)`` identifies each deterministic-flow signal.  Handlers call
:func:`check_and_record` before running side effects: first delivery
returns ``(True, ts)``; any subsequent delivery with the same payload
returns ``(False, ts_of_first)``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import LifecycleSignal


def compute_signal_key(
    entity_id: uuid.UUID, signal_name: str, payload: Mapping[str, Any]
) -> str:
    """Canonical, deterministic dedupe key.

    Keys sorted, UTF-8 encoded, no whitespace.  Non-JSON-serialisable values
    (UUIDs, datetimes) fall through ``default=str``.
    """
    canonical = json.dumps(
        [str(entity_id), signal_name, dict(payload)],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def check_and_record(
    db: AsyncSession,
    *,
    key: str,
    entity_id: uuid.UUID,
    signal_name: str,
) -> tuple[bool, datetime]:
    """Record a signal key; return ``(is_new, recorded_at)``.

    Uses ``INSERT ... ON CONFLICT DO NOTHING RETURNING``; on conflict looks
    up the prior row to surface the original timestamp.
    """
    stmt = (
        pg_insert(LifecycleSignal)
        .values(key=key, entity_id=entity_id, signal_name=signal_name)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(LifecycleSignal.recorded_at)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return True, row
    prior = await db.scalar(
        select(LifecycleSignal.recorded_at).where(LifecycleSignal.key == key)
    )
    assert prior is not None, "UNIQUE constraint guarantees the row exists"
    return False, prior
