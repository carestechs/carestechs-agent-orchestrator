"""FEAT-019 — GitHub artifact-commit LocalExecutor handlers.

Each public ``make_*_handler`` factory returns an ``async (ctx: DispatchContext)
-> Mapping[str, Any]`` callable suitable for ``LocalExecutor(handler=...)``.

All handlers follow the same pattern:
1. Resolve ``owner/repo`` from the run's ``codeSource`` (IMP-005).
2. Read ``LifecycleMemory`` from ``RunMemory`` (via ``session_factory``).
3. Render the artefact as markdown.
4. Call ``GitHubContentsClient.put_file`` (or ``append_ndjson`` for the event log).
5. Append an ``artifact_committed`` event to ``metrics/events.ndjson``.
6. Return ``{"commitSha": sha, "path": path}`` so the trace carries the location.

When ``github_pat`` is absent or ``codeSource.repo`` is not set on the run,
the handler logs a warning and returns ``{"skipped": True, "reason": ...}``
rather than failing — this lets runs proceed in dev mode without credentials.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ProviderError
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.code_source import read_code_source
from app.modules.ai.models import RunMemory
from app.modules.ai.tools.lifecycle.memory import (
    LifecycleTask,
    WorkItemRef,
    find_current_task,
    read_lifecycle_memory,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def render_brief_markdown(work_item: WorkItemRef) -> str:
    ac_lines = "\n".join(
        f"- {item}" for item in work_item.acceptance_criteria
    ) or "- (none specified)"
    return (
        f"# {work_item.type}-{work_item.id}: {work_item.title}\n\n"
        f"**Status:** Not Started\n"
        f"**Type:** {work_item.type}\n\n"
        f"## Summary\n\n"
        f"{work_item.summary or '(no summary provided)'}\n\n"
        f"## Acceptance Criteria\n\n"
        f"{ac_lines}\n"
    )


def render_task_list_markdown(tasks: list[LifecycleTask], work_item_id: str) -> str:
    if not tasks:
        return f"# Task List — {work_item_id}\n\n(no tasks)\n"
    sections: list[str] = [f"# Task List — {work_item_id}\n"]
    for task in tasks:
        deps = ", ".join(task.depends_on) if task.depends_on else "None"
        files = ", ".join(task.files_hint) if task.files_hint else "None"
        workflow = task.workflow or ("mockup-first" if task.kind == "mockup" else "standard")
        sections.append(
            f"## {task.id}: {task.title}\n"
            f"- **Kind:** {task.kind}\n"
            f"- **Complexity:** {task.complexity}\n"
            f"- **Workflow:** {workflow}\n"
            f"- **Dependencies:** {deps}\n"
            f"- **Files to modify:** {files}\n"
            f"- **Description:** {task.description or '(none)'}\n"
        )
    return "\n".join(sections)


def render_plan_markdown(task: LifecycleTask, plan_text: str) -> str:
    return f"# Plan: {task.id} — {task.title}\n\n{plan_text.strip()}\n"


def render_review_markdown(
    task: LifecycleTask,
    verdict: str,
    reviewer: str,
    notes: str | None,
) -> str:
    return (
        f"# Implementation Review: {task.id}\n\n"
        f"## Verdict\n\n{verdict}\n\n"
        f"## Reviewer\n\n{reviewer}\n\n"
        f"## Task\n\n{task.title}\n\n"
        f"## Notes\n\n{notes or '(no notes)'}\n"
    )


def render_event_line(
    *,
    run_id: str,
    agent_ref: str,
    event: str,
    step: str,
    work_item_id: str | None,
    task_id: str | None,
    detail: str | None,
) -> str:
    return json.dumps(
        {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "run_id": run_id,
            "agent_ref": agent_ref,
            "step": step,
            "event": event,
            "work_item_id": work_item_id,
            "task_id": task_id,
            "detail": detail,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_plan_text(mem_data: dict[str, Any], task_id: str) -> str:
    """Read ``plans[task_id].plan_markdown`` from the top-level RunMemory.data dict."""
    plans_raw = mem_data.get("plans")
    if not isinstance(plans_raw, dict):
        return ""
    from typing import cast
    entry_raw = cast("dict[str, Any]", plans_raw).get(task_id)
    if not isinstance(entry_raw, dict):
        return ""
    return str(cast("dict[str, Any]", entry_raw).get("plan_markdown") or "")


async def _load_mem_data(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: DispatchContext,
) -> dict[str, Any]:
    async with session_factory() as session:
        mem = await session.scalar(
            select(RunMemory).where(RunMemory.run_id == ctx.run_id)
        )
    return (mem.data if mem is not None else {}) or {}


def _owner_repo(ctx: DispatchContext) -> tuple[str, str] | None:
    try:
        code_source = read_code_source(ctx)
    except (ValueError, Exception):
        return None
    if not code_source.repo:
        return None
    owner, sep, repo = code_source.repo.partition("/")
    if not sep:
        return None
    return owner, repo


async def _append_event_log(
    client: Any,
    owner: str,
    repo: str,
    *,
    run_id: str,
    agent_ref: str,
    event: str,
    step: str,
    work_item_id: str | None,
    task_id: str | None,
    detail: str | None,
) -> None:
    line = render_event_line(
        run_id=run_id,
        agent_ref=agent_ref,
        event=event,
        step=step,
        work_item_id=work_item_id,
        task_id=task_id,
        detail=detail,
    )
    try:
        await client.append_ndjson(
            owner=owner,
            repo=repo,
            path="metrics/events.ndjson",
            line=line,
            message=f"events({work_item_id or run_id}): {event} at {step}",
        )
    except ProviderError as exc:
        logger.warning("event log write failed (non-fatal): %s", exc)


def _make_client(pat: str, branch: str, http: httpx.AsyncClient) -> Any:
    from app.modules.ai.github.auth import PatAuthStrategy
    from app.modules.ai.github.contents import GitHubContentsClient

    return GitHubContentsClient(auth=PatAuthStrategy(pat), http=http, branch=branch)


# ---------------------------------------------------------------------------
# Handler factories
# ---------------------------------------------------------------------------


def make_commit_brief_handler(
    session_factory: async_sessionmaker[AsyncSession],
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: commit the work-item brief after ``confirm_brief``."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set on run intake"}
        owner, repo = coords

        mem_data = await _load_mem_data(session_factory, ctx)
        memory = read_lifecycle_memory(mem_data)
        if memory.work_item is None:
            return {"skipped": True, "reason": "work_item not in memory yet"}

        wi = memory.work_item
        content = render_brief_markdown(wi)
        path = f"docs/work-items/{wi.id}-{_slug(wi.title)}.md"
        message = f"docs({wi.id}): brief"

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            sha = await client.put_file(
                owner=owner, repo=repo, path=path, content=content, message=message
            )
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="artifact_committed", step="commit_brief",
                work_item_id=wi.id, task_id=None, detail=f"{path}@{sha[:7]}",
            )
        return {"commitSha": sha, "path": path}

    return _handler


def make_commit_tasks_handler(
    session_factory: async_sessionmaker[AsyncSession],
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: commit the task list after ``confirm_task_review``."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set on run intake"}
        owner, repo = coords

        mem_data = await _load_mem_data(session_factory, ctx)
        memory = read_lifecycle_memory(mem_data)
        wi_id = memory.work_item.id if memory.work_item else "unknown"
        content = render_task_list_markdown(memory.tasks, wi_id)
        path = f"tasks/{wi_id}-tasks.md"
        message = f"tasks({wi_id}): generate task list"

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            sha = await client.put_file(
                owner=owner, repo=repo, path=path, content=content, message=message
            )
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="artifact_committed", step="commit_tasks",
                work_item_id=wi_id, task_id=None, detail=f"{path}@{sha[:7]}",
            )
        return {"commitSha": sha, "path": path}

    return _handler


def make_commit_plan_handler(
    session_factory: async_sessionmaker[AsyncSession],
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: commit the plan file after ``confirm_plan``."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set on run intake"}
        owner, repo = coords

        mem_data = await _load_mem_data(session_factory, ctx)
        memory = read_lifecycle_memory(mem_data)
        task = find_current_task(memory)
        if task is None:
            return {"skipped": True, "reason": "no current_task_id in memory"}

        plan_text = _resolve_plan_text(mem_data, task.id)
        content = render_plan_markdown(task, plan_text)
        path = f"plans/plan-{task.id}-{_slug(task.title)}.md"
        message = f"plan({task.id}): implementation plan"

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            sha = await client.put_file(
                owner=owner, repo=repo, path=path, content=content, message=message
            )
            wi_id = memory.work_item.id if memory.work_item else None
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="artifact_committed", step="commit_plan",
                work_item_id=wi_id, task_id=task.id, detail=f"{path}@{sha[:7]}",
            )
        return {"commitSha": sha, "path": path}

    return _handler


def make_commit_review_handler(
    session_factory: async_sessionmaker[AsyncSession],
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: commit the implementation review after ``human_review_implementation``."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set on run intake"}
        owner, repo = coords

        mem_data = await _load_mem_data(session_factory, ctx)
        memory = read_lifecycle_memory(mem_data)
        task = find_current_task(memory)
        if task is None:
            return {"skipped": True, "reason": "no current_task_id in memory"}

        history = [r for r in memory.review_history if r.task_id == task.id]
        verdict = history[-1].verdict if history else "pass"
        content = render_review_markdown(task, verdict, reviewer="operator", notes=None)
        path = f"tasks/{task.id}-implementation-review.md"
        message = f"review({task.id}): implementation {verdict}"

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            sha = await client.put_file(
                owner=owner, repo=repo, path=path, content=content, message=message
            )
            wi_id = memory.work_item.id if memory.work_item else None
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="artifact_committed", step="commit_review",
                work_item_id=wi_id, task_id=task.id, detail=f"{path}@{sha[:7]}",
            )
        return {"commitSha": sha, "path": path, "verdict": verdict}

    return _handler


def make_log_run_started_handler(
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: write the ``started`` event immediately after ``start``."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set"}
        owner, repo = coords

        wi_id: str | None = None
        raw_wi: Any = ctx.intake.get("workItem")
        if isinstance(raw_wi, dict):
            from typing import cast as _cast
            typed_wi = _cast("dict[str, Any]", raw_wi)
            raw_id = typed_wi.get("id")
            if isinstance(raw_id, str) and raw_id:
                wi_id = raw_id

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="started", step="start",
                work_item_id=wi_id, task_id=None, detail=None,
            )
        return {"logged": True}

    return _handler


def make_log_run_completed_handler(
    session_factory: async_sessionmaker[AsyncSession],
    pat: str | None,
    branch: str,
    agent_ref: str,
) -> Any:
    """LocalExecutor handler: write the ``completed`` event at end of run."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not pat:
            return {"skipped": True, "reason": "GITHUB_PAT not configured"}
        coords = _owner_repo(ctx)
        if coords is None:
            return {"skipped": True, "reason": "codeSource.repo not set"}
        owner, repo = coords

        mem_data = await _load_mem_data(session_factory, ctx)
        memory = read_lifecycle_memory(mem_data)
        wi_id = memory.work_item.id if memory.work_item else None

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = _make_client(pat, branch, http_client)
            await _append_event_log(
                client, owner, repo,
                run_id=str(ctx.run_id), agent_ref=agent_ref,
                event="completed", step="log_run_completed",
                work_item_id=wi_id, task_id=None, detail=None,
            )
        return {"logged": True}

    return _handler


__all__ = [
    "make_commit_brief_handler",
    "make_commit_plan_handler",
    "make_commit_review_handler",
    "make_commit_tasks_handler",
    "make_log_run_completed_handler",
    "make_log_run_started_handler",
    "render_brief_markdown",
    "render_event_line",
    "render_plan_markdown",
    "render_review_markdown",
    "render_task_list_markdown",
]
