"""Service-layer tests for ``service.start_run`` (T-040).

Covers: happy path, unknown-agent guard, intake validation guard, supervisor
spawn wiring, and non-blocking timing.  The loop itself is silenced via a
``FakeSupervisor`` that records ``spawn`` calls without actually scheduling
the coroutine (we close it to avoid a "coroutine was never awaited" warning).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.llm import StubLLMProvider
from app.modules.ai.models import Run, RunMemory
from app.modules.ai.schemas import CreateRunRequest
from app.modules.ai.service import start_run
from app.modules.ai.trace import NoopTraceStore

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "agents"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    """Records ``spawn`` calls; closes the coroutine so it never runs."""

    def __init__(self) -> None:
        self.spawned: list[uuid.UUID] = []

    def spawn(
        self,
        run_id: uuid.UUID,
        coro_factory: Callable[[asyncio.Event], Coroutine[Any, Any, None]],
    ) -> None:
        self.spawned.append(run_id)
        coro = coro_factory(asyncio.Event())
        coro.close()  # do not actually schedule; we only want the wiring


class _FakeEngine:
    """Never called — start_run does not dispatch."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings that point ``agents_dir`` at the test fixtures directory."""
    settings = get_settings()
    return settings.model_copy(update={"agents_dir": _FIXTURES, "trace_dir": tmp_path})


async def _cleanup_run(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    async with factory() as session:
        await session.execute(
            RunMemory.__table__.delete().where(RunMemory.run_id == run_id)
        )
        await session.execute(Run.__table__.delete().where(Run.id == run_id))
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartRun:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_happy_path_writes_run_and_memory(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
    ) -> None:
        supervisor = _FakeSupervisor()
        request = CreateRunRequest(
            agent_ref="sample-linear", intake={"brief": "hello"}
        )

        summary = await start_run(
            request,
            settings=test_settings,
            supervisor=supervisor,  # type: ignore[arg-type]
            session_factory=session_factory,
            policy=StubLLMProvider([]),
            engine=_FakeEngine(),  # type: ignore[arg-type]
            trace=NoopTraceStore(),
        )

        try:
            assert summary.agent_ref == "sample-linear"
            assert summary.status.value == "pending"
            assert supervisor.spawned == [summary.id]

            async with session_factory() as session:
                run = await session.scalar(select(Run).where(Run.id == summary.id))
                memory = await session.scalar(
                    select(RunMemory).where(RunMemory.run_id == summary.id)
                )

            assert run is not None
            assert run.intake == {"brief": "hello"}
            assert run.trace_uri.startswith("file://")
            assert memory is not None
            assert memory.data == {}
        finally:
            await _cleanup_run(session_factory, summary.id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_agent_raises_not_found_and_writes_nothing(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
    ) -> None:
        supervisor = _FakeSupervisor()
        request = CreateRunRequest(agent_ref="does-not-exist@9.9", intake={})

        with pytest.raises(NotFoundError):
            await start_run(
                request,
                settings=test_settings,
                supervisor=supervisor,  # type: ignore[arg-type]
                session_factory=session_factory,
                policy=StubLLMProvider([]),
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
            )

        assert supervisor.spawned == []
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Run).where(Run.agent_ref == "does-not-exist@9.9")
                )
            ).scalars().all()
        assert list(rows) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_intake_validation_failure_raises_before_insert(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
        tmp_path: Path,
    ) -> None:
        """Write a fixture with a non-trivial intake schema, then break it."""
        strict_agent = tmp_path / "strict-agent@1.0.yaml"
        strict_agent.write_text(
            """
ref: strict-agent
version: "1.0"
description: requires a non-empty brief
nodes:
  - name: analyze_brief
    description: analyze
    input_schema:
      type: object
      properties:
        brief: {type: string}
      required: [brief]
terminal_nodes: [analyze_brief]
flow:
  entry_node: analyze_brief
intake_schema:
  type: object
  properties:
    brief: {type: string, minLength: 1}
  required: [brief]
""".strip()
        )
        settings = test_settings.model_copy(update={"agents_dir": tmp_path})

        supervisor = _FakeSupervisor()
        request = CreateRunRequest(agent_ref="strict-agent@1.0", intake={})

        with pytest.raises(ValidationError):
            await start_run(
                request,
                settings=settings,
                supervisor=supervisor,  # type: ignore[arg-type]
                session_factory=session_factory,
                policy=StubLLMProvider([]),
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
            )

        assert supervisor.spawned == []
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Run).where(Run.agent_ref == "strict-agent@1.0")
                )
            ).scalars().all()
        assert list(rows) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_quickly_does_not_block_on_loop(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
    ) -> None:
        supervisor = _FakeSupervisor()
        request = CreateRunRequest(
            agent_ref="sample-linear", intake={"brief": "hello"}
        )

        start = time.monotonic()
        summary = await start_run(
            request,
            settings=test_settings,
            supervisor=supervisor,  # type: ignore[arg-type]
            session_factory=session_factory,
            policy=StubLLMProvider([]),
            engine=_FakeEngine(),  # type: ignore[arg-type]
            trace=NoopTraceStore(),
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        try:
            # Generous CI bound — local runs are well under 50 ms.
            assert elapsed_ms < 500, f"start_run took {elapsed_ms:.0f} ms"
            assert summary.id is not None
        finally:
            await _cleanup_run(session_factory, summary.id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_trace_uri_uses_trace_dir_from_settings(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
    ) -> None:
        supervisor = _FakeSupervisor()
        request = CreateRunRequest(
            agent_ref="sample-linear", intake={"brief": "hi"}
        )
        summary = await start_run(
            request,
            settings=test_settings,
            supervisor=supervisor,  # type: ignore[arg-type]
            session_factory=session_factory,
            policy=StubLLMProvider([]),
            engine=_FakeEngine(),  # type: ignore[arg-type]
            trace=NoopTraceStore(),
        )

        try:
            async with session_factory() as session:
                run = await session.scalar(select(Run).where(Run.id == summary.id))
            assert run is not None
            assert str(test_settings.trace_dir) in run.trace_uri
            assert run.trace_uri.endswith(f"{summary.id}.jsonl")
        finally:
            await _cleanup_run(session_factory, summary.id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_starts_issue_distinct_run_ids(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        test_settings: Settings,
    ) -> None:
        supervisor = _FakeSupervisor()

        async def _one() -> uuid.UUID:
            req = CreateRunRequest(
                agent_ref="sample-linear", intake={"brief": "hi"}
            )
            summary = await start_run(
                req,
                settings=test_settings,
                supervisor=supervisor,  # type: ignore[arg-type]
                session_factory=session_factory,
                policy=StubLLMProvider([]),
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
            )
            return summary.id

        ids = await asyncio.gather(*[_one() for _ in range(5)])
        try:
            assert len(set(ids)) == 5
            assert set(supervisor.spawned) == set(ids)
        finally:
            for rid in ids:
                await _cleanup_run(session_factory, rid)
