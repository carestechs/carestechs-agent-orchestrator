"""Regression for the runtime threading taskId from LifecycleMemory.

After the lifecycle agent's ``generate_tasks`` step writes
``current_task_id`` into ``RunMemory.data['lifecycle.v1']``, every
subsequent dispatch's intake MUST carry ``taskId`` so:

* LLM-content templates (``generate_plan``, ``review_implementation``)
  can substitute ``{taskId}`` without each binding repeating the
  memory-lookup boilerplate.
* Local executors (``correct_implementation``) can resolve the
  symbolic task ref without re-parsing memory.

Before this fix the runtime only threaded ``Run.intake`` plus
``runId``/``nodeName`` — task-scoped nodes hit
``prompt_render_failed: missing template variable 'taskId'`` on the
first LLM call.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import NullPool, delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.modules.ai.agents import AgentDefinition, _parse_file
from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.models import Dispatch, Run, RunMemory, Step
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.tools.lifecycle.memory import (
    LifecycleMemory,
    LifecycleTask,
    write_lifecycle_memory,
)
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _build_session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(
    test_database_url: str, migrated: None, fresh_pool: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield _build_session_factory(test_database_url)


def _build_agent(tmp_path: Path) -> AgentDefinition:
    """Three-node flow: ``start`` (synthetic) → ``capture_intake`` → ``done``.

    The flow resolver treats ``entryNode`` as the previous-node marker
    on the first iteration; its transition target is the first node
    actually dispatched.  Mirrors the lifecycle-agent@0.3.0 YAML's
    ``start: [load_work_item]`` shape.
    """
    spec: dict[str, Any] = {
        "ref": "intake-test@0.1.0",
        "version": "0.1.0",
        "description": "Capture-intake test agent.",
        "nodes": [
            {"name": "start", "description": "synthetic", "inputSchema": {"type": "object"}},
            {
                "name": "capture_intake",
                "description": "Captures the dispatch intake into a list.",
                "inputSchema": {"type": "object"},
            },
            {"name": "done", "description": "Terminal sink.", "inputSchema": {"type": "object"}},
        ],
        "flow": {
            "entryNode": "start",
            "transitions": {
                "start": ["capture_intake"],
                "capture_intake": ["done"],
                "done": [],
            },
            "policy": "deterministic",
        },
        "intakeSchema": {"type": "object"},
        "terminalNodes": ["done"],
    }
    path = tmp_path / "intake-test@0.1.0.yaml"
    path.write_text(yaml.safe_dump(spec))
    return _parse_file(path, repo_root=tmp_path)


async def _seed_with_memory(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent: AgentDefinition,
    run_intake: dict[str, Any],
    memory: LifecycleMemory,
) -> uuid.UUID:
    async with session_factory() as session:
        run = Run(
            agent_ref=agent.ref,
            agent_definition_hash=agent.agent_definition_hash or "sha256:" + "0" * 64,
            intake=run_intake,
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/intake-test.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data=write_lifecycle_memory(memory)))
        await session.commit()
        await session.refresh(run)
        return run.id


async def test_taskid_threaded_into_dispatch_intake(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """LifecycleMemory.current_task_id surfaces as intake.taskId."""
    captured: list[Mapping[str, Any]] = []

    async def _capture(ctx: DispatchContext) -> Mapping[str, Any]:
        captured.append(dict(ctx.intake))
        return {"ok": True}

    agent = _build_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "capture_intake",
        LocalExecutor(ref="local:capture_intake", handler=_capture),
    )

    run_id = await _seed_with_memory(
        session_factory,
        agent=agent,
        run_intake={},
        memory=LifecycleMemory(
            tasks=[LifecycleTask(id="T-007", title="x", executor="claude-code")],
            current_task_id="T-007",
        ),
    )

    try:
        await run_deterministic_loop(
            run_id=run_id,
            agent=agent,
            trace=NoopTraceStore(),
            supervisor=RunSupervisor(),
            registry=registry,
            session_factory=session_factory,
            cancel_event=asyncio.Event(),
            dispatch_timeout_seconds=10,
        )

        assert len(captured) == 1, f"handler should have run; captured={captured}"
        intake = captured[0]
        assert intake.get("taskId") == "T-007"
    finally:
        async with session_factory() as session:
            await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
            await session.execute(delete(Step).where(Step.run_id == run_id))
            await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
            await session.execute(delete(Run).where(Run.id == run_id))
            await session.commit()


async def test_run_intake_taskid_takes_precedence(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Caller-supplied ``Run.intake.taskId`` is not stomped by memory."""
    captured: list[Mapping[str, Any]] = []

    async def _capture(ctx: DispatchContext) -> Mapping[str, Any]:
        captured.append(dict(ctx.intake))
        return {"ok": True}

    agent = _build_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "capture_intake",
        LocalExecutor(ref="local:capture_intake", handler=_capture),
    )

    async with session_factory() as session:
        run = Run(
            agent_ref=agent.ref,
            agent_definition_hash="sha256:" + "0" * 64,
            intake={"taskId": "T-FROM-INTAKE"},
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri=f"file://{tmp_path / 'trace.jsonl'}",
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        memory = LifecycleMemory(
            tasks=[LifecycleTask(id="T-MEMORY", title="x", executor="claude-code")],
            current_task_id="T-MEMORY",
        )
        session.add(RunMemory(run_id=run_id, data=write_lifecycle_memory(memory)))
        await session.commit()

    supervisor = RunSupervisor()
    cancel_event = asyncio.Event()
    trace = NoopTraceStore()

    try:
        await run_deterministic_loop(
            run_id=run_id,
            agent=agent,
            trace=trace,
            supervisor=supervisor,
            registry=registry,
            session_factory=session_factory,
            cancel_event=cancel_event,
            dispatch_timeout_seconds=5,
        )

        assert captured[0].get("taskId") == "T-FROM-INTAKE", (
            "Run.intake.taskId must take precedence over memory.current_task_id "
            "(the runtime uses setdefault, not overwrite, when threading)"
        )
    finally:
        async with session_factory() as session:
            await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
            await session.execute(delete(Step).where(Step.run_id == run_id))
            await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
            await session.execute(delete(Run).where(Run.id == run_id))
            await session.commit()
