"""One-shot JSONL → Postgres trace migration (FEAT-013 / T-276).

Scope (deliberate, per brief Section 13 / Note 6 — *don't generalize*):

* Imports ``effector_call`` and ``executor_call`` JSONL lines into the
  corresponding Postgres tables.  These are the two trace kinds without
  an existing SQL home; importing them is what closes the cutover
  window for in-flight production runs.
* For the other four kinds (``step``, ``policy_call``,
  ``webhook_event``, ``operator_signal``) the JSONL line is the
  trace-side projection of a row already written by the runtime /
  signal adapter / webhook router.  This command performs a *read-only
  divergence check* — verifies the SQL row exists — and logs a count
  of missing rows.  It never inserts.

Idempotent.  Re-running over the same JSONL set produces the same
database state and logs the count of already-present skips.

Used at the cutover from ``TRACE_BACKEND=jsonl`` to
``TRACE_BACKEND=postgres``; after the soak it can be deleted entirely.
Not a "live" or "incremental" tool — there are no flags for that, by
design.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.models import (
    EffectorCall,
    ExecutorCall,
    PolicyCall,
    RunSignal,
    Step,
    WebhookEvent,
)
from app.modules.ai.schemas import (
    EffectorCallDto,
    ExecutorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    """Aggregate counters across all JSONL files scanned."""

    files_scanned: int = 0
    effector_calls_inserted: int = 0
    effector_calls_skipped_existing: int = 0
    executor_calls_inserted: int = 0
    executor_calls_skipped_existing: int = 0
    # Divergence: JSONL lines whose corresponding SQL row could not be
    # found.  Reported, not inserted.
    divergence_step: int = 0
    divergence_policy_call: int = 0
    divergence_webhook_event: int = 0
    divergence_operator_signal: int = 0
    malformed_lines: int = 0
    unknown_kind_lines: int = 0
    dry_run: bool = False
    malformed_examples: list[str] = field(default_factory=list[str])

    def total_inserted(self) -> int:
        return self.effector_calls_inserted + self.executor_calls_inserted

    def total_divergence(self) -> int:
        return (
            self.divergence_step
            + self.divergence_policy_call
            + self.divergence_webhook_event
            + self.divergence_operator_signal
        )


def _trace_files(trace_dir: Path) -> Iterator[Path]:
    """Yield every ``.jsonl`` file under *trace_dir*.

    Three layouts produced by the JSONL writer:
      * ``<trace_dir>/<run_id>.jsonl`` — step / policy_call / webhook_event / operator_signal
      * ``<trace_dir>/effectors/<entity_id>.jsonl`` — effector_call
      * ``<trace_dir>/executors/<run_id>.jsonl`` — executor_call
    """
    if not trace_dir.is_dir():
        return
    yield from trace_dir.glob("*.jsonl")
    eff = trace_dir / "effectors"
    if eff.is_dir():
        yield from eff.glob("*.jsonl")
    ex = trace_dir / "executors"
    if ex.is_dir():
        yield from ex.glob("*.jsonl")


def parse_iso_since(raw: str) -> datetime:
    """Parse an ISO-8601 string to a timezone-aware ``datetime``.

    Bare dates and naive datetimes are not accepted — the cutoff must be
    explicit, no implicit timezone.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"--since must include a timezone, got {raw!r}")
    return parsed


def _dto_event_timestamp(dto: object) -> datetime | None:
    """Return the canonical event timestamp for a DTO.

    Used both for ``--since`` filtering and for divergence-check lookup.
    """
    if isinstance(dto, EffectorCallDto):
        return dto.emitted_at
    if isinstance(dto, ExecutorCallDto):
        return dto.finished_at or dto.started_at
    if isinstance(dto, StepDto):
        return dto.dispatched_at or dto.completed_at
    if isinstance(dto, PolicyCallDto):
        return dto.created_at
    if isinstance(dto, RunSignalDto | WebhookEventDto):
        return dto.received_at
    return None


# ---------------------------------------------------------------------------
# Per-line handlers
# ---------------------------------------------------------------------------


async def _import_effector_line(
    session: AsyncSession,
    dto: EffectorCallDto,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> None:
    """Insert one effector_call DTO if no equivalent row already exists.

    Dedupe key: ``(entity_id, transition_key, emitted_at)``.  Effector
    DTOs carry ``emitted_at`` as their canonical wall-clock; it does
    not change between import runs.
    """
    existing = await session.execute(
        select(EffectorCall.id)
        .where(EffectorCall.entity_id == dto.entity_id)
        .where(EffectorCall.transition_key == dto.transition_key)
        .where(EffectorCall.created_at == dto.emitted_at)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        report.effector_calls_skipped_existing += 1
        return

    if dry_run:
        report.effector_calls_inserted += 1
        return

    session.add(
        EffectorCall(
            entity_id=dto.entity_id,
            transition_key=dto.transition_key,
            payload=dto.model_dump(mode="json", by_alias=True),
            outcome=dto.status,
            # Anchor the SQL ``created_at`` to the DTO's wall-clock so a
            # re-import is idempotent.  Without this, ``server_default``
            # would stamp the import time and the dedupe key above would
            # miss on the second run.
            created_at=dto.emitted_at,
        )
    )
    report.effector_calls_inserted += 1


async def _import_executor_line(
    session: AsyncSession,
    dto: ExecutorCallDto,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> None:
    """Insert one executor_call DTO if no equivalent row already exists.

    Dedupe key: ``dispatch_id`` — naturally unique per executor
    invocation, and present on every DTO.
    """
    existing = await session.execute(
        select(ExecutorCall.id).where(ExecutorCall.dispatch_id == dto.dispatch_id).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        report.executor_calls_skipped_existing += 1
        return

    if dry_run:
        report.executor_calls_inserted += 1
        return

    ts = dto.finished_at or dto.started_at
    session.add(
        ExecutorCall(
            run_id=dto.run_id,
            node_name=dto.executor_ref,
            dispatch_id=dto.dispatch_id,
            payload=dto.model_dump(mode="json", by_alias=True),
            outcome=(dto.outcome.value if dto.outcome is not None else "pending"),
            created_at=ts,
        )
    )
    report.executor_calls_inserted += 1


async def _check_divergence_step(session: AsyncSession, dto: StepDto, report: MigrationReport) -> None:
    row = await session.execute(select(Step.id).where(Step.id == dto.id).limit(1))
    if row.scalar_one_or_none() is None:
        report.divergence_step += 1


async def _check_divergence_policy_call(session: AsyncSession, dto: PolicyCallDto, report: MigrationReport) -> None:
    row = await session.execute(select(PolicyCall.id).where(PolicyCall.id == dto.id).limit(1))
    if row.scalar_one_or_none() is None:
        report.divergence_policy_call += 1


async def _check_divergence_webhook_event(session: AsyncSession, dto: WebhookEventDto, report: MigrationReport) -> None:
    row = await session.execute(select(WebhookEvent.id).where(WebhookEvent.id == dto.id).limit(1))
    if row.scalar_one_or_none() is None:
        report.divergence_webhook_event += 1


async def _check_divergence_operator_signal(session: AsyncSession, dto: RunSignalDto, report: MigrationReport) -> None:
    row = await session.execute(select(RunSignal.id).where(RunSignal.id == dto.id).limit(1))
    if row.scalar_one_or_none() is None:
        report.divergence_operator_signal += 1


# ---------------------------------------------------------------------------
# DTO discriminator
# ---------------------------------------------------------------------------


_DTO_BY_KIND: dict[
    str, type[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto | EffectorCallDto | ExecutorCallDto]
] = {
    "step": StepDto,
    "policy_call": PolicyCallDto,
    "webhook_event": WebhookEventDto,
    "operator_signal": RunSignalDto,
    "effector_call": EffectorCallDto,
    "executor_call": ExecutorCallDto,
}


def _parse_line(
    line: str, path: Path, line_number: int, report: MigrationReport
) -> StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto | EffectorCallDto | ExecutorCallDto | None:
    """Hydrate one JSONL line.  Logs + counts on malformed input."""
    try:
        record_raw: Any = json.loads(line)
    except ValueError as exc:
        report.malformed_lines += 1
        if len(report.malformed_examples) < 3:
            report.malformed_examples.append(f"{path}:{line_number} — {exc}")
        return None
    if not isinstance(record_raw, dict):
        report.malformed_lines += 1
        return None
    record = cast("dict[str, Any]", record_raw)
    kind_raw: Any = record.get("kind")
    dto_cls = _DTO_BY_KIND.get(kind_raw) if isinstance(kind_raw, str) else None
    if dto_cls is None:
        report.unknown_kind_lines += 1
        return None
    try:
        return dto_cls.model_validate(record.get("data"))
    except ValueError as exc:
        report.malformed_lines += 1
        if len(report.malformed_examples) < 3:
            report.malformed_examples.append(f"{path}:{line_number} — {exc}")
        return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def migrate(
    *,
    trace_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    since: datetime | None,
    dry_run: bool,
) -> MigrationReport:
    """Scan *trace_dir* and apply per-line handlers in one short session.

    The migration runs in a single transaction so a crash partway leaves
    the DB unchanged (re-running is idempotent either way, but this
    avoids partial-state confusion).
    """
    report = MigrationReport(dry_run=dry_run)

    async with session_factory() as session:
        try:
            for path in _trace_files(trace_dir):
                report.files_scanned += 1
                await _scan_file(session, path, since=since, dry_run=dry_run, report=report)

            if not dry_run:
                await session.commit()
            else:
                await session.rollback()
        except Exception:
            await session.rollback()
            raise

    return report


async def _scan_file(
    session: AsyncSession,
    path: Path,
    *,
    since: datetime | None,
    dry_run: bool,
    report: MigrationReport,
) -> None:
    """Stream one ``.jsonl`` file and dispatch each line to the right handler."""
    line_number = 0
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line_number += 1
            line = raw.strip()
            if not line:
                continue
            dto = _parse_line(line, path, line_number, report)
            if dto is None:
                continue

            if since is not None:
                ts = _dto_event_timestamp(dto)
                if ts is not None and ts < since:
                    continue

            if isinstance(dto, EffectorCallDto):
                await _import_effector_line(session, dto, report, dry_run=dry_run)
            elif isinstance(dto, ExecutorCallDto):
                await _import_executor_line(session, dto, report, dry_run=dry_run)
            elif isinstance(dto, StepDto):
                await _check_divergence_step(session, dto, report)
            elif isinstance(dto, PolicyCallDto):
                await _check_divergence_policy_call(session, dto, report)
            elif isinstance(dto, WebhookEventDto):
                await _check_divergence_webhook_event(session, dto, report)
            else:
                # Only ``RunSignalDto`` remains in the discriminator after
                # the branches above; pyright treats this as exhaustive.
                await _check_divergence_operator_signal(session, dto, report)


def format_report(report: MigrationReport) -> str:
    """Human-readable summary for CLI output."""
    verb = "would insert" if report.dry_run else "inserted"
    lines = [
        f"migrate-traces ({'dry-run' if report.dry_run else 'committed'}):",
        f"  files scanned:          {report.files_scanned}",
        f"  effector_calls {verb}: {report.effector_calls_inserted}",
        f"  effector_calls skipped (already present): {report.effector_calls_skipped_existing}",
        f"  executor_calls {verb}: {report.executor_calls_inserted}",
        f"  executor_calls skipped (already present): {report.executor_calls_skipped_existing}",
        "  divergence (JSONL line whose SQL row is missing):",
        f"    step:            {report.divergence_step}",
        f"    policy_call:     {report.divergence_policy_call}",
        f"    webhook_event:   {report.divergence_webhook_event}",
        f"    operator_signal: {report.divergence_operator_signal}",
        f"  malformed lines: {report.malformed_lines}",
        f"  unknown-kind lines: {report.unknown_kind_lines}",
    ]
    if report.malformed_examples:
        lines.append("  malformed examples (first 3):")
        lines.extend(f"    - {ex}" for ex in report.malformed_examples)
    return "\n".join(lines)


__all__ = ["MigrationReport", "format_report", "migrate"]
