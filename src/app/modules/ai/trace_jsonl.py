"""JSONL implementation of :class:`~app.modules.ai.trace.TraceStore` (AD-5 v1).

Each record is appended as one NDJSON line to ``<trace_dir>/<run_id>.jsonl``.
Writes are serialized per run via an :class:`asyncio.Lock`; independent runs
do not contend.  Files are created with mode ``0600`` since traces may
contain sensitive policy inputs.

Lines are self-describing via a ``kind`` discriminator (``step`` /
``policy_call`` / ``webhook_event``) so ``open_run_stream`` can rehydrate
typed DTOs on replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import aiofiles

from app.modules.ai.schemas import (
    EffectorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)

logger = logging.getLogger(__name__)


_TAIL_POLL_SECONDS = 0.2
"""How often the follow-mode tail polls the file for new lines.

Module-level so tests can ``monkeypatch.setattr(...)`` it down to ~0.01
for fast CI."""


class JsonlTraceStore:
    """Append NDJSON trace lines to ``<trace_dir>/<run_id>.jsonl``."""

    def __init__(self, trace_dir: Path) -> None:
        self._dir = Path(trace_dir)
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # -- Public API --------------------------------------------------------

    async def record_step(self, run_id: uuid.UUID, step: StepDto) -> None:
        await self._append(
            run_id, {"kind": "step", "data": step.model_dump(mode="json", by_alias=True)}
        )

    async def record_policy_call(self, run_id: uuid.UUID, call: PolicyCallDto) -> None:
        await self._append(
            run_id, {"kind": "policy_call", "data": call.model_dump(mode="json", by_alias=True)}
        )

    async def record_webhook_event(self, run_id: uuid.UUID, event: WebhookEventDto) -> None:
        await self._append(
            run_id,
            {"kind": "webhook_event", "data": event.model_dump(mode="json", by_alias=True)},
        )

    async def record_operator_signal(
        self, run_id: uuid.UUID, signal: RunSignalDto
    ) -> None:
        await self._append(
            run_id,
            {
                "kind": "operator_signal",
                "data": signal.model_dump(mode="json", by_alias=True),
            },
        )

    async def record_effector_call(
        self, entity_id: uuid.UUID, call: EffectorCallDto
    ) -> None:
        """Append an effector-call trace under ``effectors/<entity_id>.jsonl``.

        Separate directory keeps effector fan-out from contending with
        per-run stream readers; the file layout mirrors run traces so
        the same inspection tooling works.
        """
        record = {
            "kind": "effector_call",
            "data": call.model_dump(mode="json", by_alias=True),
        }
        path = self._effector_path(entity_id)
        lock = await self._lock_for(entity_id)
        line = json.dumps(record, default=str) + "\n"
        async with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            newly_created = not path.exists()
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write(line)
            if newly_created:
                try:
                    os.chmod(path, 0o600)
                except OSError as exc:  # pragma: no cover — best-effort hardening
                    logger.warning("chmod 0600 failed for %s: %s", path, exc)

    async def open_run_stream(
        self, run_id: uuid.UUID
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Replay every trace entry for *run_id* as a typed DTO."""
        path = self._path(run_id)
        if not path.is_file():
            return _empty()
        return _replay(path)

    def tail_run_stream(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool = False,
        since: datetime | None = None,
        kinds: frozenset[str] | None = None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Richer reader driving the streaming endpoint (FEAT-004).

        Non-follow mode yields every committed record once and closes.
        Follow mode polls for new lines every
        :data:`_TAIL_POLL_SECONDS` seconds after the initial EOF, and
        awaits file creation if the JSONL file does not yet exist.
        ``kinds=None`` (or empty) means "all kinds"; ``since=None`` means
        "no lower bound".
        """
        effective_kinds = kinds if kinds else None
        return _tail(
            self._path(run_id),
            follow=follow,
            since=since,
            kinds=effective_kinds,
        )

    # -- Internals ---------------------------------------------------------

    def _path(self, run_id: uuid.UUID) -> Path:
        return self._dir / f"{run_id}.jsonl"

    def _effector_path(self, entity_id: uuid.UUID) -> Path:
        return self._dir / "effectors" / f"{entity_id}.jsonl"

    async def _lock_for(self, run_id: uuid.UUID) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(run_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[run_id] = lock
            return lock

    async def _append(self, run_id: uuid.UUID, record: dict[str, Any]) -> None:
        lock = await self._lock_for(run_id)
        path = self._path(run_id)
        line = json.dumps(record, default=str) + "\n"

        async with lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            newly_created = not path.exists()
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write(line)
            if newly_created:
                try:
                    os.chmod(path, 0o600)
                except OSError as exc:  # pragma: no cover — best-effort hardening
                    logger.warning("chmod 0600 failed for %s: %s", path, exc)


# -- Replay helpers --------------------------------------------------------


_DTO_BY_KIND: dict[
    str, type[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]
] = {
    "step": StepDto,
    "policy_call": PolicyCallDto,
    "webhook_event": WebhookEventDto,
    "operator_signal": RunSignalDto,
}


def _parse_line(
    line: str, path: Path, line_number: int
) -> StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto | None:
    """Return the hydrated DTO for *line*, or ``None`` if it can't be parsed.

    Malformed JSON and unknown ``kind`` discriminators both log a
    ``WARNING`` identifying the file + line number and return ``None`` so
    the caller can skip the record without breaking the stream.
    """
    try:
        record_raw: Any = json.loads(line)
    except json.JSONDecodeError as exc:
        logger.warning(
            "malformed trace line %s:%d — %s", path, line_number, exc
        )
        return None
    if not isinstance(record_raw, dict):
        logger.warning(
            "non-object trace line at %s:%d", path, line_number
        )
        return None
    record = cast("dict[str, Any]", record_raw)
    kind_raw: Any = record.get("kind")
    dto_cls = _DTO_BY_KIND.get(kind_raw) if isinstance(kind_raw, str) else None
    if dto_cls is None:
        logger.warning(
            "unknown trace line kind=%r in %s:%d", kind_raw, path, line_number
        )
        return None
    return dto_cls.model_validate(record["data"])


def _record_timestamp(
    dto: StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto,
) -> datetime | None:
    """Return the most representative timestamp for *dto*, or ``None``.

    ``None`` means "record has no timestamp yet" — the ``since`` filter
    treats those records as a pass (lower bound, not mandatory exclude).
    """
    if isinstance(dto, StepDto):
        return dto.dispatched_at or dto.completed_at
    if isinstance(dto, PolicyCallDto):
        return dto.created_at
    if isinstance(dto, RunSignalDto):
        return dto.received_at
    # Must be a WebhookEventDto by elimination.
    return dto.received_at


async def _replay(
    path: Path,
) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
    async with aiofiles.open(path, encoding="utf-8") as f:
        line_number = 0
        async for raw in f:
            line_number += 1
            line = raw.strip()
            if not line:
                continue
            dto = _parse_line(line, path, line_number)
            if dto is None:
                continue
            yield dto


async def _empty() -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
    return
    yield  # pragma: no cover — makes this an async generator


async def _tail(
    path: Path,
    *,
    follow: bool,
    since: datetime | None,
    kinds: frozenset[str] | None,
) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
    """Polling tail reader behind :meth:`JsonlTraceStore.tail_run_stream`.

    Non-follow: open the file, yield every line past the filters, close.
    Follow: additionally poll every :data:`_TAIL_POLL_SECONDS` for new
    lines the writer has appended, yielding them as they arrive.  Awaits
    the file's creation in follow mode if it does not yet exist.
    """
    # Filename-await under follow.
    if not path.is_file():
        if not follow:
            return
        while not path.is_file():
            await asyncio.sleep(_TAIL_POLL_SECONDS)

    async with aiofiles.open(path, encoding="utf-8") as f:
        line_number = 0
        while True:
            async for raw in f:
                line_number += 1
                line = raw.strip()
                if not line:
                    continue
                # Fast kind filter before full DTO hydration — saves cycles
                # when the caller is only interested in one kind.
                if kinds is not None:
                    try:
                        record: Any = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "malformed trace line %s:%d — %s",
                            path, line_number, exc,
                        )
                        continue
                    if isinstance(record, dict):
                        kind: Any = cast("dict[str, Any]", record).get("kind")
                    else:
                        kind = None
                    if kind not in kinds:
                        continue
                dto = _parse_line(line, path, line_number)
                if dto is None:
                    continue
                if since is not None:
                    ts = _record_timestamp(dto)
                    if ts is not None and ts < since:
                        continue
                yield dto
            if not follow:
                return
            await asyncio.sleep(_TAIL_POLL_SECONDS)
