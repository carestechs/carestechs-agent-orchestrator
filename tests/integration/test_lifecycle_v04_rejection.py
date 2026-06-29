"""End-to-end rejection tests for ``lifecycle-agent@0.4.0-manual`` (IMP-006).

Three scenarios, one per rejectable checkpoint:
  1. ``confirm_brief``  — operator rejects the brief once, then approves.
  2. ``confirm_tasks``  — operator rejects the task list once, then approves.
  3. ``confirm_plan``   — operator rejects the plan once, then approves.

Each test verifies:
  - The run completes (``RunStatus.COMPLETED``).
  - The ``rejections`` memory sidecar contains the feedback at the right key.
  - The producing node's LLM executor was called twice (once per attempt).
  - Backward-compat: existing happy-path test in test_lifecycle_v04_manual.py
    is unchanged — no verdict field still approves.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
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
from app.modules.ai.executors.bootstrap import register_lifecycle_v04_manual
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
_AGENT_REF = "lifecycle-agent@0.4.0-manual"
_ENGINE_BASE = "http://engine.test"
_ENGINE_API_KEY = "test-key"
_TOKEN_RESPONSE = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


# ---------------------------------------------------------------------------
# Scripted LLM provider
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    name: str = "scripted-test"
    model: str = "scripted-v1"

    def __init__(self, by_node: Mapping[str, list[dict[str, Any]]]) -> None:
        self._by_node: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in by_node.items()
        }
        self.calls: list[str] = []

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
        tool_choice: Mapping[str, Any] | None = None,
    ) -> ToolCall:
        del tools, tool_choice
        first_line = system.splitlines()[0] if system else ""
        node_name = _node_from_system_prompt(first_line)
        self.calls.append(node_name)
        bucket = self._by_node.get(node_name)
        if not bucket:
            raise AssertionError(
                f"scripted provider exhausted for node {node_name!r}; "
                f"calls so far: {self.calls}"
            )
        payload = bucket.pop(0)
        return ToolCall(
            name="content",
            arguments=dict(payload),
            usage=Usage(input_tokens=0, output_tokens=0, latency_ms=0),
            raw_response=None,
        )


def _node_from_system_prompt(first_line: str) -> str:
    headline = first_line.lstrip("# ").strip().lower()
    if "load" in headline and "work-item" in headline:
        return "load_work_item"
    if "feature" in headline and "task" in headline:
        return "generate_tasks"
    if "plan" in headline:
        return "generate_plan"
    raise AssertionError(f"scripted provider could not map headline: {first_line!r}")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _build_session_factory(
    test_database_url: str,
) -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(test_database_url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    intake: dict[str, Any],
) -> Run:
    async with session_factory() as session:
        run = Run(
            agent_ref=_AGENT_REF,
            agent_definition_hash="sha256:" + "0" * 64,
            intake=intake,
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/imp006-rejection-e2e.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data={}))
        await session.commit()
        await session.refresh(run)
        return run


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    work_item_id: uuid.UUID | None = None,
) -> None:
    async with session_factory() as session:
        await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(RunSignal).where(RunSignal.run_id == run_id))
        await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.execute(delete(PendingAuxWrite))
        await session.execute(delete(WebhookEvent))
        if work_item_id is not None:
            task_ids = (
                await session.scalars(
                    select(Task.id).where(Task.work_item_id == work_item_id)
                )
            ).all()
            if task_ids:
                await session.execute(
                    delete(Approval).where(Approval.task_id.in_(task_ids))
                )
                await session.execute(
                    delete(Task).where(Task.work_item_id == work_item_id)
                )
            await session.execute(delete(WorkItem).where(WorkItem.id == work_item_id))
        await session.commit()


@pytest_asyncio.fixture(loop_scope="function")
async def engine_client() -> AsyncIterator[FlowEngineLifecycleClient]:
    client = FlowEngineLifecycleClient(
        base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=2
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


# ---------------------------------------------------------------------------
# Generic rejection-scenario driver
# ---------------------------------------------------------------------------

# A signal resolver receives (executor_ref, task_id, call_count_for_this_ref)
# and returns the payload dict to deliver.
SignalResolver = Callable[[str, str, int], dict[str, Any]]


async def _drive_rejection_scenario(
    *,
    test_database_url: str,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
    llm_script: Mapping[str, list[dict[str, Any]]],
    signal_resolver: SignalResolver,
) -> tuple[Run, async_sessionmaker[AsyncSession], uuid.UUID | None, list[str]]:
    """Drive a full manual lifecycle run with a custom per-ref signal resolver.

    Returns (final_run, session_factory, work_item_id, llm_call_list).
    """
    monkeypatch.setenv("ENGINE_WEBHOOK_SECRET", webhook_secret)
    from app.config import get_settings

    get_settings.cache_clear()

    session_factory = _build_session_factory(test_database_url)

    engine_item_id = uuid.uuid4()
    work_item_id: uuid.UUID | None = None
    work_item_workflow_id = uuid.uuid4()
    task_workflow_id = uuid.uuid4()

    run = await _seed_run(
        session_factory,
        intake={
            "workItemPath": "docs/work-items/FEAT-099.md",
            "workItemId": "FEAT-099",
            "codeSource": {
                "repo": "carestechs/orchestrator-test-fixture",
                "baseBranch": "main",
            },
        },
    )

    registry = ExecutorRegistry()
    provider = _ScriptedProvider(by_node=llm_script)
    fake_engine_client = FlowEngineLifecycleClient(
        base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=1
    )
    register_lifecycle_v04_manual(
        registry,
        _AGENT_REF,
        lifecycle_client=fake_engine_client,
        llm_provider=provider,  # type: ignore[arg-type]
        session_factory=session_factory,
        workflow_ids={
            "work_item_workflow": work_item_workflow_id,
            "task_workflow": task_workflow_id,
        },
    )

    agent = load_agent(_AGENT_REF, _AGENTS_DIR)
    supervisor = RunSupervisor()
    app = create_app()
    app.state.supervisor = supervisor
    app.state.executor_registry = registry
    transport = ASGITransport(app=app)

    trace_dir = Path(f"/tmp/imp006-rejection-e2e-{run.id}")
    trace_dir.mkdir(exist_ok=True)
    trace = JsonlTraceStore(trace_dir)
    cancel_event = asyncio.Event()

    correlation_log: list[uuid.UUID] = []
    counters: dict[str, int] = {"work_item_creates": 0, "task_creates": 0}

    async def _post_transition(request: Any) -> Response:
        body = json.loads(request.content.decode())
        corr_raw = body.get("correlationId")
        if corr_raw:
            correlation_log.append(uuid.UUID(corr_raw))
        return Response(
            200,
            json={"data": {"id": str(uuid.uuid4()), "transitionRunId": str(uuid.uuid4())}},
        )

    async def _post_create_item(request: Any) -> Response:
        path = str(request.url.path)
        wf_id = path.split("/")[-2]
        if wf_id == str(work_item_workflow_id):
            counters["work_item_creates"] += 1
            return Response(201, json={"data": {"id": str(engine_item_id)}})
        counters["task_creates"] += 1
        return Response(201, json={"data": {"id": str(uuid.uuid4())}})

    # Per-ref call count so the resolver can behave differently on retries.
    signal_counts: dict[str, int] = {}

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:

            async def _drainer() -> None:
                webhook_fired: set[uuid.UUID] = set()
                signaled_dispatches: set[uuid.UUID] = set()
                deadline = asyncio.get_event_loop().time() + 60
                while asyncio.get_event_loop().time() < deadline:
                    # 1. Engine webhooks for committed correlation ids.
                    for corr in list(correlation_log):
                        if corr in webhook_fired:
                            continue
                        async with session_factory() as session:
                            committed = await session.scalar(
                                select(Dispatch).where(
                                    Dispatch.run_id == run.id,
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
                                "triggeredBy": "engine",
                                "correlationId": str(corr),
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
                        webhook_fired.add(corr)

                    # 2. Checkpoint signals for parked human dispatches.
                    async with session_factory() as session:
                        humans = (
                            await session.scalars(
                                select(Dispatch)
                                .where(Dispatch.run_id == run.id)
                                .where(Dispatch.mode == "human")
                                .where(Dispatch.state == "dispatched")
                            )
                        ).all()
                    for human_row in humans:
                        if human_row.dispatch_id in signaled_dispatches:
                            continue
                        ref = human_row.executor_ref
                        task_id = (human_row.intake or {}).get("taskId") or ""
                        call_n = signal_counts.get(ref, 0)
                        signal_counts[ref] = call_n + 1

                        payload = signal_resolver(ref, task_id, call_n)

                        # Derive signal name from ref.
                        signal_name = _signal_name_for_ref(ref)

                        envelope_result: dict[str, Any] = {
                            "signal_name": signal_name,
                            "task_id": task_id,
                            "payload": payload,
                        }
                        for k, v in payload.items():
                            if k not in envelope_result:
                                envelope_result[k] = v

                        node_name = (human_row.intake or {}).get("nodeName")
                        if node_name:
                            binding = registry.resolve(_AGENT_REF, node_name)
                            builder = getattr(
                                binding.executor, "memory_patch_builder", None
                            )
                            if builder is not None:
                                async with session_factory() as session:
                                    mem_row = await session.scalar(
                                        select(RunMemory).where(
                                            RunMemory.run_id == run.id
                                        )
                                    )
                                current_memory: dict[str, Any] = (
                                    mem_row.data if mem_row is not None else {}
                                ) or {}
                                try:
                                    patch = builder(payload, current_memory)
                                    envelope_result["__memory_patch"] = patch
                                except Exception as exc:
                                    raise AssertionError(
                                        f"memory_patch_builder for {ref!r} raised on "
                                        f"payload {payload!r}: {exc}"
                                    ) from exc

                        async with session_factory() as session:
                            row = await session.get(Dispatch, human_row.dispatch_id)
                            if row is not None and row.state == "dispatched":
                                row.mark_completed(
                                    at=datetime.now(UTC), result=envelope_result
                                )
                                await session.commit()
                        envelope = DispatchEnvelope(
                            dispatch_id=human_row.dispatch_id,
                            step_id=human_row.step_id,
                            run_id=run.id,
                            executor_ref=ref,
                            mode="human",  # type: ignore[arg-type]
                            state="completed",  # type: ignore[arg-type]
                            intake=dict(human_row.intake or {}),
                            outcome="ok",  # type: ignore[arg-type]
                            started_at=human_row.started_at or datetime.now(UTC),
                            finished_at=datetime.now(UTC),
                            result=envelope_result,
                        )
                        supervisor.deliver_dispatch(human_row.dispatch_id, envelope)
                        signaled_dispatches.add(human_row.dispatch_id)

                    await asyncio.sleep(0.05)

            with respx.mock(base_url=_ENGINE_BASE, assert_all_called=False) as mock:
                mock.post("/api/auth/token").respond(200, json=_TOKEN_RESPONSE)
                mock.post(url__regex=r"/api/workflows/[^/]+/items").mock(
                    side_effect=_post_create_item
                )
                mock.post(url__regex=r"/api/items/[^/]+/transitions").mock(
                    side_effect=_post_transition
                )
                mock.get(url__regex=r"/api/workflows.*").respond(
                    200, json={"data": {"items": []}}
                )

                drainer_task = asyncio.create_task(_drainer())
                try:
                    await asyncio.wait_for(
                        run_deterministic_loop(
                            run_id=run.id,
                            agent=agent,
                            trace=trace,
                            supervisor=supervisor,
                            registry=registry,
                            session_factory=session_factory,
                            cancel_event=cancel_event,
                            dispatch_timeout_seconds=10,
                        ),
                        timeout=120,
                    )
                except TimeoutError:
                    async with session_factory() as session:
                        dispatches = (
                            await session.scalars(
                                select(Dispatch).where(Dispatch.run_id == run.id)
                            )
                        ).all()
                    print(
                        "[debug] dispatches:",
                        [(d.executor_ref, d.mode, d.state) for d in dispatches],
                    )
                    print("[debug] llm calls:", provider.calls)
                    raise
                finally:
                    if not drainer_task.done():
                        drainer_task.cancel()
                        try:
                            await drainer_task
                        except (asyncio.CancelledError, Exception):
                            pass
    finally:
        await fake_engine_client.aclose()

    async with session_factory() as session:
        wi_row = await session.scalar(
            select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
        )
        if wi_row is not None:
            work_item_id = wi_row.id

    return run, session_factory, work_item_id, provider.calls


def _signal_name_for_ref(ref: str) -> str:
    mapping = {
        "human:confirm_brief": "brief-confirmed",
        "human:confirm_tasks": "tasks-confirmed",
        "human:confirm_assignment": "assignment-confirmed",
        "human:confirm_plan": "plan-confirmed",
        "human:request_implementation": "implementation-complete",
        "human:review_implementation": "review-completed",
    }
    return mapping.get(ref, "unknown-signal")


# ---------------------------------------------------------------------------
# Default approve payloads for non-rejected checkpoints
# (1-task scenario: T-1 only)
# ---------------------------------------------------------------------------


def _default_resolver(ref: str, task_id: str, _call_n: int) -> dict[str, Any]:
    if ref == "human:confirm_brief":
        return {}
    if ref == "human:confirm_tasks":
        # _TaskInput only accepts id, title, summary, description, complexity —
        # no executor field (extra="forbid" would reject it).
        return {"tasks": [{"id": "T-1", "title": "Single task"}]}
    if ref == "human:confirm_assignment":
        return {"assignee": f"operator-{task_id}"}
    if ref == "human:confirm_plan":
        return {}
    if ref == "human:request_implementation":
        return {}
    if ref == "human:review_implementation":
        return {"verdict": "pass"}
    return {}


# 1-task LLM script used by all three rejection scenarios.
_ONE_TASK_GENERATE_TASKS = {
    "tasks": [
        {
            "id": "T-1",
            "title": "Single task",
            "executor": "claude-code",
            "description": "Rejection test task",
            "acceptance_criteria": ["Done"],
            "complexity": "small",
        }
    ]
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reject_brief_then_approve(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator rejects brief once → feedback lands in memory → retry approves → run completes."""
    rejection_feedback = "Summary is too vague — needs explicit scope and goals."

    def resolver(ref: str, task_id: str, call_n: int) -> dict[str, Any]:
        if ref == "human:confirm_brief" and call_n == 0:
            return {"verdict": "reject", "feedback": rejection_feedback}
        return _default_resolver(ref, task_id, call_n)

    llm_script: dict[str, list[dict[str, Any]]] = {
        # load_work_item runs twice: once before rejection, once after.
        "load_work_item": [
            {"work_item_id": "FEAT-099", "title": "v04 rejection brief", "summary": "initial"},
            {"work_item_id": "FEAT-099", "title": "v04 rejection brief", "summary": "revised"},
        ],
        "generate_tasks": [_ONE_TASK_GENERATE_TASKS],
        "generate_plan": [{"task_id": "T-1", "plan_markdown": "# plan T-1"}],
    }

    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id, llm_calls = await _drive_rejection_scenario(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
            signal_resolver=resolver,
        )

        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run.id)
            )
        actual_status = (
            RunStatus(run_row.status) if isinstance(run_row.status, str) else run_row.status
        )
        assert actual_status == RunStatus.COMPLETED, (
            f"expected COMPLETED, got {actual_status}; final_state={run_row.final_state}"
        )

        # Rejection feedback is persisted in the rejections sidecar.
        mem_data: dict[str, Any] = (mem_row.data if mem_row else {}) or {}
        rejections = mem_data.get("rejections") or {}
        brief_rejection = rejections.get("confirm_brief") or {}
        assert brief_rejection.get("feedback") == rejection_feedback, (
            f"expected rejection feedback in memory; got {rejections!r}"
        )
        assert brief_rejection.get("attempt") == 1

        # load_work_item was called twice — once per attempt.
        assert llm_calls.count("load_work_item") == 2, (
            f"expected 2 load_work_item calls; got {llm_calls}"
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)


async def test_reject_tasks_then_approve(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator rejects task list once → feedback lands in memory → retry approves → run completes."""
    rejection_feedback = "Tasks are too granular — merge into fewer, higher-level tasks."

    def resolver(ref: str, task_id: str, call_n: int) -> dict[str, Any]:
        if ref == "human:confirm_tasks" and call_n == 0:
            return {"verdict": "reject", "feedback": rejection_feedback}
        # After rejection, approve with the single task so the flow can continue.
        if ref == "human:confirm_tasks":
            return {"tasks": [{"id": "T-1", "title": "Single task"}]}
        return _default_resolver(ref, task_id, call_n)

    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [
            {"work_item_id": "FEAT-099", "title": "v04 rejection tasks", "summary": "test"}
        ],
        # generate_tasks runs twice: before rejection + after.
        "generate_tasks": [_ONE_TASK_GENERATE_TASKS, _ONE_TASK_GENERATE_TASKS],
        "generate_plan": [{"task_id": "T-1", "plan_markdown": "# plan T-1"}],
    }

    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id, llm_calls = await _drive_rejection_scenario(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
            signal_resolver=resolver,
        )

        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run.id)
            )
        actual_status = (
            RunStatus(run_row.status) if isinstance(run_row.status, str) else run_row.status
        )
        assert actual_status == RunStatus.COMPLETED, (
            f"expected COMPLETED, got {actual_status}; final_state={run_row.final_state}"
        )

        mem_data: dict[str, Any] = (mem_row.data if mem_row else {}) or {}
        rejections = mem_data.get("rejections") or {}
        tasks_rejection = rejections.get("confirm_tasks") or {}
        assert tasks_rejection.get("feedback") == rejection_feedback, (
            f"expected rejection feedback in memory; got {rejections!r}"
        )
        assert tasks_rejection.get("attempt") == 1

        # generate_tasks was called twice.
        assert llm_calls.count("generate_tasks") == 2, (
            f"expected 2 generate_tasks calls; got {llm_calls}"
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)


async def test_reject_plan_then_approve(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator rejects plan once → feedback lands in memory → retry approves → run completes."""
    rejection_feedback = "Plan is missing test steps and rollback strategy."

    def resolver(ref: str, task_id: str, call_n: int) -> dict[str, Any]:
        if ref == "human:confirm_plan" and call_n == 0:
            return {"verdict": "reject", "feedback": rejection_feedback}
        return _default_resolver(ref, task_id, call_n)

    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [
            {"work_item_id": "FEAT-099", "title": "v04 rejection plan", "summary": "test"}
        ],
        "generate_tasks": [_ONE_TASK_GENERATE_TASKS],
        # generate_plan runs twice for the same task: before rejection + after.
        "generate_plan": [
            {"task_id": "T-1", "plan_markdown": "# plan T-1 initial"},
            {"task_id": "T-1", "plan_markdown": "# plan T-1 revised with test steps"},
        ],
    }

    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id, llm_calls = await _drive_rejection_scenario(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
            signal_resolver=resolver,
        )

        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run.id)
            )
        actual_status = (
            RunStatus(run_row.status) if isinstance(run_row.status, str) else run_row.status
        )
        assert actual_status == RunStatus.COMPLETED, (
            f"expected COMPLETED, got {actual_status}; final_state={run_row.final_state}"
        )

        mem_data: dict[str, Any] = (mem_row.data if mem_row else {}) or {}
        rejections = mem_data.get("rejections") or {}
        plan_rejection = rejections.get("confirm_plan") or {}
        assert plan_rejection.get("feedback") == rejection_feedback, (
            f"expected rejection feedback in memory; got {rejections!r}"
        )
        assert plan_rejection.get("attempt") == 1

        # generate_plan was called twice for T-1.
        assert llm_calls.count("generate_plan") == 2, (
            f"expected 2 generate_plan calls; got {llm_calls}"
        )

        # The plan in memory reflects the second (revised) response.
        plans = mem_data.get("plans") or {}
        t1_plan = plans.get("T-1") or {}
        assert "revised" in str(t1_plan.get("plan_markdown") or ""), (
            f"expected revised plan in memory; got {plans!r}"
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)
