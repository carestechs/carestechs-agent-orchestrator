"""FastAPI routers for /api/v1/* and /hooks/engine/*."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
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
from app.core.webhook_auth import require_engine_signature
from app.modules.ai import repository, service
from app.modules.ai.dependencies import get_engine_client
from app.modules.ai.engine_client import FlowEngineClient
from app.modules.ai.schemas import (
    AgentDto,
    CancelRunRequest,
    CreateRunRequest,
    PolicyCallDto,
    RunDetailDto,
    RunSummaryDto,
    SignalCreateRequest,
    SignalCreateResponse,
    StepDto,
    WebhookAckDto,
    WebhookEventRequest,
)
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import TraceStore, get_trace_store

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
