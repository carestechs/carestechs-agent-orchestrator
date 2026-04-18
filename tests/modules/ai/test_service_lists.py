"""Service-layer tests for ``list_steps`` + ``list_policy_calls`` (T-043)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.ai.enums import RunStatus, StepStatus
from app.modules.ai.models import PolicyCall, Run, RunMemory, Step
from app.modules.ai.service import list_policy_calls, list_steps


async def _seed_run_with_children(
    db: AsyncSession, *, n: int
) -> tuple[Run, list[Step], list[PolicyCall]]:
    run = Run(
        agent_ref="a",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    db.add(RunMemory(run_id=run.id, data={}))

    steps: list[Step] = []
    calls: list[PolicyCall] = []
    for i in range(1, n + 1):
        step = Step(
            run_id=run.id,
            step_number=i,
            node_name=f"node-{i}",
            node_inputs={"i": i},
            status=StepStatus.PENDING,
        )
        db.add(step)
        await db.flush()
        steps.append(step)

        call = PolicyCall(
            run_id=run.id,
            step_id=step.id,
            prompt_context={"step": i},
            available_tools=[{"name": "x", "description": "y", "parameters": {}}],
            provider="stub",
            model="stub-v1",
            selected_tool=f"node-{i}",
            tool_arguments={"i": i},
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            raw_response=None,
        )
        db.add(call)
        calls.append(call)

    await db.commit()
    return run, steps, calls


class TestListSteps:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_pagination_and_ascending_order(
        self, db_session: AsyncSession
    ) -> None:
        run, _, _ = await _seed_run_with_children(db_session, n=5)

        collected: list[int] = []
        for page in (1, 2, 3):
            items, total = await list_steps(run.id, db_session, page=page, page_size=2)
            assert total == 5
            collected.extend(i.step_number for i in items)
            if page in (1, 2):
                assert len(items) == 2
            else:
                assert len(items) == 1
        assert collected == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_run_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(NotFoundError):
            await list_steps(uuid.uuid4(), db_session)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_run_returns_empty_list(
        self, db_session: AsyncSession
    ) -> None:
        run, _, _ = await _seed_run_with_children(db_session, n=0)
        items, total = await list_steps(run.id, db_session)
        assert items == []
        assert total == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_step_dto_preserves_node_inputs(
        self, db_session: AsyncSession
    ) -> None:
        run, _, _ = await _seed_run_with_children(db_session, n=1)
        items, _ = await list_steps(run.id, db_session)
        assert items[0].node_inputs == {"i": 1}


class TestListPolicyCalls:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_pagination_and_ascending_order(
        self, db_session: AsyncSession
    ) -> None:
        run, _, calls = await _seed_run_with_children(db_session, n=5)
        expected_ids = [c.id for c in calls]

        collected: list[uuid.UUID] = []
        for page in (1, 2, 3):
            items, total = await list_policy_calls(
                run.id, db_session, page=page, page_size=2
            )
            assert total == 5
            collected.extend(i.id for i in items)
        assert collected == expected_ids

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_run_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(NotFoundError):
            await list_policy_calls(uuid.uuid4(), db_session)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dto_preserves_tool_arguments(
        self, db_session: AsyncSession
    ) -> None:
        run, _, _ = await _seed_run_with_children(db_session, n=1)
        items, _ = await list_policy_calls(run.id, db_session)
        assert items[0].tool_arguments == {"i": 1}
        assert items[0].selected_tool == "node-1"
