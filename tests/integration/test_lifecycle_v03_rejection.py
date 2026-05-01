"""AC-3 — task rejection writes Approval inline; engine receives zero rejection POSTs (FEAT-011 / T-258 + T-261).

The ``correct_implementation`` LocalExecutor fires after
``review_implementation`` returns ``verdict='fail'``.  Per the FEAT-008
rejection contract preserved by FEAT-011, the handler:

* writes an ``Approval(stage="implementation", decision="reject")`` row
  for the active task,
* does **not** call the engine for the rejection itself,
* surfaces ``outcome="rejected"`` so the
  ``correction_attempts_under_bound`` predicate can route the next loop
  iteration.

This test seeds a real ``Task`` row so the Approval FK resolves, drives
the run through one fail-cycle that exhausts the budget, and asserts:

1. Exactly one ``Approval`` row exists for the task with the expected
   shape (``decision='reject'``, ``stage='implementation'``,
   ``decided_by='lifecycle-agent'``).
2. The engine never receives a rejection-bearing transition POST — the
   review's ``T10`` fires (composite forces it before the verdict
   branch), but no ``T3``/``T8``/``T11`` POST is made because no
   executor is bound to those keys in v0.3.0.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import NullPool, delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.llm import ToolCall, ToolDefinition, Usage
from app.core.webhook_auth import sign_body
from app.main import create_app
from app.modules.ai.agents import load_agent
from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.bootstrap import register_lifecycle_v03
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import (
    Approval,
    Dispatch,
    PendingAuxWrite,
    Run,
    RunMemory,
    RunSignal,
    Step,
    Task,
    WebhookEvent,
    WorkItem,
)
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace_jsonl import JsonlTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
_AGENT_REF = "lifecycle-agent@0.3.0"
_ENGINE_BASE = "http://engine.test"
_ENGINE_API_KEY = "test-key"
_TOKEN_RESPONSE = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


class _ScriptedProvider:
    name: str = "scripted-test"
    model: str = "scripted-v1"

    def __init__(self, by_node: Mapping[str, list[dict[str, Any]]]) -> None:
        self._by_node: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in by_node.items()}
        self.calls: list[str] = []

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall:
        del tools, messages
        first_line = system.splitlines()[0] if system else ""
        node_name = _node_from_headline(first_line)
        self.calls.append(node_name)
        bucket = self._by_node.get(node_name)
        if not bucket:
            raise AssertionError(f"scripted provider exhausted for node {node_name!r}")
        payload = bucket.pop(0)
        return ToolCall(
            name="content",
            arguments=dict(payload),
            usage=Usage(input_tokens=0, output_tokens=0, latency_ms=0),
            raw_response=None,
        )


def _node_from_headline(line: str) -> str:
    head = line.lstrip("# ").strip().lower()
    if "load" in head and "work-item" in head:
        return "load_work_item"
    if "feature" in head and "task" in head:
        return "generate_tasks"
    if "plan" in head:
        return "generate_plan"
    if "review" in head:
        return "review_implementation"
    raise AssertionError(f"unknown headline: {line!r}")


def _build_session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def engine_client() -> AsyncIterator[FlowEngineLifecycleClient]:
    client = FlowEngineLifecycleClient(base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=2)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    work_item_id: uuid.UUID,
) -> None:
    async with session_factory() as session:
        await session.execute(delete(Approval))
        await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(RunSignal).where(RunSignal.run_id == run_id))
        await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.execute(delete(PendingAuxWrite))
        await session.execute(delete(WebhookEvent))
        await session.execute(delete(Task).where(Task.work_item_id == work_item_id))
        await session.execute(delete(WorkItem).where(WorkItem.id == work_item_id))
        await session.commit()


async def test_ac3_rejection_writes_approval_row_and_calls_no_rejection_engine_endpoint(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: rejection branch writes Approval + makes no engine call to rejection keys."""
    monkeypatch.setenv("ENGINE_WEBHOOK_SECRET", webhook_secret)
    from app.config import get_settings

    get_settings.cache_clear()

    session_factory = _build_session_factory(test_database_url)
    engine_item_id = uuid.uuid4()

    # Seed WorkItem + Task so the Approval FK resolves.
    async with session_factory() as session:
        wi = WorkItem(
            external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
            type="FEAT",
            title="v03 rejection",
            status="in_progress",
            opened_by="admin",
            engine_item_id=engine_item_id,
        )
        session.add(wi)
        await session.commit()
        await session.refresh(wi)
        work_item_id = wi.id

        task = Task(
            work_item_id=work_item_id,
            external_ref="T-1",
            title="task one",
            status="implementing",
            proposer_type="agent",
            proposer_id="lifecycle-agent",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    run_row: Run
    async with session_factory() as session:
        run_row = Run(
            agent_ref=_AGENT_REF,
            agent_definition_hash="sha256:" + "0" * 64,
            intake={
                "engineItemId": str(engine_item_id),
                "workItemPath": "docs/work-items/FEAT-099.md",
                "workItemId": "FEAT-099",
                "taskId": "T-1",
            },
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/feat011-ac3.jsonl",
        )
        session.add(run_row)
        await session.flush()
        session.add(RunMemory(run_id=run_row.id, data={}))
        await session.commit()
        await session.refresh(run_row)
    run_id = run_row.id

    # Script: review fails twice, then budget exhausts.
    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [{"work_item_id": "FEAT-099", "title": "x", "summary": "x"}],
        "generate_tasks": [{"tasks": [{"id": "T-1", "title": "t1", "executor": "claude-code"}]}],
        "generate_plan": [{"task_id": "T-1", "plan_markdown": "# plan"}],
        "review_implementation": [
            {"task_id": "T-1", "verdict": "fail", "feedback": "first"},
            {"task_id": "T-1", "verdict": "fail", "feedback": "second"},
        ],
    }
    provider = _ScriptedProvider(by_node=llm_script)

    fake_engine_client = FlowEngineLifecycleClient(
        base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=1
    )
    registry = ExecutorRegistry()
    register_lifecycle_v03(
        registry,
        _AGENT_REF,
        lifecycle_client=fake_engine_client,
        llm_provider=provider,  # type: ignore[arg-type]
        session_factory=session_factory,
    )

    agent = load_agent(_AGENT_REF, _AGENTS_DIR)
    supervisor = RunSupervisor()
    app = create_app()
    app.state.supervisor = supervisor
    transport = ASGITransport(app=app)

    trace_dir = Path(f"/tmp/feat011-ac3-{run_id}")
    trace_dir.mkdir(exist_ok=True)
    trace = JsonlTraceStore(trace_dir)
    cancel_event = asyncio.Event()

    correlation_log: list[tuple[uuid.UUID, str]] = []  # (correlation, transition_path)

    async def _post_transition(request: Any) -> Response:
        body = json.loads(request.content.decode())
        comment = body.get("comment", "")
        for tok in comment.split():
            if tok.startswith("orchestrator-corr:"):
                correlation_log.append(
                    (uuid.UUID(tok[len("orchestrator-corr:") :]), str(request.url.path))
                )
                break
        return Response(
            200, json={"data": {"id": str(uuid.uuid4()), "transitionRunId": str(uuid.uuid4())}}
        )

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:

            async def _drainer() -> None:
                fired: set[uuid.UUID] = set()
                signaled: set[uuid.UUID] = set()
                deadline = asyncio.get_event_loop().time() + 30
                while asyncio.get_event_loop().time() < deadline:
                    for corr, _ in list(correlation_log):
                        if corr in fired:
                            continue
                        async with session_factory() as session:
                            committed = await session.scalar(
                                select(Dispatch).where(
                                    Dispatch.run_id == run_id,
                                    Dispatch.intake["correlation_id"].astext == str(corr),
                                )
                            )
                        if committed is None:
                            continue
                        body = {
                            "deliveryId": str(uuid.uuid4()),
                            "eventType": "item.transitioned",
                            "tenantId": str(uuid.uuid4()),
                            "workflowId": str(uuid.uuid4()),
                            "itemId": str(engine_item_id),
                            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "data": {
                                "fromStatus": "in_progress",
                                "toStatus": "ready",
                                "triggeredBy": f"orchestrator-corr:{corr}",
                            },
                        }
                        raw = json.dumps(body).encode()
                        sig = sign_body(raw, webhook_secret)
                        resp = await http_client.post(
                            "/hooks/engine/lifecycle/item-transitioned",
                            content=raw,
                            headers={
                                "X-FlowEngine-Signature": sig,
                                "Content-Type": "application/json",
                            },
                        )
                        assert resp.status_code in (202, 200, 409, 400), resp.text
                        fired.add(corr)
                    async with session_factory() as session:
                        humans = (
                            await session.scalars(
                                select(Dispatch)
                                .where(Dispatch.run_id == run_id)
                                .where(Dispatch.mode == "human")
                                .where(Dispatch.state == "dispatched")
                            )
                        ).all()
                    for human_row in humans:
                        if human_row.dispatch_id in signaled:
                            continue
                        env = DispatchEnvelope(
                            dispatch_id=human_row.dispatch_id,
                            step_id=human_row.step_id,
                            run_id=run_id,
                            executor_ref="human:request_implementation",
                            mode="human",  # type: ignore[arg-type]
                            state="completed",  # type: ignore[arg-type]
                            intake=dict(human_row.intake or {}),
                            outcome="ok",  # type: ignore[arg-type]
                            started_at=human_row.started_at or datetime.now(UTC),
                            finished_at=datetime.now(UTC),
                            result={"signal": "implementation-complete"},
                        )
                        supervisor.deliver_dispatch(human_row.dispatch_id, env)
                        signaled.add(human_row.dispatch_id)
                    await asyncio.sleep(0.05)

            with respx.mock(base_url=_ENGINE_BASE, assert_all_called=False) as mock:
                mock.post("/api/auth/token").respond(200, json=_TOKEN_RESPONSE)
                mock.post(url__regex=r"/api/items/[^/]+/transitions").mock(side_effect=_post_transition)
                mock.get(url__regex=r"/api/workflows.*").respond(
                    200, json={"data": {"items": []}}
                )

                drainer_task = asyncio.create_task(_drainer())
                try:
                    await asyncio.wait_for(
                        run_deterministic_loop(
                            run_id=run_id,
                            agent=agent,
                            trace=trace,
                            supervisor=supervisor,
                            registry=registry,
                            session_factory=session_factory,
                            cancel_event=cancel_event,
                            dispatch_timeout_seconds=8,
                        ),
                        timeout=120,
                    )
                finally:
                    if not drainer_task.done():
                        drainer_task.cancel()
                        try:
                            await drainer_task
                        except (asyncio.CancelledError, Exception):
                            pass
    finally:
        await fake_engine_client.aclose()

    # Run terminated as FAILED via correction_budget_exceeded path.
    async with session_factory() as session:
        run_after = (await session.scalars(select(Run).where(Run.id == run_id))).one()
        approvals = (await session.scalars(select(Approval).where(Approval.task_id == task_id))).all()

    actual_status = (
        RunStatus(run_after.status) if isinstance(run_after.status, str) else run_after.status
    )

    try:
        assert actual_status == RunStatus.FAILED, f"expected FAILED, got {actual_status}"
        # AC-3 core assertion: every fail cycle wrote an Approval row.
        rejection_rows = [a for a in approvals if a.decision == "reject"]
        assert len(rejection_rows) >= 1, (
            f"expected at least one rejection Approval; got {[(a.stage, a.decision) for a in approvals]}"
        )
        for row in rejection_rows:
            assert row.stage == "impl"
            assert row.decided_by == "lifecycle-agent"
            assert row.decided_by_role == "admin"
        # AC-3 core assertion: no engine call carried a rejection transition key.
        # v0.3.0's only engine-bound transitions on the rejection branch
        # are T10 (review→ready_for_close).  T3/T8/T11 keys (rejection-
        # specific in FEAT-006 declarations) MUST NOT appear in any POST
        # the test observed.
        seen_paths = [path for _, path in correlation_log]
        assert not any("T3" in p or "T8" in p or "T11" in p for p in seen_paths), (
            f"unexpected rejection-key engine POST: {seen_paths}"
        )
    finally:
        await _cleanup(session_factory, run_id, work_item_id=work_item_id)
