"""FastAPI routers for /api/v1/* and /hooks/engine/*."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse

from app.config import Settings
from app.core.api_auth import require_api_key
from app.core.database import get_db_session
from app.core.dependencies import (
    get_llm_provider_dep,
    get_session_factory,
    get_settings_dep,
    get_supervisor,
)
from app.core.envelope import Envelope, Meta, envelope
from app.core.exceptions import NotFoundError
from app.core.llm import LLMProvider
from app.core.webhook_auth import require_engine_signature, require_flow_engine_signature
from app.modules.ai import repository, service
from app.modules.ai.dependencies import (
    get_engine_client,
    get_github_checks_client_dep,
    get_lifecycle_engine_client,
    get_lifecycle_workflow_ids,
    require_actor_role,
)
from app.modules.ai.engine_client import FlowEngineClient
from app.modules.ai.enums import ActorRole, WebhookEventType, WebhookSource
from app.modules.ai.github.checks import GitHubChecksClient
from app.modules.ai.lifecycle import reactor as lifecycle_reactor
from app.modules.ai.lifecycle import service as lifecycle_service
from app.modules.ai.lifecycle.declarations import WORK_ITEM_WORKFLOW_NAME
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Task, WorkItem
from app.modules.ai.schemas import (
    AgentDto,
    CancelRunRequest,
    CreateRunRequest,
    ImplementationSubmitRequest,
    LifecycleSignalMeta,
    PlanApproveRequest,
    PlanRejectRequest,
    PlanSubmitRequest,
    PolicyCallDto,
    ReviewApproveRequest,
    ReviewRejectRequest,
    RunDetailDto,
    RunSummaryDto,
    SignalCreateRequest,
    SignalCreateResponse,
    StepDto,
    TaskApproveRequest,
    TaskAssignRequest,
    TaskDeferRequest,
    TaskDto,
    TaskRejectRequest,
    TaskSignalResponse,
    WebhookAckDto,
    WebhookEventRequest,
    WorkItemCloseRequest,
    WorkItemCreateRequest,
    WorkItemDto,
    WorkItemLockRequest,
    WorkItemSignalResponse,
    WorkItemUnlockRequest,
)
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import TraceStore, get_trace_store
from app.modules.ai.webhooks.github import (
    GitHubPrEvent,
    extract_task_reference,
    verify_github_signature,
)

# ---------------------------------------------------------------------------
# Control-plane router — /api/v1
# ---------------------------------------------------------------------------

api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)


@api_router.post("/runs", status_code=202, response_model=Envelope[RunSummaryDto])
async def create_run(
    body: CreateRunRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    supervisor: Annotated[RunSupervisor, Depends(get_supervisor)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    policy: Annotated[LLMProvider, Depends(get_llm_provider_dep)],
    engine: Annotated[FlowEngineClient, Depends(get_engine_client)],
    trace: Annotated[TraceStore, Depends(get_trace_store)],
) -> Envelope[RunSummaryDto]:
    """Start a new agent run (returns 202 immediately)."""
    result = await service.start_run(
        body,
        settings=settings,
        supervisor=supervisor,
        session_factory=session_factory,
        policy=policy,
        engine=engine,
        trace=trace,
    )
    return envelope(result)


@api_router.get("/runs", response_model=Envelope[list[RunSummaryDto]])
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    status: Annotated[str | None, Query()] = None,
    agent_ref: Annotated[str | None, Query(alias="agentRef")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> Envelope[list[RunSummaryDto]]:
    """List runs with pagination and filtering."""
    items, total = await service.list_runs(db, status=status, agent_ref=agent_ref, page=page, page_size=page_size)
    return envelope(items, meta=Meta(total_count=total, page=page, page_size=page_size))


@api_router.get("/runs/{run_id}", response_model=Envelope[RunDetailDto])
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Envelope[RunDetailDto]:
    """Fetch a single run."""
    result = await service.get_run(run_id, db)
    return envelope(result)


@api_router.post("/runs/{run_id}/cancel", response_model=Envelope[RunSummaryDto])
async def cancel_run(
    run_id: uuid.UUID,
    body: CancelRunRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    supervisor: Annotated[RunSupervisor, Depends(get_supervisor)],
) -> Envelope[RunSummaryDto]:
    """Cancel a running run."""
    result = await service.cancel_run(run_id, body, db, supervisor=supervisor)
    return envelope(result)


@api_router.post(
    "/runs/{run_id}/signals",
    status_code=202,
    response_model=SignalCreateResponse,
)
async def post_signal(
    run_id: uuid.UUID,
    body: SignalCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    supervisor: Annotated[RunSupervisor, Depends(get_supervisor)],
    trace: Annotated[TraceStore, Depends(get_trace_store)],
) -> SignalCreateResponse:
    """Deliver an operator signal to a run (FEAT-005).

    Persist-first-then-wake.  Idempotent on ``(run_id, name, task_id)`` —
    duplicate calls return ``202`` with ``meta.alreadyReceived=true`` and
    do not re-wake the supervisor.
    """
    dto, created = await service.send_signal(
        run_id=run_id,
        name=body.name,
        task_id=body.task_id,
        payload=body.payload,
        db=db,
        supervisor=supervisor,
        trace=trace,
    )
    meta = None if created else {"alreadyReceived": True}
    return SignalCreateResponse(data=dto, meta=meta)


@api_router.get("/runs/{run_id}/steps", response_model=Envelope[list[StepDto]])
async def list_steps(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> Envelope[list[StepDto]]:
    """List steps for a run."""
    items, total = await service.list_steps(run_id, db, page=page, page_size=page_size)
    return envelope(items, meta=Meta(total_count=total, page=page, page_size=page_size))


@api_router.get("/runs/{run_id}/policy-calls", response_model=Envelope[list[PolicyCallDto]])
async def list_policy_calls(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> Envelope[list[PolicyCallDto]]:
    """List policy decisions for a run."""
    items, total = await service.list_policy_calls(run_id, db, page=page, page_size=page_size)
    return envelope(items, meta=Meta(total_count=total, page=page, page_size=page_size))


@api_router.get("/runs/{run_id}/trace")
async def stream_trace(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trace: Annotated[TraceStore, Depends(get_trace_store)],
    follow: Annotated[bool, Query()] = False,
    since: Annotated[datetime | None, Query()] = None,
    kind: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    """Stream a run's trace as ``application/x-ndjson``.

    * ``follow=true`` keeps the stream open until the run terminates.
    * ``since=<ISO-8601>`` emits only records with a later timestamp.
    * ``kind=step`` / ``kind=policy_call`` / ``kind=webhook_event`` filters
      by record kind; the parameter is repeatable.
    """
    # Pre-flight 404 so the caller gets RFC 7807 Problem Details, not a
    # truncated stream with headers already sent.
    if await repository.get_run_by_id(db, run_id) is None:
        raise NotFoundError(f"run not found: {run_id}")

    kinds: frozenset[str] | None = frozenset(kind) if kind else None
    iterator = service.stream_trace(
        run_id,
        db=db,
        trace=trace,
        follow=follow,
        since=since,
        kinds=kinds,
    )
    return StreamingResponse(
        iterator,
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_router.get("/agents", response_model=Envelope[list[AgentDto]])
async def list_agents(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Envelope[list[AgentDto]]:
    """List on-disk agent definitions."""
    items = await service.list_agents(settings=settings)
    return envelope(items)


# ---------------------------------------------------------------------------
# FEAT-006 — Work-item lifecycle signals (S1-S4)
# ---------------------------------------------------------------------------


def _work_item_envelope(
    wi: WorkItem,
    *,
    already_received: bool,
) -> WorkItemSignalResponse:
    dto = WorkItemDto.model_validate(wi, from_attributes=True)
    meta = LifecycleSignalMeta(already_received=True) if already_received else None
    return WorkItemSignalResponse(data=dto, meta=meta)


@api_router.post(
    "/work-items",
    status_code=202,
    response_model=WorkItemSignalResponse,
)
async def open_work_item(
    body: WorkItemCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    workflow_ids: Annotated[dict[str, uuid.UUID], Depends(get_lifecycle_workflow_ids)],
) -> WorkItemSignalResponse:
    """S1 — open a new work item.  Admin only."""
    del role  # enforced by dependency
    wi, is_new = await lifecycle_service.open_work_item_signal(
        db,
        external_ref=body.external_ref,
        type=body.type,
        title=body.title,
        source_path=body.source_path,
        opened_by="admin",
        engine=engine,
        engine_workflow_id=workflow_ids.get(WORK_ITEM_WORKFLOW_NAME),
    )
    return _work_item_envelope(wi, already_received=not is_new)


@api_router.post(
    "/work-items/{work_item_id}/lock",
    status_code=202,
    response_model=WorkItemSignalResponse,
)
async def lock_work_item(
    work_item_id: uuid.UUID,
    body: WorkItemLockRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> WorkItemSignalResponse:
    """S2 — admin pause."""
    del role
    wi, is_new = await lifecycle_service.lock_work_item_signal(
        db, work_item_id, reason=body.reason, actor="admin", engine=engine
    )
    return _work_item_envelope(wi, already_received=not is_new)


@api_router.post(
    "/work-items/{work_item_id}/unlock",
    status_code=202,
    response_model=WorkItemSignalResponse,
)
async def unlock_work_item(
    work_item_id: uuid.UUID,
    body: WorkItemUnlockRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> WorkItemSignalResponse:
    """S3 — admin resume."""
    del role, body
    wi, is_new = await lifecycle_service.unlock_work_item_signal(
        db, work_item_id, actor="admin", engine=engine
    )
    return _work_item_envelope(wi, already_received=not is_new)


@api_router.post(
    "/work-items/{work_item_id}/close",
    status_code=202,
    response_model=WorkItemSignalResponse,
)
async def close_work_item(
    work_item_id: uuid.UUID,
    body: WorkItemCloseRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> WorkItemSignalResponse:
    """S4 — admin close (requires ``ready``)."""
    del role
    wi, is_new = await lifecycle_service.close_work_item_signal(
        db, work_item_id, notes=body.notes, actor="admin", engine=engine
    )
    return _work_item_envelope(wi, already_received=not is_new)


# ---------------------------------------------------------------------------
# FEAT-006 — Task signals (S5-S7, S14)
# ---------------------------------------------------------------------------


def _task_envelope(task: Task, *, already_received: bool) -> TaskSignalResponse:
    dto = TaskDto.model_validate(task, from_attributes=True)
    meta = LifecycleSignalMeta(already_received=True) if already_received else None
    return TaskSignalResponse(data=dto, meta=meta)


@api_router.post(
    "/tasks/{task_id}/approve",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def approve_task(
    task_id: uuid.UUID,
    body: TaskApproveRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> TaskSignalResponse:
    """S5 — admin approves a proposed task (fires T4 + W2 derivation)."""
    del role, body
    task, is_new = await lifecycle_service.approve_task_signal(
        db, task_id, actor="admin", engine=engine
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/reject",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def reject_task(
    task_id: uuid.UUID,
    body: TaskRejectRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> TaskSignalResponse:
    """S6 — admin rejects a proposed task with feedback."""
    del role
    task, is_new = await lifecycle_service.reject_task_signal(
        db, task_id, feedback=body.feedback, actor="admin", engine=engine
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/assign",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def assign_task(
    task_id: uuid.UUID,
    body: TaskAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> TaskSignalResponse:
    """S7 — admin assigns the task (dev or agent)."""
    del role
    task, _, is_new = await lifecycle_service.assign_task_signal(
        db,
        task_id,
        assignee_type=body.assignee_type,
        assignee_id=body.assignee_id,
        actor="admin",
        engine=engine,
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/defer",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def defer_task(
    task_id: uuid.UUID,
    body: TaskDeferRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
) -> TaskSignalResponse:
    """S14 — admin defers a non-terminal task (fires W5 derivation)."""
    del role
    task, is_new = await lifecycle_service.defer_task_signal(
        db, task_id, reason=body.reason, actor="admin", engine=engine
    )
    return _task_envelope(task, already_received=not is_new)


# ---------------------------------------------------------------------------
# FEAT-006 — Plan signals (S8-S10)
# ---------------------------------------------------------------------------


@api_router.post(
    "/tasks/{task_id}/plan",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def submit_plan(
    task_id: uuid.UUID,
    body: PlanSubmitRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[
        ActorRole, Depends(require_actor_role(ActorRole.ADMIN, ActorRole.DEV))
    ],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TaskSignalResponse:
    """S8 — submit a plan for review.  Allowed for admin or dev."""
    del role, settings
    task, is_new = await lifecycle_service.submit_plan_signal(
        db,
        task_id,
        plan_path=body.plan_path,
        plan_sha=body.plan_sha,
        actor="submitter",
        engine=engine,
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/plan/approve",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def approve_plan(
    task_id: uuid.UUID,
    body: PlanApproveRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[
        ActorRole, Depends(require_actor_role(ActorRole.ADMIN, ActorRole.DEV))
    ],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TaskSignalResponse:
    """S9 — approve plan.  Matrix decides required role inside the transition."""
    del body
    task, is_new = await lifecycle_service.approve_plan_signal(
        db,
        task_id,
        actor=role.value,
        actor_role=role,
        solo_dev=settings.solo_dev_mode,
        engine=engine,
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/plan/reject",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def reject_plan(
    task_id: uuid.UUID,
    body: PlanRejectRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[
        ActorRole, Depends(require_actor_role(ActorRole.ADMIN, ActorRole.DEV))
    ],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TaskSignalResponse:
    """S10 — reject plan with feedback."""
    task, is_new = await lifecycle_service.reject_plan_signal(
        db,
        task_id,
        feedback=body.feedback,
        actor=role.value,
        actor_role=role,
        solo_dev=settings.solo_dev_mode,
        engine=engine,
    )
    return _task_envelope(task, already_received=not is_new)


# ---------------------------------------------------------------------------
# FEAT-006 — Implementation + review signals (S11, S12, S13)
# ---------------------------------------------------------------------------


@api_router.post(
    "/tasks/{task_id}/implementation",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def submit_implementation(
    task_id: uuid.UUID,
    body: ImplementationSubmitRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[ActorRole, Depends(require_actor_role(ActorRole.ADMIN))],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    github: Annotated[GitHubChecksClient, Depends(get_github_checks_client_dep)],
) -> TaskSignalResponse:
    """S11 (agent path) — submit an implementation for review."""
    del role
    task, is_new = await lifecycle_service.submit_implementation_signal(
        db,
        task_id,
        pr_url=body.pr_url,
        commit_sha=body.commit_sha,
        summary=body.summary,
        actor="admin",
        engine=engine,
        github=github,
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/review/approve",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def approve_review(
    task_id: uuid.UUID,
    body: ReviewApproveRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[
        ActorRole, Depends(require_actor_role(ActorRole.ADMIN, ActorRole.DEV))
    ],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    github: Annotated[GitHubChecksClient, Depends(get_github_checks_client_dep)],
) -> TaskSignalResponse:
    """S12 — approve review.  Fires W5 derivation."""
    del body
    task, is_new = await lifecycle_service.approve_review_signal(
        db,
        task_id,
        actor=role.value,
        actor_role=role,
        solo_dev=settings.solo_dev_mode,
        engine=engine,
        github=github,
    )
    return _task_envelope(task, already_received=not is_new)


@api_router.post(
    "/tasks/{task_id}/review/reject",
    status_code=202,
    response_model=TaskSignalResponse,
)
async def reject_review(
    task_id: uuid.UUID,
    body: ReviewRejectRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    role: Annotated[
        ActorRole, Depends(require_actor_role(ActorRole.ADMIN, ActorRole.DEV))
    ],
    engine: Annotated[FlowEngineLifecycleClient | None, Depends(get_lifecycle_engine_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    github: Annotated[GitHubChecksClient, Depends(get_github_checks_client_dep)],
) -> TaskSignalResponse:
    """S13 — reject review with feedback."""
    task, is_new = await lifecycle_service.reject_review_signal(
        db,
        task_id,
        feedback=body.feedback,
        actor=role.value,
        actor_role=role,
        solo_dev=settings.solo_dev_mode,
        engine=engine,
        github=github,
    )
    return _task_envelope(task, already_received=not is_new)


# ---------------------------------------------------------------------------
# Webhook router — /hooks/engine
# ---------------------------------------------------------------------------

hooks_router = APIRouter(prefix="/hooks/engine")


@hooks_router.post("/events", status_code=202, response_model=Envelope[WebhookAckDto])
async def receive_engine_event(
    request: Request,
    body: WebhookEventRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    sig_ok: Annotated[bool, Depends(require_engine_signature)],
    supervisor: Annotated[RunSupervisor, Depends(get_supervisor)],
    trace: Annotated[TraceStore, Depends(get_trace_store)],
) -> Envelope[WebhookAckDto] | JSONResponse:
    """Ingest a flow-engine lifecycle event.

    Bad-signature events are persisted (for forensics) and rejected with 401.
    """
    result = await service.ingest_engine_event(
        body.model_dump(mode="json"),
        sig_ok,
        db,
        supervisor=supervisor,
        trace=trace,
    )

    if not sig_ok:
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://orchestrator.local/problems/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "invalid webhook signature",
            },
            media_type="application/problem+json",
        )

    return envelope(WebhookAckDto(received=True, event_id=result.id))


# ---------------------------------------------------------------------------
# FEAT-006 rc2 — Engine lifecycle webhook (item.transitioned)
# ---------------------------------------------------------------------------


@hooks_router.post("/lifecycle/item-transitioned", status_code=202)
async def receive_lifecycle_item_transitioned(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    sig_ok: Annotated[bool, Depends(require_flow_engine_signature)],
    workflow_ids: Annotated[
        dict[str, uuid.UUID], Depends(get_lifecycle_workflow_ids)
    ],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> JSONResponse:
    """Ingest a flow-engine lifecycle webhook (state change on an item).

    Regardless of signature outcome the event is persisted to
    ``webhook_events`` (source='engine', event_type=
    ``lifecycle_item_transitioned``).  On a valid signature, the reactor
    dispatches derivations (W2/W5).  Idempotent via the delivery id.
    """
    raw: bytes = request.state.raw_body

    import json as _json

    try:
        parsed = _json.loads(raw)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "type": "https://orchestrator.local/problems/validation-error",
                "title": "Validation error",
                "status": 400,
                "detail": "invalid JSON body",
            },
            media_type="application/problem+json",
        )

    body_dict: dict[str, Any] = parsed if isinstance(parsed, dict) else {}  # type: ignore[assignment]
    delivery_id: str = str(body_dict.get("deliveryId") or "unknown")
    item_id_raw = body_dict.get("itemId")
    item_id: str | None = str(item_id_raw) if item_id_raw else None
    dedupe_key = f"lifecycle:{item_id or 'unknown'}:{delivery_id}"

    wh_event = await repository.upsert_webhook_event(
        db,
        event_type=WebhookEventType.LIFECYCLE_ITEM_TRANSITIONED.value,
        engine_run_id=f"lifecycle:{item_id or 'unknown'}",
        payload=body_dict,
        signature_ok=sig_ok,
        source=WebhookSource.ENGINE.value,
        dedupe_key=dedupe_key,
    )
    await db.commit()

    if not sig_ok:
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://orchestrator.local/problems/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "invalid lifecycle webhook signature",
            },
            media_type="application/problem+json",
        )

    # Parse into the typed event for reactor dispatch.  Malformed body is
    # logged but not an error — the event row is already persisted for
    # forensics.
    try:
        event = lifecycle_reactor.LifecycleWebhookEvent.model_validate(parsed)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "lifecycle webhook body did not parse; skipping reactor",
            exc_info=True,
        )
        return JSONResponse(
            status_code=202,
            content={"data": {"received": True, "eventId": str(wh_event.id) if wh_event else None, "reacted": False}},
        )

    # Flip workflow ids mapping so reactor can resolve name from id.
    workflow_name_by_id = {wid: name for name, wid in workflow_ids.items()}
    # FEAT-008/T-173: thread the lifespan-built effector registry into the
    # reactor so registered effectors fire on every transition. ``getattr``
    # default keeps tests that build a bare ``FastAPI()`` (no lifespan run)
    # working — registry-less is a graceful no-op in the reactor.
    registry = getattr(request.app.state, "effector_registry", None)
    await lifecycle_reactor.handle_transition(
        db,
        event,
        workflow_name_by_id=workflow_name_by_id,
        registry=registry,
        settings=settings,
    )
    await db.commit()

    return JSONResponse(
        status_code=202,
        content={
            "data": {
                "received": True,
                "eventId": str(wh_event.id) if wh_event else None,
                "reacted": True,
            }
        },
    )


# ---------------------------------------------------------------------------
# FEAT-006 — GitHub PR webhook
# ---------------------------------------------------------------------------

github_hooks_router = APIRouter(prefix="/hooks/github")


@github_hooks_router.post("/pr")
async def receive_github_pr(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    event_type: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    delivery_id: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
) -> JSONResponse:
    """Ingest a GitHub ``pull_request`` webhook.

    Verifies the signature, persists a ``WebhookEvent`` regardless, then
    (on match) invokes the S11 transition for the referenced task.
    """
    raw = await request.body()

    secret = settings.github_webhook_secret
    if secret is None:
        return JSONResponse(
            status_code=503,
            content={
                "type": "https://orchestrator.local/problems/not-configured",
                "title": "Not configured",
                "status": 503,
                "detail": "GITHUB_WEBHOOK_SECRET is not configured",
            },
            media_type="application/problem+json",
        )

    sig_ok = verify_github_signature(raw, signature, secret.get_secret_value())

    # Non-pull_request events: ack without persisting (less noise).
    if event_type != "pull_request":
        return JSONResponse(
            status_code=202,
            content={"data": {"received": True, "ignored": True}},
        )

    import json

    try:
        parsed_dict = json.loads(raw)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "type": "https://orchestrator.local/problems/validation-error",
                "title": "Validation error",
                "status": 400,
                "detail": "invalid JSON body",
            },
            media_type="application/problem+json",
        )

    if sig_ok:
        try:
            event = GitHubPrEvent.model_validate(parsed_dict)
        except Exception:
            event = None
    else:
        event = None

    pr_number = None
    if event is not None:
        pr_number = event.pull_request.number
    elif isinstance(parsed_dict.get("pull_request"), dict):
        pr_number = parsed_dict["pull_request"].get("number")

    dedupe_key = (
        f"github:pr:{pr_number}:{delivery_id or 'unknown'}"
        if pr_number is not None
        else f"github:pr:unknown:{delivery_id or 'unknown'}"
    )

    parsed_dict_typed: dict[str, Any] = (
        parsed_dict if isinstance(parsed_dict, dict) else {}  # type: ignore[assignment]
    )
    wh_event = await repository.upsert_webhook_event(
        db,
        event_type=WebhookEventType.GITHUB_PR_OPENED.value,
        engine_run_id=f"github:pr:{pr_number or 'unknown'}",
        payload=parsed_dict_typed,
        signature_ok=sig_ok,
        source=WebhookSource.GITHUB.value,
        dedupe_key=dedupe_key,
    )
    await db.commit()

    if not sig_ok:
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://orchestrator.local/problems/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "invalid github signature",
            },
            media_type="application/problem+json",
        )

    if event is None:
        return JSONResponse(
            status_code=202,
            content={"data": {"received": True, "matchedTaskId": None}},
        )

    ref = extract_task_reference(event.pull_request.title, event.pull_request.body)
    matched_task_id = None

    if ref is not None and event.action in {"opened", "reopened"}:
        # Find the task by external_ref.
        task = await repository.get_task_by_external_ref(db, ref)
        if task is not None:
            matched_task_id = str(task.id)
            try:
                await lifecycle_service.submit_implementation_signal(
                    db,
                    task.id,
                    pr_url=None,
                    commit_sha=event.pull_request.head.sha,
                    summary=(event.pull_request.title or f"PR #{event.pull_request.number}"),
                    actor="github",
                )
            except Exception:
                # Task state didn't permit the transition; audit only.
                await db.rollback()

    return JSONResponse(
        status_code=202,
        content={
            "data": {
                "received": True,
                "eventId": str(wh_event.id),
                "matchedTaskId": matched_task_id,
            }
        },
    )
