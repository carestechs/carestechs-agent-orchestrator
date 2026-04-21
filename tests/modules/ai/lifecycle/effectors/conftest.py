"""Shared fixtures for effector registry tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.modules.ai.lifecycle.effectors import EffectorContext
from app.modules.ai.schemas import (
    EffectorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)


class RecordingTraceStore:
    """Minimal ``TraceStore`` stub capturing effector-call emissions."""

    def __init__(self) -> None:
        self.effector_calls: list[tuple[uuid.UUID, EffectorCallDto]] = []

    async def record_step(self, run_id: uuid.UUID, step: StepDto) -> None:
        pass

    async def record_policy_call(
        self, run_id: uuid.UUID, call: PolicyCallDto
    ) -> None:
        pass

    async def record_webhook_event(
        self, run_id: uuid.UUID, event: WebhookEventDto
    ) -> None:
        pass

    async def record_operator_signal(
        self, run_id: uuid.UUID, signal: RunSignalDto
    ) -> None:
        pass

    async def record_effector_call(
        self, entity_id: uuid.UUID, call: EffectorCallDto
    ) -> None:
        self.effector_calls.append((entity_id, call))

    async def open_run_stream(
        self, run_id: uuid.UUID
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        return _empty()

    def tail_run_stream(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool = False,
        since: datetime | None = None,
        kinds: frozenset[str] | None = None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        return _empty()


async def _empty() -> AsyncIterator[Any]:
    return
    yield  # pragma: no cover


@pytest.fixture
def trace_store() -> RecordingTraceStore:
    return RecordingTraceStore()


@pytest.fixture
def make_context() -> Any:
    """Factory building ``EffectorContext`` instances with sane defaults."""

    def _factory(
        *,
        entity_type: str = "task",
        entity_id: uuid.UUID | None = None,
        from_state: str | None = "implementing",
        to_state: str = "impl_review",
        transition: str = "T9",
    ) -> EffectorContext:
        return EffectorContext(
            entity_type=cast("Any", entity_type),
            entity_id=entity_id or uuid.uuid4(),
            from_state=from_state,
            to_state=to_state,
            transition=transition,
            correlation_id=uuid.uuid4(),
            db=cast("Any", MagicMock()),
            settings=cast("Any", MagicMock()),
        )

    return _factory
