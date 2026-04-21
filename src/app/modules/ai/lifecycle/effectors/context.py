"""Immutable carrier + result shape for effector dispatch.

The carrier is deliberately narrow. If an effector needs state beyond
what is here, it pulls from :class:`~app.config.Settings` or from a DI
registry — we do not widen this context per effector. A wide context
rots the seam.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings

EffectorStatus = Literal["ok", "error", "skipped"]


@dataclass(frozen=True, slots=True)
class EffectorContext:
    """Carrier passed to every effector fire. Immutable."""

    entity_type: Literal["work_item", "task"]
    entity_id: uuid.UUID
    from_state: str | None
    to_state: str
    transition: str
    correlation_id: uuid.UUID | None
    db: AsyncSession
    settings: Settings


@dataclass(frozen=True, slots=True)
class EffectorResult:
    """Structured outcome of a single effector fire."""

    effector_name: str
    status: EffectorStatus
    duration_ms: int
    error_code: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
