"""GET /health endpoint with dependency-check chain."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.envelope import Envelope, envelope
from app.modules.ai.dependencies import get_engine_client
from app.modules.ai.engine_client import FlowEngineClient

router = APIRouter()


async def _check_database(db: AsyncSession) -> str:
    try:
        await db.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "down"


async def _check_llm_provider() -> str:
    # Stub provider is always "ok"; real check deferred to post-v1.
    return "ok"


async def _check_flow_engine(engine: FlowEngineClient) -> str:
    ok = await engine.health()
    return "ok" if ok else "down"


@router.get("/health")
async def health(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    engine: Annotated[FlowEngineClient, Depends(get_engine_client)],
) -> Envelope[dict[str, Any]]:
    """Liveness and dependency readiness.  Unauthenticated."""
    checks = {
        "database": await _check_database(db),
        "llm_provider": await _check_llm_provider(),
        "flow_engine": await _check_flow_engine(engine),
    }
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return envelope({"status": overall, "checks": checks})
