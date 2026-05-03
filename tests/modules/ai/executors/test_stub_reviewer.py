"""Unit tests for the stub-pass reviewer (IMP-003 / T-270).

The stub MUST write the canonical ``lifecycle.v1.reviewHistory`` shape
exactly as the LLM path does.  These tests pin that contract against
the shared ``_patch_review`` builder so a future drift in either side
is caught at the seam.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.bootstrap import _patch_review
from app.modules.ai.executors.stub_reviewer import (
    STUB_FEEDBACK,
    STUB_REVIEWER_REF,
    make_stub_pass_reviewer,
)
from app.modules.ai.models import Run, RunMemory
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleReview,
    LifecycleTask,
    read_lifecycle_memory,
    to_run_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


_SEEDED_RUN_IDS: list[uuid.UUID] = []


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    # Tests in this module insert Run + RunMemory rows directly (no
    # SAVEPOINT-wrapped session); clean them up so they don't pollute
    # downstream suites (e.g. test_service_list_get's count assertions).
    if _SEEDED_RUN_IDS:
        async with factory() as session:
            await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id.in_(_SEEDED_RUN_IDS)))
            await session.execute(Run.__table__.delete().where(Run.id.in_(_SEEDED_RUN_IDS)))
            await session.commit()
        _SEEDED_RUN_IDS.clear()


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    memory_data: dict[str, Any] | None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Run(
                id=run_id,
                agent_ref="lifecycle-agent@0.3.0",
                agent_definition_hash="sha256:" + "0" * 64,
                intake={},
                status="running",
                started_at=datetime.now(UTC),
                trace_uri="file:///tmp/t.jsonl",
            )
        )
        if memory_data is not None:
            session.add(RunMemory(run_id=run_id, data=memory_data))
        await session.commit()
    _SEEDED_RUN_IDS.append(run_id)
    return run_id


def _ctx(run_id: uuid.UUID, *, task_id: str) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=run_id,
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.3.0",
        node_name="review_implementation",
        intake={"taskId": task_id},
        extras={},
    )


class TestExecutorShape:
    async def test_ref_and_mode(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        executor = make_stub_pass_reviewer(
            session_factory=session_factory,
            patch_review_builder=_patch_review,
        )
        assert executor.name == STUB_REVIEWER_REF
        assert executor.mode == "local"


class TestCanonicalReviewHistoryEntry:
    async def test_writes_canonical_review_history_entry(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        memory_model = LifecycleMemory(
            tasks=[LifecycleTask(id="T-001", title="Task 1")],
        )
        run_id = await _seed_run(
            session_factory,
            memory_data={LIFECYCLE_MEMORY_NS: to_run_memory(memory_model)},
        )

        executor = make_stub_pass_reviewer(
            session_factory=session_factory,
            patch_review_builder=_patch_review,
        )
        envelope = await executor.dispatch(_ctx(run_id, task_id="T-001"))

        assert envelope.state.value == "completed"
        assert envelope.outcome is not None
        assert envelope.outcome.value == "ok"
        result = envelope.result or {}
        assert result["verdict"] == "pass"
        assert result["task_id"] == "T-001"
        assert result["feedback"] == STUB_FEEDBACK

        patch = result["__memory_patch"]
        assert LIFECYCLE_MEMORY_NS in patch
        merged = read_lifecycle_memory(patch)
        assert len(merged.review_history) == 1
        entry = merged.review_history[0]
        assert entry.task_id == "T-001"
        assert entry.verdict == "pass"
        assert entry.attempt == 1
        assert entry.feedback == STUB_FEEDBACK
        assert entry.written_to == "memory"

    async def test_appends_to_existing_review_history(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        prior = LifecycleMemory(
            tasks=[LifecycleTask(id="T-001", title="Task 1")],
            review_history=[
                LifecycleReview(
                    task_id="T-001",
                    attempt=1,
                    verdict="fail",
                    feedback="missing tests",
                    written_to="memory",
                )
            ],
        )
        run_id = await _seed_run(
            session_factory,
            memory_data={LIFECYCLE_MEMORY_NS: to_run_memory(prior)},
        )

        executor = make_stub_pass_reviewer(
            session_factory=session_factory,
            patch_review_builder=_patch_review,
        )
        envelope = await executor.dispatch(_ctx(run_id, task_id="T-001"))

        merged = read_lifecycle_memory((envelope.result or {})["__memory_patch"])
        assert [(r.attempt, r.verdict) for r in merged.review_history] == [
            (1, "fail"),
            (2, "pass"),
        ]
        # Prior entry's feedback survived intact.
        assert merged.review_history[0].feedback == "missing tests"

    async def test_preserves_other_namespace_keys(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        prior = LifecycleMemory(
            tasks=[
                LifecycleTask(id="T-001", title="Task 1"),
                LifecycleTask(id="T-002", title="Task 2"),
            ],
        )
        run_id = await _seed_run(
            session_factory,
            memory_data={LIFECYCLE_MEMORY_NS: to_run_memory(prior)},
        )

        executor = make_stub_pass_reviewer(
            session_factory=session_factory,
            patch_review_builder=_patch_review,
        )
        envelope = await executor.dispatch(_ctx(run_id, task_id="T-001"))

        merged = read_lifecycle_memory((envelope.result or {})["__memory_patch"])
        # Tasks slot from prior memory must survive the patch.
        assert {t.id for t in merged.tasks} == {"T-001", "T-002"}

    async def test_empty_memory_still_writes_canonical_shape(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = await _seed_run(session_factory, memory_data=None)

        executor = make_stub_pass_reviewer(
            session_factory=session_factory,
            patch_review_builder=_patch_review,
        )
        envelope = await executor.dispatch(_ctx(run_id, task_id="T-001"))

        merged = read_lifecycle_memory((envelope.result or {})["__memory_patch"])
        assert len(merged.review_history) == 1
        assert merged.review_history[0].verdict == "pass"
        assert merged.review_history[0].attempt == 1
