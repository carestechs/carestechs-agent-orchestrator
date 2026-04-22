"""GitHub Checks effectors (FEAT-008/T-162).

Relocates the inline ``_post_create_check`` / ``_post_update_check``
helpers from ``lifecycle/service.py`` into two first-class effectors:

* :class:`GitHubCheckCreateEffector` — fires on T9
  (``implementing → impl_review``). Creates the ``orchestrator/impl-review``
  check-run on the PR, persists the resulting ``check_id`` on the latest
  ``TaskImplementation``, or stores ``NOOP_CHECK_ID`` when the configured
  client is a no-op.

* :class:`GitHubCheckUpdateEffector` — fires on T10 (``impl_review → done``,
  ``conclusion=success``) and T11 (``impl_review → implementing``,
  ``conclusion=failure``). Updates the prior check with the terminal
  conclusion; no-ops on the noop sentinel or when the URL is missing.

Behaviour is identical to the inline helpers — the seam moves; the
merge-gating story from FEAT-007 is preserved verbatim. Tests in
``test_feat007_*`` pass unchanged.
"""

from __future__ import annotations

import logging
import uuid
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ValidationError
from app.modules.ai.github.checks import (
    NOOP_CHECK_ID,
    CheckConclusion,
    GitHubChecksClient,
    NoopGitHubChecksClient,
)
from app.modules.ai.github.pr_urls import parse_pr_url
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)
from app.modules.ai.models import TaskImplementation

logger = logging.getLogger(__name__)


async def _latest_task_impl(
    db: AsyncSession, task_id: uuid.UUID
) -> TaskImplementation | None:
    return await db.scalar(
        select(TaskImplementation)
        .where(TaskImplementation.task_id == task_id)
        .order_by(TaskImplementation.submitted_at.desc())
        .limit(1)
    )


class GitHubCheckCreateEffector:
    """Creates the merge-gating check on the PR that carries the impl."""

    name: ClassVar[str] = "github_check_create"

    def __init__(self, github: GitHubChecksClient) -> None:
        self._github = github

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        db = ctx.db
        impl = await _latest_task_impl(db, ctx.entity_id)
        if impl is None:
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=0,
                detail="no-task-implementation-row",
            )

        if isinstance(self._github, NoopGitHubChecksClient):
            impl.github_check_id = NOOP_CHECK_ID
            await db.commit()
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=0,
                detail="noop-github-client",
            )

        if impl.pr_url is None:
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=0,
                detail="no-pr-url",
            )

        try:
            ref = parse_pr_url(impl.pr_url)
        except ValidationError:
            # Malformed URL with a real client configured is the caller's
            # problem; bubble up so the signal handler returns 400.
            raise

        commit_sha = impl.commit_sha
        try:
            check_id = await self._github.create_check(
                owner=ref.owner, repo=ref.repo, head_sha=commit_sha
            )
        except AppError as exc:
            logger.warning(
                "github check create failed; state machine continues",
                extra={
                    "task_id": str(ctx.entity_id),
                    "repo": ref.slug,
                    "error_code": exc.code,
                    "error": str(exc),
                },
            )
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=0,
                error_code=exc.code,
                detail=str(exc),
            )

        impl.github_check_id = check_id
        await db.commit()
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=0,
            metadata={"check_id": check_id, "repo": ref.slug},
        )


class GitHubCheckUpdateEffector:
    """Flips the prior check to success or failure on review outcome."""

    name: ClassVar[str] = "github_check_update"

    def __init__(
        self,
        github: GitHubChecksClient,
        conclusion: CheckConclusion,
    ) -> None:
        self._github = github
        self._conclusion: CheckConclusion = conclusion

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        db = ctx.db
        impl = await _latest_task_impl(db, ctx.entity_id)
        if impl is None or impl.github_check_id is None or impl.pr_url is None:
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=0,
                detail="no-prior-check",
            )
        if impl.github_check_id == NOOP_CHECK_ID or isinstance(
            self._github, NoopGitHubChecksClient
        ):
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=0,
                detail="noop-github-client",
            )
        try:
            ref = parse_pr_url(impl.pr_url)
        except ValidationError:
            logger.warning(
                "stored pr_url is invalid; skipping check update",
                extra={"task_id": str(ctx.entity_id), "pr_url": impl.pr_url},
            )
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=0,
                error_code="invalid-pr-url",
                detail=impl.pr_url,
            )
        try:
            await self._github.update_check(
                owner=ref.owner,
                repo=ref.repo,
                check_id=impl.github_check_id,
                conclusion=self._conclusion,
            )
        except AppError as exc:
            logger.warning(
                "github check update failed; state machine continues",
                extra={
                    "task_id": str(ctx.entity_id),
                    "check_id": impl.github_check_id,
                    "conclusion": self._conclusion,
                    "error_code": exc.code,
                    "error": str(exc),
                },
            )
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=0,
                error_code=exc.code,
                detail=str(exc),
            )
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=0,
            metadata={
                "check_id": impl.github_check_id,
                "conclusion": self._conclusion,
                "repo": ref.slug,
            },
        )
