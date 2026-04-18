"""End-to-end lifecycle agent test with the stub policy (FEAT-005 / T-101).

Drives ``lifecycle-agent@0.1.0`` through all 8 stages with a deterministic
stub policy.  Proves AD-3 composition integrity for the lifecycle agent:
with the LLM removed, the flow still completes and produces real
artifacts in the temp repo.

The implementation-stage signal is delivered via
:meth:`RunSupervisor.deliver_signal` directly (the endpoint path is
exercised separately in T-098's route tests).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.core.llm import ScriptedCall
from tests.conftest import API_KEY

from .env import integration_env
from .lifecycle_helpers import (
    deliver_signal_when_paused,
    prepare_repo,
    wait_for_terminal,
)

_MINIMAL_TASKS_DOC = """# Task Breakdown: IMP-fixture

### T-001: Trivial fixture task
**Type:** Testing
Body.
"""

_MINIMAL_PLAN_DOC = """# Implementation Plan: T-001

## Overview
Fixture plan.

## Steps
1. Do the thing.
"""


@pytest.mark.asyncio(loop_scope="function")
async def test_lifecycle_agent_stub_end_to_end(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = prepare_repo(tmp_path)

    # Stub out `git diff`: tmp_path is not a git repo.  The lifecycle tools
    # that reach this helper only need a non-empty string.
    def _stub_get_diff(*_args: object, **_kwargs: object) -> str:
        return "diff --git a/x b/x\n+hello\n"

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.git.get_diff",
        _stub_get_diff,
    )

    # Point `get_settings()` (cached singleton used by the local tools) at
    # the tmp repo root.  The DI-level override via `settings_extra` takes
    # care of the runtime's own settings.
    monkeypatch.setenv("REPO_ROOT", str(repo.root))
    get_settings.cache_clear()

    script: list[ScriptedCall] = [
        (
            "load_work_item",
            {"path": "docs/work-items/IMP-fixture.md"},
        ),
        (
            "generate_tasks",
            {
                "work_item_id": "IMP-fixture",
                "tasks_markdown": _MINIMAL_TASKS_DOC,
            },
        ),
        ("assign_task", {"task_id": "T-001"}),
        (
            "generate_plan",
            {
                "task_id": "T-001",
                "plan_markdown": _MINIMAL_PLAN_DOC,
                "slug": "fixture",
            },
        ),
        ("wait_for_implementation", {"task_id": "T-001"}),
        (
            "review_implementation",
            {
                "task_id": "T-001",
                "verdict": "pass",
                "feedback": "looks good",
            },
        ),
        ("close_work_item", {"work_item_id": "IMP-fixture"}),
    ]

    async with integration_env(
        engine,
        agents_dir=repo.root / "agents",
        trace_dir=repo.root / ".trace",
        policy_script=script,
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        settings_extra={"repo_root": repo.root},
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={
                "agentRef": "lifecycle-agent@0.1.0",
                "intake": {"workItemPath": "docs/work-items/IMP-fixture.md"},
            },
            headers=env.auth_headers,
        )
        assert resp.status_code == 202, resp.text
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        supervisor = env.app.state.supervisor
        signal_task = asyncio.create_task(
            deliver_signal_when_paused(
                env.session_factory,
                supervisor,
                run_id,
                task_id="T-001",
                payload={"commit_sha": "abc1234"},
            )
        )

        run = await wait_for_terminal(
            env.session_factory, run_id, timeout_seconds=3.0
        )
        # signal_task resolves as a side-effect of the run advancing through
        # wait_for_implementation; await it to surface any assertion from the
        # pause-watcher.
        await signal_task

        assert run.status == "completed", (
            f"run did not complete: stop_reason={run.stop_reason} "
            f"final_state={run.final_state}"
        )
        assert run.stop_reason == "done_node"

        # Artifact assertions.
        tasks_file = repo.root / "tasks" / "IMP-fixture-tasks.md"
        assert tasks_file.is_file()
        assert "T-001" in tasks_file.read_text()

        plan_files = list((repo.root / "plans").glob("plan-T-001-*.md"))
        # At least one plan + one review file.
        plan_only = [p for p in plan_files if "-review-" not in p.name]
        reviews = [p for p in plan_files if "-review-" in p.name]
        assert plan_only, f"no plan files: {plan_files}"
        assert len(reviews) == 1, f"expected 1 review, got {reviews}"

        # Work item Status flipped.
        closed = repo.work_item_path.read_text()
        assert "| **Status** | Completed |" in closed
        assert re.search(r"\| \*\*Completed\*\* \| 20\d\d-\d\d-\d\dT", closed)

    get_settings.cache_clear()


@pytest.mark.asyncio(loop_scope="function")
async def test_sanity_repo_fixture_files_exist(tmp_path: Path) -> None:
    """Smoke: ensure :func:`prepare_repo` lays the expected files in tmp_path."""
    repo = prepare_repo(tmp_path)
    assert (repo.root / "agents" / "lifecycle-agent@0.1.0.yaml").is_file()
    assert (repo.root / ".ai-framework" / "prompts" / "feature-tasks.md").is_file()
    assert repo.work_item_path.is_file()
    assert repo.tasks_dir.is_dir()
    assert repo.plans_dir.is_dir()
    assert "**Status** | In Progress" in repo.work_item_path.read_text()


_unused: tuple[Any, ...] = (API_KEY,)  # silence unused-import warning
