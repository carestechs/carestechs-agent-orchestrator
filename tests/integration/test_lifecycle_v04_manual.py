"""End-to-end test for ``lifecycle-agent@0.4.0-manual`` (FEAT-015 / T-302).

Drives the manual variant through the deterministic runtime with:
- A scripted LLM provider for the three LLM-content nodes
  (``load_work_item``, ``generate_tasks``, ``generate_plan``).
- A respx-stubbed engine that counts ``T1.create_item`` calls so the
  load-bearing "edited tasks reach the engine" assertion can fire.
- A drainer task that, for every human-mode dispatch parked at one of
  the four checkpoints, delivers the matching operator signal.

Acceptance verification (mapped to T-302 AC):
- AC-2: every status transition flips through paused → running across
  the four checkpoint kinds.
- AC-3 (LOAD-BEARING): operator replaces 3 LLM-generated tasks with 2
  via ``tasks-confirmed``; ``propose_tasks`` fans out to the engine for
  exactly 2 task creates (NOT 3).
- AC-4: ``LifecycleMemory.reviewHistory`` ends with one entry per task,
  shape-parity with the LLM reviewer's writer (delegated through
  ``_patch_review``).
- AC-5: final ``Run.status == COMPLETED``.

Structural template: ``tests/integration/test_lifecycle_v03_end_to_end.py``.
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
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    read_lifecycle_memory,
)
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
# Scripted LLM provider — copied verbatim from v0.3 template, minus the
# review_implementation slot (the manual variant has a human reviewer).
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    name: str = "scripted-test"
    model: str = "scripted-v1"

    def __init__(self, by_node: Mapping[str, list[dict[str, Any]]]) -> None:
        self._by_node: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in by_node.items()
        }
        self.calls: list[tuple[str, str]] = []

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
        self.calls.append((node_name, str(messages)))
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
# DB / fixture helpers
# ---------------------------------------------------------------------------


def _build_session_factory(test_database_url: str) -> async_sessionmaker[AsyncSession]:
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
            trace_uri="file:///tmp/feat015-e2e.jsonl",
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
# Checkpoint signal mapping — drives the drainer.
# ---------------------------------------------------------------------------


# Map (executor_ref) → (signal name, payload-builder lambda).
# The payload lambda receives the current task_id (None for work-item-scoped).
_SIGNAL_FOR_REF: dict[
    str, tuple[str, Any]
] = {
    "human:confirm_brief": ("brief-confirmed", lambda _tid: {}),
    "human:confirm_tasks": (
        "tasks-confirmed",
        lambda _tid: {
            # OPERATOR REPLACES the LLM's 3 tasks with 2 — the
            # load-bearing assertion of this test (AC-3).
            "tasks": [
                {"id": "T-edit-1", "title": "Operator task 1"},
                {"id": "T-edit-2", "title": "Operator task 2"},
            ]
        },
    ),
    "human:confirm_assignment": (
        # IMP-004 / T-309: operator picks an assignee per task.  Distinct
        # name per task lets the merge-preserve + multi-task loop-back
        # assertions discriminate the two cases.
        "assignment-confirmed",
        lambda tid: {"assignee": f"operator-{tid}"},
    ),
    "human:confirm_plan": (
        "plan-confirmed",
        lambda _tid: {},  # approve LLM plan unchanged
    ),
    "human:request_implementation": (
        "implementation-complete",
        lambda _tid: {},
    ),
    "human:review_implementation": (
        "review-completed",
        lambda _tid: {"verdict": "pass"},
    ),
}


# ---------------------------------------------------------------------------
# End-to-end driver
# ---------------------------------------------------------------------------


async def _drive_manual_lifecycle(
    *,
    test_database_url: str,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
    llm_script: Mapping[str, list[dict[str, Any]]],
) -> tuple[Run, async_sessionmaker[AsyncSession], uuid.UUID, dict[str, int]]:
    """Run the manual variant end-to-end; return final run + engine call counts."""
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
            # IMP-005: codeSource is the run's anchor to a concrete
            # repo + branch.  This test seeds the Run row directly so it
            # bypasses ``start_run`` validation, but supplying the field
            # keeps the fixture realistic and future-proofs against the
            # day we exercise the validation path here.
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
    app.state.executor_registry = registry  # FEAT-015: signal-side hook
    transport = ASGITransport(app=app)

    trace_dir = Path(f"/tmp/feat015-e2e-{run.id}")
    trace_dir.mkdir(exist_ok=True)
    trace = JsonlTraceStore(trace_dir)
    cancel_event = asyncio.Event()

    correlation_log: list[uuid.UUID] = []
    counters: dict[str, int] = {
        "work_item_creates": 0,
        "task_creates": 0,
        "transitions": 0,
    }

    async def _post_transition(request: Any) -> Response:
        counters["transitions"] += 1
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
        # Task workflow — fresh engine id per task.
        counters["task_creates"] += 1
        return Response(201, json={"data": {"id": str(uuid.uuid4())}})

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:

            async def _drainer() -> None:
                """Fire engine webhooks AND deliver checkpoint signals."""
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
                        signal_entry = _SIGNAL_FOR_REF.get(ref)
                        if signal_entry is None:
                            continue
                        signal_name, payload_builder = signal_entry
                        task_id = (human_row.intake or {}).get("taskId") or ""
                        payload = payload_builder(task_id)

                        # Build the envelope by simulating what the
                        # signal-side hook in _deliver_to_human_dispatch
                        # would assemble.  Use the registry to find the
                        # binding's builder so the patch lands correctly.
                        envelope_result: dict[str, Any] = {
                            "signal_name": signal_name,
                            "task_id": task_id,
                            "payload": payload,
                        }
                        # FEAT-015: surface payload fields at the top
                        # level (mirrors _deliver_to_human_dispatch).
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
                                patch = builder(payload, current_memory)
                                envelope_result["__memory_patch"] = patch

                        # Mark the dispatch completed in DB.
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
                        [
                            (d.executor_ref, d.mode, d.state, str(d.dispatch_id)[:8])
                            for d in dispatches
                        ],
                    )
                    print(
                        "[debug] correlation_log:",
                        [str(c) for c in correlation_log],
                    )
                    print("[debug] llm calls:", [c[0] for c in provider.calls])
                    print("[debug] counters:", counters)
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

    return run, session_factory, work_item_id, counters


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_manual_lifecycle_drives_full_flow_with_operator_edits(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end manual run with operator-edited task list.

    Scenario:
        - LLM generates 3 tasks.
        - Operator edits to 2 tasks via tasks-confirmed.
        - For each of the 2 tasks: plan-confirmed (no edit),
          implementation-complete, review-completed (pass).
        - Final: run completes, engine sees 2 task creates (NOT 3).
    """
    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [
            {
                "work_item_id": "FEAT-099",
                "title": "v04 manual e2e",
                "summary": "test only",
            }
        ],
        "generate_tasks": [
            {
                "tasks": [
                    {"id": "T-llm-1", "title": "LLM 1", "executor": "claude-code"},
                    {"id": "T-llm-2", "title": "LLM 2", "executor": "claude-code"},
                    {"id": "T-llm-3", "title": "LLM 3", "executor": "claude-code"},
                ]
            }
        ],
        # One plan per task in the *edited* list.
        "generate_plan": [
            {"task_id": "T-edit-1", "plan_markdown": "# plan T-edit-1"},
            {"task_id": "T-edit-2", "plan_markdown": "# plan T-edit-2"},
        ],
    }

    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id, counters = await _drive_manual_lifecycle(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
        )

        # AC-5: final status.
        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            actual_status = (
                RunStatus(run_row.status)
                if isinstance(run_row.status, str)
                else run_row.status
            )
        assert actual_status == RunStatus.COMPLETED, (
            f"expected COMPLETED, got {actual_status}; "
            f"final_state={run_row.final_state}, counters={counters}"
        )

        # AC-3 LOAD-BEARING: operator's 2 tasks reach the engine, not the
        # LLM's 3.
        assert counters["task_creates"] == 2, (
            f"propose_tasks must fan out to the edited 2 tasks, not the "
            f"LLM 3; got task_creates={counters['task_creates']}"
        )
        assert counters["work_item_creates"] == 1
        # Each task fires roughly 5 engine transitions (T2, T4, T5, T6, T7,
        # T9, T10) plus W2 + W4 + W6.  Spot-check a sensible lower bound.
        assert counters["transitions"] >= 10, counters

        # AC-4: review history has 2 entries (one per edited task) with
        # pass verdict and the right task ids.
        async with session_factory() as session:
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run.id)
            )
        memory = read_lifecycle_memory((mem_row.data if mem_row else {}) or {})
        assert len(memory.review_history) == 2, (
            f"expected 2 review entries, got {len(memory.review_history)}: "
            f"{[(r.task_id, r.verdict) for r in memory.review_history]}"
        )
        assert all(r.verdict == "pass" for r in memory.review_history)
        assert {r.task_id for r in memory.review_history} == {"T-edit-1", "T-edit-2"}

        # Sanity: completed_task_ids matches.
        assert memory.completed_task_ids == ["T-edit-1", "T-edit-2"]

        # IMP-004 / T-309: confirm_assignment fired once per task and
        # persisted distinct assignees through the per-task loop-back.
        # The merge-preserve logic in apply_assignment_confirmation must
        # carry T-edit-1's assignee forward when T-edit-2's gets recorded;
        # naive overwrite would leave only one key.
        assignments = ((mem_row.data if mem_row else {}) or {}).get(
            "assignments"
        ) or {}
        assert assignments == {
            "T-edit-1": "operator-T-edit-1",
            "T-edit-2": "operator-T-edit-2",
        }, (
            "expected per-task assignees preserved through loop-back; got "
            f"{assignments!r}"
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)
