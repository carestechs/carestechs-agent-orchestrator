"""Shared infrastructure for FEAT-002 integration tests.

These helpers bypass the unit-test ``app``/``db_session`` fixtures because
the runtime loop opens its own :class:`AsyncSession` per iteration via
``get_session_factory`` — which is bound to the raw engine, not to the
savepoint-wrapped connection the unit tests use.

The usual flow for a test is::

    async with integration_env(
        engine,
        agents_dir=tmp_path / "agents",
        trace_dir=tmp_path / "trace",
        policy_script=[("analyze_brief", {"brief": "hi"}), ...],
        webhook_signer=webhook_signer,
    ) as env:
        resp = await env.client.post(...)
        ...

The context manager enters the app lifespan, applies the dep overrides,
hands over an ``httpx.AsyncClient``, and on exit drains the supervisor +
the :class:`EngineEcho`.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.config import Settings, get_settings
from app.core.dependencies import (
    get_llm_provider_dep,
    get_session_factory,
    get_settings_dep,
)
from app.core.llm import LLMProvider, ScriptedCall, StubLLMProvider
from app.main import create_app
from app.modules.ai.dependencies import get_engine_client
from app.modules.ai.enums import RunStatus
from app.modules.ai.models import (
    PolicyCall,
    Run,
    RunMemory,
    RunSignal,
    Step,
    WebhookEvent,
)
from app.modules.ai.trace import get_trace_store
from app.modules.ai.trace_jsonl import JsonlTraceStore

from .engine_echo import EngineEcho

_SAMPLE_AGENT = (
    Path(__file__).parent.parent / "fixtures" / "agents" / "sample-linear.yaml"
)


@dataclass
class IntegrationEnv:
    client: AsyncClient
    app: Any
    engine_echo: EngineEcho
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    auth_headers: dict[str, str]
    run_ids: list[uuid.UUID] = field(default_factory=list)


def prepare_agents_dir(dst: Path, *, include_sample: bool = True) -> Path:
    """Populate *dst* with the sample-linear agent fixture."""
    dst.mkdir(parents=True, exist_ok=True)
    if include_sample:
        shutil.copy(_SAMPLE_AGENT, dst / "sample-linear@1.0.yaml")
    return dst


@contextlib.asynccontextmanager
async def integration_env(
    engine: AsyncEngine,
    *,
    agents_dir: Path,
    trace_dir: Path,
    policy_script: Sequence[ScriptedCall],
    webhook_signer: Callable[[bytes], str],
    api_key: str,
    engine_delay_seconds: float = 0.0,
    fail_on_step_number: int | None = None,
    fail_with: Exception | None = None,
    payload_for: Callable[[int, str], dict[str, Any]] | None = None,
    policy: LLMProvider | None = None,
    settings_extra: dict[str, Any] | None = None,
) -> AsyncIterator[IntegrationEnv]:
    """Spin up a fresh ASGI app wired with stubs / respx-mocked providers.

    *policy* overrides *policy_script*: pass a pre-built provider (e.g. a
    real :class:`AnthropicLLMProvider` with respx-mocked responses) to run
    the loop against it.  When *policy* is ``None`` the test uses the
    default :class:`StubLLMProvider` built from *policy_script*.
    """
    app = create_app()
    agents_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    settings_update: dict[str, Any] = {
        "agents_dir": agents_dir,
        "trace_dir": trace_dir,
    }
    if settings_extra:
        settings_update.update(settings_extra)
    settings = get_settings().model_copy(update=settings_update)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    engine_echo = EngineEcho(
        app,
        webhook_signer,
        delay_seconds=engine_delay_seconds,
        fail_on_step_number=fail_on_step_number,
        fail_with=fail_with,
        payload_for=payload_for,
    )
    resolved_policy: LLMProvider = (
        policy if policy is not None else StubLLMProvider(list(policy_script))
    )

    trace_store = JsonlTraceStore(trace_dir)

    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_llm_provider_dep] = lambda: resolved_policy
    app.dependency_overrides[get_engine_client] = lambda: engine_echo
    app.dependency_overrides[get_trace_store] = lambda: trace_store

    env = IntegrationEnv(
        client=AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ),
        app=app,
        engine_echo=engine_echo,
        session_factory=session_factory,
        settings=settings,
        auth_headers={"Authorization": f"Bearer {api_key}"},
    )

    async with app.router.lifespan_context(app):
        try:
            yield env
        finally:
            # Drain any in-flight run so the DB doesn't hold a row in ``running``.
            supervisor = getattr(app.state, "supervisor", None)
            if supervisor is not None:
                await supervisor.shutdown(grace=1.0)
            await env.client.aclose()
            await engine_echo.aclose()
            await _cleanup_rows(session_factory, env.run_ids)


async def _cleanup_rows(
    factory: async_sessionmaker[AsyncSession], run_ids: list[uuid.UUID]
) -> None:
    if not run_ids:
        return
    async with factory() as session:
        for rid in run_ids:
            await session.execute(
                WebhookEvent.__table__.delete().where(WebhookEvent.run_id == rid)
            )
            await session.execute(
                RunSignal.__table__.delete().where(RunSignal.run_id == rid)
            )
            await session.execute(
                PolicyCall.__table__.delete().where(PolicyCall.run_id == rid)
            )
            await session.execute(Step.__table__.delete().where(Step.run_id == rid))
            await session.execute(
                RunMemory.__table__.delete().where(RunMemory.run_id == rid)
            )
            await session.execute(Run.__table__.delete().where(Run.id == rid))
        await session.commit()


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------


async def poll_until_terminal(
    env: IntegrationEnv,
    run_id: uuid.UUID,
    *,
    timeout_seconds: float = 5.0,
    interval: float = 0.05,
) -> Run:
    """Poll the DB until *run_id* reaches a terminal status or we time out."""
    import asyncio
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with env.session_factory() as session:
            run = await session.scalar(select(Run).where(Run.id == run_id))
        if run is not None and RunStatus(run.status) in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run
        await asyncio.sleep(interval)

    # Timed out — fetch extra context so the failure message is useful.
    async with env.session_factory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id))
        steps = list(
            (
                await session.execute(
                    select(Step).where(Step.run_id == run_id).order_by(Step.step_number)
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(WebhookEvent).where(WebhookEvent.run_id == run_id)
                )
            ).scalars()
        )
    status = run.status if run is not None else "missing"
    step_summary = [(s.step_number, s.node_name, s.status) for s in steps]
    event_summary = [(e.event_type, e.signature_ok) for e in events]
    raise AssertionError(
        f"run {run_id} did not reach a terminal status within {timeout_seconds}s; "
        f"status={status!r}; dispatches={len(env.engine_echo.dispatches)}; "
        f"steps={step_summary}; webhook_events={event_summary}"
    )
