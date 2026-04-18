"""Anthropic-mocked end-to-end lifecycle test (FEAT-005 / T-102).

Drives ``lifecycle-agent@0.1.0`` with ``LLM_PROVIDER=anthropic`` and
``respx``-mocked Messages API responses — one ``tool_use`` per stage.
Covers AC-3 (real provider reaches every stage) and AC-4 (the pause is
resumed via the real ``POST /runs/{id}/signals`` endpoint).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.core.llm_anthropic import AnthropicLLMProvider
from tests.conftest import API_KEY

from .env import integration_env
from .lifecycle_helpers import prepare_repo, wait_for_pause, wait_for_terminal

_TASKS_DOC = """# Task Breakdown: IMP-fixture

### T-001: Trivial task
Body.
"""

_PLAN_DOC = """# Implementation Plan: T-001

## Overview
Plan.
"""


def _tool_use_response(
    tool_name: str,
    *,
    tool_use_id: str,
    tool_input: dict[str, Any] | None = None,
    input_tokens: int = 25,
    output_tokens: int = 10,
) -> dict[str, Any]:
    return {
        "id": f"msg_{tool_use_id}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": [
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": tool_input if tool_input is not None else {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _build_settings(repo_root: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        orchestrator_api_key="k",
        engine_webhook_secret="s",
        engine_base_url="http://engine.test",
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-xxx-aaaaaaaaaaaaaaaaaaaa",
        llm_model="claude-opus-4-7",
        repo_root=repo_root,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_lifecycle_agent_anthropic_mocked(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = prepare_repo(tmp_path)

    def _stub_get_diff(*_args: object, **_kwargs: object) -> str:
        return "diff --git a/x b/x\n+hello\n"

    monkeypatch.setattr(
        "app.modules.ai.tools.lifecycle.git.get_diff", _stub_get_diff
    )
    monkeypatch.setenv("REPO_ROOT", str(repo.root))
    get_settings.cache_clear()

    settings = _build_settings(repo.root)
    provider = AnthropicLLMProvider(settings)

    # One tool_use response per expected policy call.  The lifecycle flow
    # is: load_work_item → generate_tasks → assign_task → generate_plan →
    # wait_for_implementation (pause) → review_implementation(pass) →
    # close_work_item → terminate (done_node fires on close_work_item's
    # completion; no additional policy call).
    responses = [
        httpx.Response(
            200,
            json=_tool_use_response(
                "load_work_item",
                tool_use_id="tu1",
                tool_input={"path": "docs/work-items/IMP-fixture.md"},
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "generate_tasks",
                tool_use_id="tu2",
                tool_input={
                    "work_item_id": "IMP-fixture",
                    "tasks_markdown": _TASKS_DOC,
                },
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "assign_task", tool_use_id="tu3", tool_input={"task_id": "T-001"}
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "generate_plan",
                tool_use_id="tu4",
                tool_input={
                    "task_id": "T-001",
                    "plan_markdown": _PLAN_DOC,
                    "slug": "trivial",
                },
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "wait_for_implementation",
                tool_use_id="tu5",
                tool_input={"task_id": "T-001"},
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "review_implementation",
                tool_use_id="tu6",
                tool_input={
                    "task_id": "T-001",
                    "verdict": "pass",
                    "feedback": "looks good",
                },
            ),
        ),
        httpx.Response(
            200,
            json=_tool_use_response(
                "close_work_item",
                tool_use_id="tu7",
                tool_input={"work_item_id": "IMP-fixture"},
            ),
        ),
    ]

    with respx.mock(base_url="https://api.anthropic.com") as anthropic_mock:
        anthropic_mock.post("/v1/messages").mock(side_effect=responses)

        async with integration_env(
            engine,
            agents_dir=repo.root / "agents",
            trace_dir=repo.root / ".trace",
            policy_script=[],  # unused; we supply `policy` directly
            webhook_signer=webhook_signer,
            api_key=API_KEY,
            settings_extra={"repo_root": repo.root},
            policy=provider,
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

            # Deliver the implementation signal via the REAL endpoint.
            async def post_signal_when_paused() -> None:
                await wait_for_pause(
                    env.session_factory, run_id, task_id="T-001", timeout_seconds=3.0
                )
                r = await env.client.post(
                    f"/api/v1/runs/{run_id}/signals",
                    json={
                        "name": "implementation-complete",
                        "taskId": "T-001",
                        "payload": {"commit_sha": "abc"},
                    },
                    headers=env.auth_headers,
                )
                assert r.status_code == 202, r.text

            signal_task = asyncio.create_task(post_signal_when_paused())
            run = await wait_for_terminal(
                env.session_factory, run_id, timeout_seconds=5.0
            )
            await signal_task

        assert run.status == "completed", (
            f"status={run.status} stop_reason={run.stop_reason} final_state={run.final_state}"
        )
        assert run.stop_reason == "done_node"

    # Artifact + trace assertions (outside the respx block — it's fine to
    # inspect the filesystem after the mock exits).
    assert (repo.root / "tasks" / "IMP-fixture-tasks.md").is_file()
    assert list((repo.root / "plans").glob("plan-T-001-*.md"))
    closed = repo.work_item_path.read_text()
    assert "| **Status** | Completed |" in closed

    # The trace file should have at least one operator_signal entry.
    trace_path = repo.root / ".trace" / f"{run_id}.jsonl"
    assert trace_path.is_file(), f"trace not written to {trace_path}"
    lines = trace_path.read_text().splitlines()
    assert any('"kind":"operator_signal"' in ln.replace(" ", "") for ln in lines), (
        "operator_signal entry missing from trace"
    )

    get_settings.cache_clear()
