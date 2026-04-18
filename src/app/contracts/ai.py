"""IAIService protocol for cross-module callers (ref spec for T-051).

The concrete implementation lives in :mod:`app.modules.ai.service` as
module-level ``async def`` functions rather than methods on a class.
This Protocol is therefore a *reference* contract, not a runtime
``isinstance`` check: the drift guard in
``tests/modules/ai/test_service_contracts.py`` asserts that every public
service function still exposes the parameter names declared here.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.llm import LLMProvider
from app.modules.ai.engine_client import FlowEngineClient
from app.modules.ai.schemas import (
    AgentDto,
    CancelRunRequest,
    CreateRunRequest,
    PolicyCallDto,
    RunDetailDto,
    RunSummaryDto,
    StepDto,
    WebhookEventDto,
)
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import TraceStore


class IAIService(Protocol):
    """Contract for the AI module's public service surface."""

    async def start_run(
        self,
        request: CreateRunRequest,
        *,
        settings: Settings,
        supervisor: RunSupervisor,
        session_factory: async_sessionmaker[AsyncSession],
        policy: LLMProvider,
        engine: FlowEngineClient,
        trace: TraceStore,
    ) -> RunSummaryDto: ...

    async def list_runs(
        self,
        db: AsyncSession,
        *,
        status: str | None = None,
        agent_ref: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[RunSummaryDto], int]: ...

    async def get_run(
        self,
        run_id: uuid.UUID,
        db: AsyncSession,
    ) -> RunDetailDto: ...

    async def cancel_run(
        self,
        run_id: uuid.UUID,
        request: CancelRunRequest,
        db: AsyncSession,
        *,
        supervisor: RunSupervisor,
    ) -> RunSummaryDto: ...

    async def list_steps(
        self,
        run_id: uuid.UUID,
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[StepDto], int]: ...

    async def list_policy_calls(
        self,
        run_id: uuid.UUID,
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[PolicyCallDto], int]: ...

    async def list_agents(self, *, settings: Settings) -> list[AgentDto]: ...

    async def ingest_engine_event(
        self,
        event_body: dict[str, Any],
        signature_ok: bool,
        db: AsyncSession,
        supervisor: RunSupervisor | None = None,
        trace: TraceStore | None = None,
    ) -> WebhookEventDto: ...
