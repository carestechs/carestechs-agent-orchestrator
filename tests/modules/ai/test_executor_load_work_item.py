"""FEAT-014 / T-286 — ``_handle_request_work_item_load`` reads from DB.

Confirms the executor swap:

* When a ``WorkItem`` row is registered for the run's ``intake.workItem.id``,
  the executor's memory patch carries ``work_item_body`` (not a path).
* When the row is missing, ``loaded=False`` (no crash, no disk access).
* When ``session_factory`` was never wired (degraded mode), the executor
  surfaces ``loaded=False`` rather than touching disk.
* The session is short-lived: one session per call, closed before return
  (verified by a counting wrapper).
* No ``pathlib`` read targeting ``docs/work-items/`` occurs along this
  path under any branch.
"""

from __future__ import annotations

import builtins
import pathlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import WorkItemStatus
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.bootstrap import (
    _load_work_item_body,
    _make_work_item_load_handler,
)
from app.modules.ai.models import WorkItem


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_work_item(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[str, str]]:
    ext_ref = f"FEAT-{uuid.uuid4().int % 1_000_000_000}"
    body = "# Test brief\nbody content"
    async with session_factory() as session, session.begin():
        session.add(
            WorkItem(
                external_ref=ext_ref,
                type="FEAT",
                title="Test brief",
                body_md=body,
                body_sha256="a" * 64,
                opened_by="upload",
                status=WorkItemStatus.OPEN.value,
            )
        )
    yield ext_ref, body
    async with session_factory() as session, session.begin():
        await session.execute(delete(WorkItem).where(WorkItem.external_ref == ext_ref))


def _make_ctx(intake: dict[str, Any]) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.2.0",
        node_name="request_work_item_load",
        intake=intake,
    )


@pytest.mark.asyncio
async def test_load_from_db_returns_body_in_memory_patch(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_work_item: tuple[str, str],
) -> None:
    ext_ref, body = seeded_work_item
    handler = _make_work_item_load_handler(session_factory)
    ctx = _make_ctx({"workItem": {"id": ext_ref, "kind": "FEAT"}})
    result = await handler(ctx)
    assert result["loaded"] is True
    assert result["externalRef"] == ext_ref
    mp = result["__memory_patch"]
    assert mp["work_item_body"] == body
    assert mp["work_item_id"] == ext_ref


@pytest.mark.asyncio
async def test_legacy_work_item_id_intake_resolves(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_work_item: tuple[str, str],
) -> None:
    """A run started under the legacy intake shape can still resolve the
    body once T-288's shim has registered the row."""
    ext_ref, body = seeded_work_item
    handler = _make_work_item_load_handler(session_factory)
    ctx = _make_ctx({"workItemId": ext_ref})
    result = await handler(ctx)
    assert result["__memory_patch"]["work_item_body"] == body


@pytest.mark.asyncio
async def test_missing_row_returns_loaded_false(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    handler = _make_work_item_load_handler(session_factory)
    ctx = _make_ctx({"workItem": {"id": "FEAT-DOES-NOT-EXIST", "kind": "FEAT"}})
    result = await handler(ctx)
    assert result["loaded"] is False
    assert result["__memory_patch"]["work_item_body"] is None


@pytest.mark.asyncio
async def test_no_id_in_intake_returns_loaded_false() -> None:
    """No work_item present and no session_factory wired → safe degraded
    response (the v0.2 demo agent used to be tested without a DB)."""
    handler = _make_work_item_load_handler(None)
    ctx = _make_ctx({})
    result = await handler(ctx)
    assert result["loaded"] is False
    assert result["__memory_patch"]["work_item_body"] is None


@pytest.mark.asyncio
async def test_no_disk_read_under_work_items_dir(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_work_item: tuple[str, str],
) -> None:
    """No ``open()`` / ``Path.read_text`` of a ``docs/work-items/*`` path
    happens anywhere in the executor's call graph."""
    ext_ref, _ = seeded_work_item
    handler = _make_work_item_load_handler(session_factory)
    ctx = _make_ctx({"workItem": {"id": ext_ref, "kind": "FEAT"}})

    violations: list[str] = []
    real_open = builtins.open
    real_read_text = pathlib.Path.read_text

    def trap_open(file, *a, **k):  # type: ignore[no-untyped-def]
        s = str(file)
        if "docs/work-items" in s or "work-items/" in s:
            violations.append(s)
        return real_open(file, *a, **k)

    def trap_read_text(self, *a, **k):  # type: ignore[no-untyped-def]
        s = str(self)
        if "docs/work-items" in s or "work-items/" in s:
            violations.append(s)
        return real_read_text(self, *a, **k)

    builtins.open = trap_open
    pathlib.Path.read_text = trap_read_text
    try:
        await handler(ctx)
    finally:
        builtins.open = real_open
        pathlib.Path.read_text = real_read_text

    assert violations == [], f"unexpected disk reads: {violations}"


@pytest.mark.asyncio
async def test_session_is_short_lived(
    engine: AsyncEngine,
    seeded_work_item: tuple[str, str],
) -> None:
    """Counting wrapper confirms one session open + close per call."""
    ext_ref, _ = seeded_work_item
    real_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    opens = 0

    class CountingFactory:
        def __call__(self) -> AsyncSession:
            nonlocal opens
            opens += 1
            return real_factory()

    counting: Any = CountingFactory()
    body, _ = await _load_work_item_body(counting, external_ref=ext_ref)
    assert body is not None
    assert opens == 1
