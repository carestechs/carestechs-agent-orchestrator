"""Live lifecycle-agent contract test (FEAT-005 / T-104).

Drives ``lifecycle-agent@0.1.0`` against the REAL Anthropic Messages API
on a fixture work item.  Skipped by default — requires both
``--run-live`` AND a working ``ANTHROPIC_API_KEY``.  This is the drift
detector for ``test_lifecycle_anthropic_mocked``'s recorded responses:
if Anthropic's API shape changes, this test catches the mismatch that
the respx mocks would hide.

Cost estimate: ~$0.20 per run (8 policy calls x ~6k tokens each at
claude-opus-4-7 pricing, rough order of magnitude).  Do not run in the
default local-dev loop; run deliberately from CI or by hand.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from tests.conftest import API_KEY
from tests.integration.env import integration_env
from tests.integration.lifecycle_helpers import (
    prepare_repo,
    wait_for_pause,
    wait_for_terminal,
)


def _skip_if_no_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "set ANTHROPIC_API_KEY to run the live lifecycle contract test"
        )


@pytest.mark.live
@pytest.mark.asyncio(loop_scope="function")
async def test_lifecycle_agent_live_completes(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the lifecycle agent against real Claude; assert structural only.

    The real LLM's outputs vary run-to-run — we assert the run reaches
    ``completed`` and that every expected artifact file exists and is
    non-empty.  We do NOT assert on content quality (that's a prompt
    engineering concern, not a wiring concern).
    """
    _skip_if_no_key()

    repo = prepare_repo(tmp_path)

    def _stub_get_diff(*_args: object, **_kwargs: object) -> str:
        return "diff --git a/x b/x\n+content\n"

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.git.get_diff", _stub_get_diff
    )
    monkeypatch.setenv("REPO_ROOT", str(repo.root))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()

    async with integration_env(
        engine,
        agents_dir=repo.root / "agents",
        trace_dir=repo.root / ".trace",
        policy_script=[],  # unused — the real provider is wired via Settings.
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        settings_extra={
            "repo_root": repo.root,
            "llm_provider": "anthropic",
        },
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

        # Simulate the operator: wait for the pause, POST the signal via
        # the real endpoint.  Generous 60s timeout — the real API may take
        # 5-10s per call.
        async def operator_signal() -> None:
            await wait_for_pause(
                env.session_factory, run_id, task_id="T-001", timeout_seconds=60.0
            )
            r = await env.client.post(
                f"/api/v1/runs/{run_id}/signals",
                json={
                    "name": "implementation-complete",
                    "taskId": "T-001",
                    "payload": {},
                },
                headers=env.auth_headers,
            )
            assert r.status_code == 202, r.text

        signal_task = asyncio.create_task(operator_signal())
        run = await wait_for_terminal(
            env.session_factory, run_id, timeout_seconds=120.0
        )
        await signal_task

    assert run.status == "completed", (
        f"status={run.status} stop_reason={run.stop_reason} "
        f"final_state={run.final_state}"
    )

    # Structural artifact checks (content varies).
    tasks_file = repo.root / "tasks" / "IMP-fixture-tasks.md"
    assert tasks_file.is_file()
    assert tasks_file.stat().st_size > 100

    plans = [
        p
        for p in (repo.root / "plans").glob("plan-T-*.md")
        if "-review-" not in p.name
    ]
    reviews = list((repo.root / "plans").glob("plan-T-*-review-*.md"))
    assert plans, "no plan files produced"
    assert reviews, "no review files produced"

    closed = repo.work_item_path.read_text()
    assert "| **Status** | Completed |" in closed

    get_settings.cache_clear()


# Silence unused-import pressure when the test is skipped.
_unused: tuple[Any, ...] = (API_KEY,)
