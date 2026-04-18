"""Shared rigging for FEAT-005 lifecycle-agent integration tests.

Each test spins up a temp repo with the lifecycle-agent YAML, the
``.ai-framework/prompts/`` directory, and a fixture work item, points
``Settings.repo_root`` + ``Settings.agents_dir`` at it, and delivers
operator signals via the supervisor in-process.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import RunStatus, StepStatus
from app.modules.ai.models import Run, Step
from app.modules.ai.supervisor import RunSupervisor

_REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
_AGENT_YAML = _REPO_ROOT / "agents" / "lifecycle-agent@0.1.0.yaml"
_PROMPTS_DIR = _REPO_ROOT / ".ai-framework" / "prompts"
_WORK_ITEM_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "work-items" / "IMP-fixture.md"
)


@dataclass
class LifecycleRepo:
    """A tmp-repo layout the lifecycle agent can operate on."""

    root: Path
    work_item_path: Path
    tasks_dir: Path
    plans_dir: Path


def prepare_repo(tmp_path: Path) -> LifecycleRepo:
    """Build a minimal repo mirror under *tmp_path*.

    Copies the lifecycle-agent YAML, the prompts dir, and the fixture
    work item; creates empty ``tasks/`` and ``plans/`` so the write tools
    can land artifacts.
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "agents").mkdir()
    (root / ".ai-framework" / "prompts").mkdir(parents=True)
    (root / "tasks").mkdir()
    (root / "plans").mkdir()
    (root / "docs" / "work-items").mkdir(parents=True)

    shutil.copy(_AGENT_YAML, root / "agents" / _AGENT_YAML.name)
    for prompt in _PROMPTS_DIR.glob("*.md"):
        shutil.copy(prompt, root / ".ai-framework" / "prompts" / prompt.name)
    shutil.copy(
        _WORK_ITEM_FIXTURE, root / "docs" / "work-items" / _WORK_ITEM_FIXTURE.name
    )

    return LifecycleRepo(
        root=root,
        work_item_path=root / "docs" / "work-items" / _WORK_ITEM_FIXTURE.name,
        tasks_dir=root / "tasks",
        plans_dir=root / "plans",
    )


async def wait_for_pause(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    task_id: str,
    timeout_seconds: float = 3.0,
) -> Step:
    """Wait until *run_id* has an in-progress ``wait_for_implementation`` step.

    Pause steps are the ones with ``engine_run_id IS NULL`` AND
    ``node_name='wait_for_implementation'`` AND ``status='in_progress'``.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with factory() as session:
            step = await session.scalar(
                select(Step)
                .where(Step.run_id == run_id)
                .where(Step.node_name == "wait_for_implementation")
                .where(Step.status == StepStatus.IN_PROGRESS)
                .order_by(Step.step_number.desc())
                .limit(1)
            )
        if step is not None and step.engine_run_id is None:
            return step
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"run {run_id} did not pause at wait_for_implementation for task {task_id} "
        f"within {timeout_seconds}s"
    )


async def deliver_signal_when_paused(
    factory: async_sessionmaker[AsyncSession],
    supervisor: RunSupervisor,
    run_id: uuid.UUID,
    *,
    task_id: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 3.0,
) -> None:
    """Wait for the pause step then deliver an implementation-complete signal."""
    await wait_for_pause(
        factory, run_id, task_id=task_id, timeout_seconds=timeout_seconds
    )
    supervisor.deliver_signal(
        run_id, "implementation-complete", task_id, payload or {}
    )


async def wait_for_terminal(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    timeout_seconds: float = 5.0,
) -> Run:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with factory() as session:
            run = await session.scalar(select(Run).where(Run.id == run_id))
        if run is not None and RunStatus(run.status) in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run
        await asyncio.sleep(0.02)
    async with factory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id))
    raise AssertionError(
        f"run {run_id} did not reach terminal within {timeout_seconds}s; "
        f"status={run.status if run else 'missing'}"
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)
