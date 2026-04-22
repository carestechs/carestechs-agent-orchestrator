"""Unit tests for the GitHub Checks effectors (FEAT-008/T-162).

These are narrow — they prove the effector contract (statuses, side
effects, sentinel handling) without driving the full HTTP surface.
FEAT-007 integration tests (``test_feat007_*``) still cover the
end-to-end call-count + header-shape invariants.
"""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.github.checks import (
    NOOP_CHECK_ID,
    GitHubChecksClient,
    HttpxGitHubChecksClient,
    NoopGitHubChecksClient,
)
from app.modules.ai.lifecycle.effectors import EffectorContext
from app.modules.ai.lifecycle.effectors.github import (
    GitHubCheckCreateEffector,
    GitHubCheckUpdateEffector,
)
from app.modules.ai.models import Task, TaskImplementation, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _seed_task_and_impl(
    db: AsyncSession,
    *,
    pr_url: str | None = "https://github.com/a/b/pull/1",
    commit_sha: str = "deadbeef",
    check_id: str | None = None,
) -> tuple[Task, TaskImplementation]:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=f"T-{uuid.uuid4().hex[:6]}",
        title="t",
        status=TaskStatus.IMPL_REVIEW.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.flush()
    impl = TaskImplementation(
        task_id=task.id,
        pr_url=pr_url,
        commit_sha=commit_sha,
        summary="done",
        submitted_by="dev",
        github_check_id=check_id,
    )
    db.add(impl)
    await db.commit()
    await db.refresh(task)
    await db.refresh(impl)
    return task, impl


def _ctx(
    db: AsyncSession,
    *,
    entity_id: uuid.UUID,
    from_state: str = "implementing",
    to_state: str = "impl_review",
    transition: str = "T9",
) -> EffectorContext:
    return EffectorContext(
        entity_type="task",
        entity_id=entity_id,
        from_state=from_state,
        to_state=to_state,
        transition=transition,
        correlation_id=uuid.uuid4(),
        db=db,
        settings=cast("Any", MagicMock()),
    )


def _mock_github() -> Any:
    mock = AsyncMock(spec=HttpxGitHubChecksClient)
    mock.create_check = AsyncMock(return_value="999")
    mock.update_check = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# GitHubCheckCreateEffector
# ---------------------------------------------------------------------------


async def test_create_posts_check_and_persists_id(
    db_session: AsyncSession,
) -> None:
    task, _ = await _seed_task_and_impl(db_session)
    gh = _mock_github()
    effector = GitHubCheckCreateEffector(github=cast("GitHubChecksClient", gh))

    result = await effector.fire(_ctx(db_session, entity_id=task.id))

    assert result.status == "ok"
    assert result.metadata["check_id"] == "999"
    gh.create_check.assert_awaited_once_with(
        owner="a", repo="b", head_sha="deadbeef"
    )


async def test_create_with_noop_client_stores_sentinel(
    db_session: AsyncSession,
) -> None:
    task, impl = await _seed_task_and_impl(db_session)
    effector = GitHubCheckCreateEffector(github=NoopGitHubChecksClient())

    result = await effector.fire(_ctx(db_session, entity_id=task.id))

    assert result.status == "skipped"
    assert result.detail == "noop-github-client"
    await db_session.refresh(impl)
    assert impl.github_check_id == NOOP_CHECK_ID


async def test_create_with_no_implementation_row_skipped(
    db_session: AsyncSession,
) -> None:
    gh = _mock_github()
    effector = GitHubCheckCreateEffector(github=cast("GitHubChecksClient", gh))
    random_task_id = uuid.uuid4()

    result = await effector.fire(_ctx(db_session, entity_id=random_task_id))

    assert result.status == "skipped"
    assert result.detail == "no-task-implementation-row"
    gh.create_check.assert_not_called()


async def test_create_with_no_pr_url_skipped(db_session: AsyncSession) -> None:
    task, _ = await _seed_task_and_impl(db_session, pr_url=None)
    gh = _mock_github()
    effector = GitHubCheckCreateEffector(github=cast("GitHubChecksClient", gh))

    result = await effector.fire(_ctx(db_session, entity_id=task.id))

    assert result.status == "skipped"
    assert result.detail == "no-pr-url"
    gh.create_check.assert_not_called()


async def test_create_with_invalid_pr_url_raises_validation_error(
    db_session: AsyncSession,
) -> None:
    task, _ = await _seed_task_and_impl(
        db_session, pr_url="https://gitlab.com/x/y/pull/1"
    )
    gh = _mock_github()
    effector = GitHubCheckCreateEffector(github=cast("GitHubChecksClient", gh))

    with pytest.raises(ValidationError):
        await effector.fire(_ctx(db_session, entity_id=task.id))

    gh.create_check.assert_not_called()


# ---------------------------------------------------------------------------
# GitHubCheckUpdateEffector
# ---------------------------------------------------------------------------


async def test_update_posts_success_conclusion(db_session: AsyncSession) -> None:
    task, _ = await _seed_task_and_impl(db_session, check_id="777")
    gh = _mock_github()
    effector = GitHubCheckUpdateEffector(
        github=cast("GitHubChecksClient", gh), conclusion="success"
    )

    result = await effector.fire(
        _ctx(
            db_session,
            entity_id=task.id,
            from_state="impl_review",
            to_state="done",
            transition="T10",
        )
    )

    assert result.status == "ok"
    gh.update_check.assert_awaited_once_with(
        owner="a", repo="b", check_id="777", conclusion="success"
    )


async def test_update_with_noop_sentinel_skipped(
    db_session: AsyncSession,
) -> None:
    task, _ = await _seed_task_and_impl(db_session, check_id=NOOP_CHECK_ID)
    gh = _mock_github()
    effector = GitHubCheckUpdateEffector(
        github=cast("GitHubChecksClient", gh), conclusion="success"
    )

    result = await effector.fire(_ctx(db_session, entity_id=task.id))

    assert result.status == "skipped"
    assert result.detail == "noop-github-client"
    gh.update_check.assert_not_called()


async def test_update_with_no_check_id_skipped(
    db_session: AsyncSession,
) -> None:
    task, _ = await _seed_task_and_impl(db_session, check_id=None)
    gh = _mock_github()
    effector = GitHubCheckUpdateEffector(
        github=cast("GitHubChecksClient", gh), conclusion="failure"
    )

    result = await effector.fire(_ctx(db_session, entity_id=task.id))

    assert result.status == "skipped"
    assert result.detail == "no-prior-check"
    gh.update_check.assert_not_called()
