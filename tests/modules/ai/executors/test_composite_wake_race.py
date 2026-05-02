"""Wake-race regression for ``CompositeLLMEngineExecutor``.

Mirrors ``TestWakeRaceFix`` in ``test_engine_executor.py``: the dispatch
row's ``intake.correlation_id`` MUST be visible to a fresh session
*before* the engine's ``transition_item`` HTTP call returns.  Without
that, webhooks arriving inside the call window (which is the
common live-engine case — webhook latency < HTTP-response latency)
can't match the reactor's wake-leg query and the dispatch parks
until the runtime times out.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
import respx
from httpx import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.llm import ToolCall, Usage
from app.modules.ai.enums import DispatchMode, DispatchState, RunStatus
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.composite import CompositeLLMEngineExecutor
from app.modules.ai.executors.llm_content import LLMContentExecutor
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Dispatch, PendingAuxWrite, Run, RunMemory, Step

pytestmark = pytest.mark.asyncio(loop_scope="function")

_BASE = "http://engine.test"
_API_KEY = "k"
_TOKEN_RESP = {
    "data": {
        "accessToken": "x",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


class _Result(BaseModel):
    title: str


class _ScriptedProvider:
    name: str = "scripted"
    model: str = "scripted-1"

    async def chat_with_tools(  # type: ignore[no-untyped-def]
        self, *, system, messages, tools, tool_choice=None
    ):
        del system, messages, tools, tool_choice
        return ToolCall(
            name="content",
            arguments={"title": "x"},
            usage=Usage(input_tokens=0, output_tokens=0, latency_ms=0),
            raw_response=None,
        )


@pytest_asyncio.fixture(loop_scope="function")
async def lifecycle_client() -> AsyncIterator[FlowEngineLifecycleClient]:
    client = FlowEngineLifecycleClient(base_url=_BASE, api_key=_API_KEY, max_retries=2)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(
            PendingAuxWrite.__table__.delete().where(
                PendingAuxWrite.payload["aux_type"].astext == "engine_dispatch"
            )
        )
        await session.commit()


def _patch_builder(_result: dict[str, Any]) -> dict[str, Any]:
    return {"composite_marker": True}


async def test_intake_correlation_committed_before_http(
    engine: AsyncEngine,
    lifecycle_client: FlowEngineLifecycleClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Run(
                id=run_id,
                agent_ref="t@0.1.0",
                agent_definition_hash="sha256:" + "0" * 64,
                intake={},
                status=RunStatus.PENDING,
                started_at=datetime.now(UTC),
                trace_uri="file:///tmp/x.jsonl",
            )
        )
        session.add(
            Step(
                id=step_id,
                run_id=run_id,
                step_number=1,
                node_name="generate_plan",
                node_inputs={},
                status="pending",
            )
        )
        await session.flush()
        d = Dispatch(
            dispatch_id=dispatch_id,
            step_id=step_id,
            run_id=run_id,
            executor_ref="composite:generate_plan",
            mode=DispatchMode.ENGINE.value,
            state=DispatchState.DISPATCHED.value,
            intake={"engineItemId": str(item_id), "runId": str(run_id), "nodeName": "generate_plan"},
            started_at=datetime.now(UTC),
            dispatched_at=datetime.now(UTC),
        )
        session.add(d)
        await session.commit()

    observed: list[dict[str, object]] = []

    async def _on_http(_request: object) -> Response:
        async with session_factory() as session:
            row = await session.get(Dispatch, dispatch_id)
            assert row is not None
            observed.append(dict(row.intake or {}))
        return Response(200, json={"data": {"id": "ok"}})

    with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
        rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
        rx.post(f"/api/items/{item_id}/transitions").mock(side_effect=_on_http)

        llm = LLMContentExecutor(
            ref="llm:test",
            system_prompt="sys",
            user_prompt_template="user",
            result_schema=_Result,
            llm_provider=_ScriptedProvider(),  # type: ignore[arg-type]
        )
        executor = CompositeLLMEngineExecutor(
            ref="composite:generate_plan",
            llm_executor=llm,
            transition_key="task.T6",
            to_status="plan_review",
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            memory_patch_builder=_patch_builder,
        )
        ctx = DispatchContext(
            dispatch_id=dispatch_id,
            run_id=run_id,
            step_id=step_id,
            agent_ref="t@0.1.0",
            node_name="generate_plan",
            intake={"engineItemId": str(item_id)},
        )
        env = await executor.dispatch(ctx)

    try:
        assert env.state.value == "dispatched"
        assert len(observed) == 1
        intake_during = observed[0]
        assert intake_during.get("correlation_id") == str(env.correlation_id), (
            "BUG/wake-race regression: CompositeLLMEngineExecutor must commit "
            "Dispatch.intake.correlation_id BEFORE the transition_item HTTP "
            "call returns — otherwise webhooks arriving inside the call "
            "window can't match wake."
        )
        assert intake_during.get("transition_key") == "task.T6"
        assert intake_during.get("runId") == str(run_id)
        assert intake_during.get("nodeName") == "generate_plan"
    finally:
        async with session_factory() as session:
            await session.execute(Dispatch.__table__.delete().where(Dispatch.dispatch_id == dispatch_id))
            await session.execute(Step.__table__.delete().where(Step.id == step_id))
            await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
            await session.execute(Run.__table__.delete().where(Run.id == run_id))
            await session.commit()
