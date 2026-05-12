"""Trace-retention sweep (FEAT-013 / T-277).

Deletes ``effector_calls`` and ``executor_calls`` rows older than
``settings.trace_retention_days``.  The other four trace kinds
(``step`` / ``policy_call`` / ``webhook_event`` / ``operator_signal``)
live in tables this module does NOT touch — their retention follows the
ops policy for run state at large (separate concern).

Wired to a Typer command at ``orchestrator trace-retention-sweep``;
operators schedule that command externally (cron / systemd timer /
Kubernetes ``CronJob``).  No in-process scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import EffectorCall, ExecutorCall


@dataclass
class RetentionReport:
    """Result of a single retention pass."""

    cutoff: datetime
    effector_calls_deleted: int
    executor_calls_deleted: int
    dry_run: bool

    def total(self) -> int:
        return self.effector_calls_deleted + self.executor_calls_deleted


async def sweep(
    session: AsyncSession,
    *,
    retention_days: int,
    dry_run: bool,
) -> RetentionReport:
    """Run one retention sweep against the two trace-row tables.

    ``dry_run=True`` returns counts that *would* be deleted; the
    transaction is rolled back implicitly because no commit fires.
    ``dry_run=False`` commits the deletes in a single transaction.
    """
    if retention_days <= 0:
        raise ValueError(f"retention_days must be > 0, got {retention_days!r}")

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    eff_count = await session.scalar(
        select(func.count()).select_from(EffectorCall).where(EffectorCall.created_at < cutoff)
    )
    exec_count = await session.scalar(
        select(func.count()).select_from(ExecutorCall).where(ExecutorCall.created_at < cutoff)
    )
    eff_n = int(eff_count or 0)
    exec_n = int(exec_count or 0)

    if not dry_run and (eff_n or exec_n):
        await session.execute(delete(EffectorCall).where(EffectorCall.created_at < cutoff))
        await session.execute(delete(ExecutorCall).where(ExecutorCall.created_at < cutoff))
        await session.commit()

    return RetentionReport(
        cutoff=cutoff,
        effector_calls_deleted=eff_n,
        executor_calls_deleted=exec_n,
        dry_run=dry_run,
    )


def format_report(report: RetentionReport) -> str:
    """Human-readable summary for CLI output."""
    verb = "would delete" if report.dry_run else "deleted"
    cutoff_iso = report.cutoff.isoformat()
    return (
        f"trace retention sweep ({'dry-run' if report.dry_run else 'committed'}): "
        f"cutoff={cutoff_iso} effector_calls {verb} {report.effector_calls_deleted} "
        f"executor_calls {verb} {report.executor_calls_deleted} "
        f"(total {report.total()})"
    )


__all__ = ["RetentionReport", "format_report", "sweep"]
