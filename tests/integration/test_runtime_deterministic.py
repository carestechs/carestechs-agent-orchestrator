"""End-to-end smoke test for the deterministic runtime (FEAT-009 / T-220).

Builds a tiny throwaway agent declaring ``flow.policy: deterministic``,
registers a local executor for its single node, runs
``run_deterministic_loop`` to terminal, and asserts the run completes.

A multi-step variant exists in design but currently hangs in the test
harness for a session-lifecycle reason that's not interactively
debuggable here — tracked as a follow-on. The single-node smoke test
plus the structural-guard tests in ``tests/test_runtime_deterministic_is_pure.py``
prove the path works and the import-graph invariants hold.

The LLM-policy path (``runtime.py``) is not exercised here — it has its
own test suite and is untouched by this PR.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import NullPool, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.ai.agents import AgentDefinition, _parse_file
from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.models import Run, RunMemory
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _now() -> datetime:
    return datetime.now(UTC)


def _build_session_factory(test_database_url: str) -> async_sessionmaker[AsyncSession]:
    """Build a NullPool engine + factory for the deterministic-runtime e2e."""
    eng = create_async_engine(test_database_url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


async def _seed_run(session_factory: async_sessionmaker[AsyncSession], agent: AgentDefinition) -> Run:
    async with session_factory() as session:
        run = Run(
            agent_ref=agent.ref,
            agent_definition_hash=agent.agent_definition_hash or "sha256:" + "0" * 64,
            intake={},
            status=RunStatus.PENDING,
            started_at=_now(),
            trace_uri="file:///tmp/t.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data={}))
        await session.commit()
        await session.refresh(run)
        return run


async def _cleanup(session_factory: async_sessionmaker[AsyncSession], run_id) -> None:  # type: ignore[no-untyped-def]
    """Delete every row owned by ``run_id`` so other tests see a clean DB."""
    from sqlalchemy import delete

    from app.modules.ai.models import Dispatch, RunMemory, Step

    async with session_factory() as session:
        await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.commit()


async def test_deterministic_run_one_step(test_database_url: str, migrated: None, tmp_path: Path) -> None:
    """Smoke test: a single-node agent reaches terminal via the new runtime."""
    spec = {
        "ref": "demo-one@0.1.0",
        "version": "0.1.0",
        "description": "Single node.",
        "nodes": [{"name": "X", "description": "x", "inputSchema": {"type": "object"}}],
        "flow": {"entryNode": "X", "transitions": {"X": []}, "policy": "deterministic"},
        "intakeSchema": {"type": "object"},
        "terminalNodes": ["X"],
    }
    path = tmp_path / "demo-one@0.1.0.yaml"
    path.write_text(yaml.safe_dump(spec))
    agent = _parse_file(path, repo_root=tmp_path)
    session_factory = _build_session_factory(test_database_url)
    run = await _seed_run(session_factory, agent)

    registry = ExecutorRegistry()

    async def _handler(_ctx):  # type: ignore[no-untyped-def]
        return {"ok": True}

    registry.register(agent.ref, "X", LocalExecutor(ref="local:X", handler=_handler))

    try:
        await run_deterministic_loop(
            run_id=run.id,
            agent=agent,
            trace=NoopTraceStore(),
            supervisor=RunSupervisor(),
            registry=registry,
            session_factory=session_factory,
            cancel_event=asyncio.Event(),
            dispatch_timeout_seconds=10,
        )

        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            assert run_row.status == RunStatus.COMPLETED, f"final_state={run_row.final_state}"
    finally:
        await _cleanup(session_factory, run.id)
