"""End-to-end happy-path + correction-budget tests for lifecycle-agent@0.3.0.

Drives the v0.3.0 agent through the deterministic runtime against a
respx-stubbed engine and a scripted LLM provider.  Covers:

* **AC-1 (happy path).** The agent loops through every node — composite
  LLM+engine dispatches feed engine webhooks; the human node pauses for
  an operator signal; the run reaches ``RunStatus.COMPLETED``.
* **AC-2 (correction-budget exceeded).** When ``review_implementation``
  returns ``verdict='fail'`` more times than ``LIFECYCLE_MAX_CORRECTIONS``
  permits, the run terminates at ``terminate_correction_budget`` with
  ``RunStatus.FAILED``.

The tests deliberately patch the runtime's ``_build_node_intake`` so the
intake payload threads ``taskId`` from the typed lifecycle memory.
Without that, downstream nodes (``generate_plan``, ``review_implementation``)
have no taskId to substitute into their prompt templates, and the LLM
content executor would fail with ``prompt_render_failed``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
    Dispatch,
    PendingAuxWrite,
    Run,
    RunMemory,
    RunSignal,
    Step,
    WebhookEvent,
    WorkItem,
)
from app.modules.ai.runtime_deterministic import run_deterministic_loop
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
    """Returns scripted ``ToolCall`` payloads keyed by node name."""

    name: str = "scripted-test"
    model: str = "scripted-v1"

    def __init__(self, by_node: Mapping[str, list[dict[str, Any]]]) -> None:
        # Mutable map; each call pops the head.
        self._by_node: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in by_node.items()}
        self.calls: list[tuple[str, str]] = []

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall:
        del tools
        # Identify the node from the system prompt's first line.
        # The bootstrap loads a ``<node>.md`` system prompt; we encode
        # the node name by inspecting the first H1.
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
    """Map the H1 of a system prompt back to its node name.

    Headlines (after copying the .ai-framework prompts):
    - "# Load Work-Item Brief"            -> load_work_item
    - "# Feature Task Generation Prompt"  -> generate_tasks
    - "# Task Implementation Plan Prompt" -> generate_plan
    - "# Lifecycle Review Prompt"         -> review_implementation
    """
    headline = first_line.lstrip("# ").strip().lower()
    if "load" in headline and "work-item" in headline:
        return "load_work_item"
    if "feature" in headline and "task" in headline:
        return "generate_tasks"
    if "plan" in headline:
        return "generate_plan"
    if "review" in headline:
        return "review_implementation"
    raise AssertionError(f"scripted provider could not map headline: {first_line!r}")


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
            trace_uri="file:///tmp/feat011-e2e.jsonl",
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
            await session.execute(delete(WorkItem).where(WorkItem.id == work_item_id))
        await session.commit()


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


async def _drive_engine_with_webhooks(
    *,
    test_database_url: str,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
    llm_script: Mapping[str, list[dict[str, Any]]],
    inject_signal: bool,
    expected_status: RunStatus,
) -> tuple[Run, async_sessionmaker[AsyncSession], uuid.UUID]:
    """Shared driver used by both AC-1 and AC-2 tests."""
    monkeypatch.setenv("ENGINE_WEBHOOK_SECRET", webhook_secret)
    from app.config import get_settings

    get_settings.cache_clear()

    session_factory = _build_session_factory(test_database_url)

    # BUG-003: ``register_work_item`` (W1 create_item) is the single
    # entry point that mints ``engine_item_id`` and inserts the local
    # ``work_items`` row.  The test no longer pre-seeds either — the
    # respx-mocked engine returns a synthesised id below.
    engine_item_id = uuid.uuid4()
    work_item_id: uuid.UUID | None = None
    work_item_workflow_id = uuid.uuid4()

    run = await _seed_run(
        session_factory,
        intake={
            "workItemPath": "docs/work-items/FEAT-099.md",
            "workItemId": "FEAT-099",
            "taskId": "T-1",
        },
    )

    # Wire registry.
    registry = ExecutorRegistry()
    provider = _ScriptedProvider(by_node=llm_script)
    fake_engine_client = FlowEngineLifecycleClient(
        base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=1
    )
    register_lifecycle_v03(
        registry,
        _AGENT_REF,
        lifecycle_client=fake_engine_client,
        llm_provider=provider,  # type: ignore[arg-type]
        session_factory=session_factory,
        workflow_ids={"work_item_workflow": work_item_workflow_id},
    )

    agent = load_agent(_AGENT_REF, _AGENTS_DIR)
    supervisor = RunSupervisor()

    app = create_app()
    app.state.supervisor = supervisor
    transport = ASGITransport(app=app)

    trace_dir = Path(f"/tmp/feat011-e2e-{run.id}")
    trace_dir.mkdir(exist_ok=True)
    trace = JsonlTraceStore(trace_dir)
    cancel_event = asyncio.Event()

    correlation_log: list[uuid.UUID] = []

    async def _post_transition(request: Any) -> Response:
        body = json.loads(request.content.decode())
        comment = body.get("comment", "")
        prefix = "orchestrator-corr:"
        for tok in comment.split():
            if tok.startswith(prefix):
                correlation_log.append(uuid.UUID(tok[len(prefix) :]))
                break
        return Response(
            200,
            json={"data": {"id": str(uuid.uuid4()), "transitionRunId": str(uuid.uuid4())}},
        )

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:

            async def _drainer() -> None:
                """Fire matching webhooks for every observed engine call.

                Polls ``correlation_log`` and pushes webhooks so the
                reactor's wake-dispatch step resolves the supervisor
                future.  Also fires the implementation-complete signal
                when ``inject_signal`` is True (after the human node
                pauses).
                """
                fired: set[uuid.UUID] = set()
                signaled_dispatches: set[uuid.UUID] = set()
                deadline = asyncio.get_event_loop().time() + 30
                while asyncio.get_event_loop().time() < deadline:
                    # Fire webhooks for unseen correlation ids — but only
                    # once the runtime has persisted ``correlation_id``
                    # onto the matching ``Dispatch`` row.  Otherwise the
                    # reactor's lookup misses and the dispatch hangs.
                    for corr in list(correlation_log):
                        if corr in fired:
                            continue
                        async with session_factory() as session:
                            committed = await session.scalar(
                                select(Dispatch).where(
                                    Dispatch.run_id == run.id,
                                    Dispatch.intake["correlation_id"].astext == str(corr),
                                )
                            )
                        if committed is None:
                            continue  # try again next tick
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
                        # 202 expected; tolerate 4xx on idempotent retries.
                        assert resp.status_code in (202, 200, 409, 400), resp.text
                        fired.add(corr)
                    # Inject signal for every parked human dispatch.
                    if inject_signal:
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
                            # Use the supervisor directly — bypasses API
                            # auth and matches the deterministic-runtime
                            # contract (deliver_dispatch resolves the
                            # awaited future).
                            from app.modules.ai.schemas import DispatchEnvelope

                            env = DispatchEnvelope(
                                dispatch_id=human_row.dispatch_id,
                                step_id=human_row.step_id,
                                run_id=run.id,
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
                            signaled_dispatches.add(human_row.dispatch_id)
                    await asyncio.sleep(0.05)

            with respx.mock(base_url=_ENGINE_BASE, assert_all_called=False) as mock:
                mock.post("/api/auth/token").respond(200, json=_TOKEN_RESPONSE)
                # BUG-003: mock the W1 create_item endpoint —
                # ``register_work_item`` posts here and persists the
                # returned id into the local WorkItem + Run.intake.
                mock.post(url__regex=r"/api/workflows/[^/]+/items").respond(
                    201, json={"data": {"id": str(engine_item_id)}}
                )
                mock.post(url__regex=r"/api/items/[^/]+/transitions").mock(side_effect=_post_transition)
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
                            dispatch_timeout_seconds=8,
                        ),
                        timeout=120,
                    )
                except TimeoutError:
                    # Surface what happened so failures are diagnosable.
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
                    print("[debug] correlation_log:", [str(c) for c in correlation_log])
                    print("[debug] llm calls:", [c[0] for c in provider.calls])
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
        run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
        actual_status = RunStatus(run_row.status) if isinstance(run_row.status, str) else run_row.status
        # BUG-003: ``register_work_item`` inserted the local row keyed on
        # the synthesised engine_item_id — discover its id for cleanup.
        wi_row = await session.scalar(
            select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
        )
        if wi_row is not None:
            work_item_id = wi_row.id
    assert actual_status == expected_status, (
        f"expected {expected_status}, got {actual_status}; "
        f"final_state={run_row.final_state}, calls={[c[0] for c in provider.calls]}"
    )
    return run, session_factory, work_item_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_ac1_happy_path_run_reaches_completed(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: every node fires; the run reaches RunStatus.COMPLETED."""
    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [
            {"work_item_id": "FEAT-099", "title": "v03 e2e", "summary": "test only"}
        ],
        "generate_tasks": [
            {"tasks": [{"id": "T-1", "title": "only task", "executor": "claude-code"}]}
        ],
        "generate_plan": [
            {"task_id": "T-1", "plan_markdown": "# plan\nstep 1."}
        ],
        "review_implementation": [
            {"task_id": "T-1", "verdict": "pass", "feedback": "lgtm"}
        ],
    }
    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id = await _drive_engine_with_webhooks(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
            inject_signal=True,
            expected_status=RunStatus.COMPLETED,
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)


async def test_ac2_correction_budget_exhausted_run_fails(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2: review fails twice, budget exhausted → run reaches RunStatus.FAILED.

    The review and human-signal cycle repeats until
    ``correction_attempts_under_bound`` returns False (default bound=2);
    the resolver routes to ``terminate_correction_budget`` whose
    LocalExecutor emits an ``error`` envelope, surfacing the run as
    ``FAILED`` with stop_reason=ERROR.
    """
    # Provide enough fail verdicts to exhaust the budget.  After 2 fails
    # ``correction_attempts == 2 == bound`` so the resolver routes to
    # terminate_correction_budget (no further review call needed).
    llm_script: dict[str, list[dict[str, Any]]] = {
        "load_work_item": [
            {"work_item_id": "FEAT-099", "title": "v03 e2e", "summary": "test only"}
        ],
        "generate_tasks": [
            {"tasks": [{"id": "T-1", "title": "only task", "executor": "claude-code"}]}
        ],
        "generate_plan": [
            {"task_id": "T-1", "plan_markdown": "# plan\nv1."}
        ],
        "review_implementation": [
            {"task_id": "T-1", "verdict": "fail", "feedback": "missing tests"},
            {"task_id": "T-1", "verdict": "fail", "feedback": "still missing tests"},
        ],
    }
    run = None
    session_factory = None
    work_item_id = None
    try:
        run, session_factory, work_item_id = await _drive_engine_with_webhooks(
            test_database_url=test_database_url,
            webhook_secret=webhook_secret,
            monkeypatch=monkeypatch,
            llm_script=llm_script,
            inject_signal=True,
            expected_status=RunStatus.FAILED,
        )
    finally:
        if run is not None and session_factory is not None:
            await _cleanup(session_factory, run.id, work_item_id=work_item_id)


# Silence unused-import warnings for shared fixtures.
_ = MagicMock
