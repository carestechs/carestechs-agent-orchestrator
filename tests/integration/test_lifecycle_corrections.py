"""Correction-bound end-to-end integration test (FEAT-005 / T-103).

Drives the lifecycle agent with a stub policy that yields three
consecutive ``review_implementation(fail, ...)`` verdicts.  The run must
terminate with ``stop_reason=error`` and
``final_state.reason=correction_budget_exceeded`` once the third
correction attempt pushes the counter past the default bound (2).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.core.llm import ScriptedCall
from app.modules.ai.enums import RunStatus, StopReason
from tests.conftest import API_KEY

from .env import integration_env
from .lifecycle_helpers import (
    prepare_repo,
    wait_for_pause,
    wait_for_terminal,
)

_TASKS_DOC = """# Task Breakdown: IMP-fixture

### T-001: First task
Body.
"""

_PLAN_DOC = """# Implementation Plan: T-001

## Overview
Plan.
"""


@pytest.mark.asyncio(loop_scope="function")
async def test_correction_budget_exceeded_terminates_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = prepare_repo(tmp_path)

    def _stub_get_diff(*_args: object, **_kwargs: object) -> str:
        return "diff --git a/x b/x\n+hello\n"

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.git.get_diff",
        _stub_get_diff,
    )
    monkeypatch.setenv("REPO_ROOT", str(repo.root))
    monkeypatch.setenv("LIFECYCLE_MAX_CORRECTIONS", "2")
    get_settings.cache_clear()

    # Policy script: intake → task-gen → assign → plan → pause →
    # review(fail) → corrections → pause → review(fail) → corrections →
    # pause → review(fail).  The 3rd corrections entry pushes
    # memory.correction_attempts[T-001] to 3, tripping the bound on the
    # next evaluate() iteration.
    script: list[ScriptedCall] = [
        ("load_work_item", {"path": "docs/work-items/IMP-fixture.md"}),
        ("generate_tasks", {"work_item_id": "IMP-fixture", "tasks_markdown": _TASKS_DOC}),
        ("assign_task", {"task_id": "T-001"}),
        (
            "generate_plan",
            {"task_id": "T-001", "plan_markdown": _PLAN_DOC, "slug": "first"},
        ),
        ("wait_for_implementation", {"task_id": "T-001"}),
        (
            "review_implementation",
            {"task_id": "T-001", "verdict": "fail", "feedback": "attempt 1 failed"},
        ),
        ("corrections", {"task_id": "T-001"}),
        ("wait_for_implementation", {"task_id": "T-001"}),
        (
            "review_implementation",
            {"task_id": "T-001", "verdict": "fail", "feedback": "attempt 2 failed"},
        ),
        ("corrections", {"task_id": "T-001"}),
        ("wait_for_implementation", {"task_id": "T-001"}),
        (
            "review_implementation",
            {"task_id": "T-001", "verdict": "fail", "feedback": "attempt 3 failed"},
        ),
        ("corrections", {"task_id": "T-001"}),
        # After this 3rd `corrections` entry, correction_attempts={"T-001": 3}
        # which exceeds the bound (2) → evaluate() returns ERROR → run ends.
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

        # Deliver three signals as the run pauses three times.
        async def deliver_three() -> None:
            for _ in range(3):
                await wait_for_pause(
                    env.session_factory, run_id, task_id="T-001", timeout_seconds=3.0
                )
                supervisor.deliver_signal(
                    run_id, "implementation-complete", "T-001", {}
                )
                # Small breathing room so the runtime exits the wait before
                # we start checking for the next pause step.
                await asyncio.sleep(0.05)

        signal_task = asyncio.create_task(deliver_three())

        run = await wait_for_terminal(
            env.session_factory, run_id, timeout_seconds=5.0
        )
        signal_task.cancel()
        try:
            await signal_task
        except (asyncio.CancelledError, AssertionError):
            pass

        assert run.status == RunStatus.FAILED
        assert run.stop_reason == StopReason.ERROR

        final_state: dict[str, Any] = run.final_state or {}
        assert final_state.get("reason") == "correction_budget_exceeded", final_state
        assert final_state.get("task_id") == "T-001"
        assert final_state.get("attempts") == 3

        # Three review files exist, Status NOT flipped.  The review tool
        # derives its slug from ``task.title`` ("First task" → "first-task")
        # regardless of the slug the plan was written under.
        reviews = list((repo.root / "plans").glob("plan-T-001-*-review-*.md"))
        assert len(reviews) == 3, reviews

        brief_body = repo.work_item_path.read_text()
        assert "| **Status** | In Progress |" in brief_body
        assert "| **Status** | Completed |" not in brief_body

    get_settings.cache_clear()
