"""FEAT-014 / T-284 — register_work_item state machine tests.

Real-Postgres fixture (CLAUDE.md: no SQL mocks).  Each test isolates on
a unique ``external_ref`` and cleans up after itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import (
    WorkItemContentConflictError,
    WorkItemKindConflictError,
    WorkItemNotRegisteredError,
)
from app.modules.ai.enums import WorkItemStatus, WorkItemType
from app.modules.ai.lifecycle.work_item_registry import (
    _derive_title,
    register_work_item,
)
from app.modules.ai.models import WorkItem
from app.modules.ai.schemas import RunIntakeWorkItem


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def isolated_ref(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    ref = f"FEAT-{uuid.uuid4().int % 1_000_000_000}"
    yield ref
    async with session_factory() as session, session.begin():
        await session.execute(delete(WorkItem).where(WorkItem.external_ref == ref))


# ---------------------------------------------------------------------------
# State-machine branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_sight_inserts_row(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 2: no row + content → INSERT."""
    body = "# Test\nsome body"
    expected_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    async with session_factory() as session, session.begin():
        wi = await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content=body),
        )
        assert wi.external_ref == isolated_ref
        assert wi.body_md == body
        assert wi.body_sha256 == expected_sha
        assert wi.type == "FEAT"
        assert wi.title == "Test"
        assert wi.status == WorkItemStatus.OPEN.value
        assert wi.opened_by == "upload"


@pytest.mark.asyncio
async def test_no_content_no_row_raises(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 1: no row + no content → WorkItemNotRegisteredError."""
    async with session_factory() as session, session.begin():
        with pytest.raises(WorkItemNotRegisteredError, match=isolated_ref):
            await register_work_item(
                session,
                RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT),
            )


@pytest.mark.asyncio
async def test_no_content_with_row_reuses(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 3: row exists + no content → reuse."""
    async with session_factory() as session, session.begin():
        first = await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content="# A\nbody"),
        )
        first_id = first.id

    async with session_factory() as session, session.begin():
        second = await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT),
        )
        assert second.id == first_id


@pytest.mark.asyncio
async def test_matching_content_returns_existing(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 5: row + content with matching sha → idempotent (no UPDATE)."""
    body = "# Stable\nbody"
    async with session_factory() as session, session.begin():
        first = await register_work_item(
            session, RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content=body)
        )
        first_id = first.id
        original_title = first.title

    async with session_factory() as session, session.begin():
        second = await register_work_item(
            session, RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content=body)
        )
        assert second.id == first_id
        assert second.title == original_title


@pytest.mark.asyncio
async def test_mismatched_content_raises(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 7: row + content with different sha → 409 with diff metadata."""
    async with session_factory() as session, session.begin():
        await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content="# v1"),
        )

    async with session_factory() as session, session.begin():
        with pytest.raises(WorkItemContentConflictError) as exc_info:
            await register_work_item(
                session,
                RunIntakeWorkItem(
                    id=isolated_ref, kind=WorkItemType.FEAT, content="# v2 different"
                ),
            )
        assert exc_info.value.stored_sha256 != exc_info.value.uploaded_sha256
        assert exc_info.value.meta["storedSha256"] == exc_info.value.stored_sha256


@pytest.mark.asyncio
async def test_kind_mismatch_raises(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 4: kind mismatch → 409 (no content read past the kind check)."""
    async with session_factory() as session, session.begin():
        await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content="# body"),
        )

    async with session_factory() as session, session.begin():
        with pytest.raises(WorkItemKindConflictError) as exc_info:
            await register_work_item(
                session,
                RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.BUG, content="# body"),
            )
        assert exc_info.value.stored_kind == "FEAT"
        assert exc_info.value.uploaded_kind == "BUG"


@pytest.mark.asyncio
async def test_backfills_null_sha256(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Branch 6: pre-FEAT-014 row (body NULL) → backfill."""
    async with session_factory() as session, session.begin():
        session.add(
            WorkItem(
                external_ref=isolated_ref,
                type="FEAT",
                title="placeholder",
                source_path="docs/work-items/whatever.md",
                opened_by="legacy",
                status=WorkItemStatus.OPEN.value,
            )
        )

    new_body = "# Backfilled\nbody"
    expected_sha = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    async with session_factory() as session, session.begin():
        wi = await register_work_item(
            session,
            RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content=new_body),
        )
        assert wi.body_md == new_body
        assert wi.body_sha256 == expected_sha


@pytest.mark.asyncio
async def test_concurrent_first_sight_race(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """Two coroutines first-sight the same ref with identical content.
    The IntegrityError branch re-reads; only one row exists; no exceptions."""
    body = "# Race\nbody"

    async def call() -> WorkItem:
        async with session_factory() as session, session.begin():
            return await register_work_item(
                session,
                RunIntakeWorkItem(id=isolated_ref, kind=WorkItemType.FEAT, content=body),
            )

    results = await asyncio.gather(call(), call())
    assert results[0].id == results[1].id

    async with session_factory() as session:
        count = (
            await session.execute(
                select(WorkItem).where(WorkItem.external_ref == isolated_ref)
            )
        ).scalars().all()
        assert len(count) == 1


@pytest.mark.asyncio
async def test_sha256_no_normalization_crlf_vs_lf(
    session_factory: async_sessionmaker[AsyncSession],
    isolated_ref: str,
) -> None:
    """sha256 is computed over bytes-as-received; CRLF and LF produce
    different shas and the second register attempt raises 409."""
    async with session_factory() as session, session.begin():
        await register_work_item(
            session,
            RunIntakeWorkItem(
                id=isolated_ref, kind=WorkItemType.FEAT, content="# A\nB\n"
            ),
        )

    async with session_factory() as session, session.begin():
        with pytest.raises(WorkItemContentConflictError):
            await register_work_item(
                session,
                RunIntakeWorkItem(
                    id=isolated_ref, kind=WorkItemType.FEAT, content="# A\r\nB\r\n"
                ),
            )


# ---------------------------------------------------------------------------
# _derive_title cases
# ---------------------------------------------------------------------------


class TestDeriveTitle:
    def test_h1_at_top(self) -> None:
        assert _derive_title("# Hello\nbody", fallback="X") == "Hello"

    def test_h1_after_blank_lines(self) -> None:
        assert _derive_title("\n\n# Hello\nbody", fallback="X") == "Hello"

    def test_h1_with_trailing_whitespace(self) -> None:
        assert _derive_title("#   Spaced  \nbody", fallback="X") == "Spaced"

    def test_only_h2_returns_fallback(self) -> None:
        assert _derive_title("## H2 only\nbody", fallback="FEAT-1") == "FEAT-1"

    def test_no_headings_returns_fallback(self) -> None:
        assert _derive_title("just body", fallback="FEAT-7") == "FEAT-7"

    def test_empty_body_returns_fallback(self) -> None:
        assert _derive_title("", fallback="FEAT-9") == "FEAT-9"
