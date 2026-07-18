"""FEAT-020 — Validator executor handlers for lifecycle-agent@0.6.0-human.

Three LocalExecutor handler factories:

- ``make_validate_tasks_handler`` — renders in-memory task list to a temp
  markdown file and runs ``validate-tasks.py`` from ia-framework tools.
  Result written to ``validatorResults.tasks`` in RunMemory.

- ``make_run_tests_handler`` — runs ``uv run pytest`` with a bounded timeout.
  Result written to ``testResults[task_id]`` in RunMemory.

- ``make_validate_specs_strict_handler`` — runs ``validate-specs.py --strict``
  against the project's docs/ tree.  On failure: writes a rejection patch to
  RunMemory so ``confirm_docs_update`` surfaces the failure as ``priorFeedback``
  on next dispatch.  Returns ``{"passed": bool}`` for the ``validator_passed``
  predicate to route on.

All handlers skip non-fatally when the required tool path or repo path is
absent — validator unavailability never terminates a run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext

logger = logging.getLogger(__name__)

_SKIPPED_NO_TOOLS = {"skipped": True, "reason": "ia_framework_tools_path not configured"}
_SKIPPED_NO_REPO = {"skipped": True, "reason": "lifecycle_project_repo_path not configured"}
_SKIPPED_TOOL_NOT_FOUND = {"skipped": True, "reason": "validator script not found on disk"}


async def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: float = 300.0,
) -> tuple[int, str]:
    """Run a subprocess and return (exit_code, combined_output).

    Truncates output to 8 000 chars to keep the memory patch bounded.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return 1, f"[timed out after {timeout:.0f}s]"
        output = (stdout or b"").decode("utf-8", errors="replace")
        return (proc.returncode or 0), output[:8000]
    except FileNotFoundError:
        return 1, "[command not found]"


async def _read_memory(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: Any,
) -> dict[str, Any]:
    from app.modules.ai.models import RunMemory

    async with session_factory() as session:
        row = await session.scalar(select(RunMemory).where(RunMemory.run_id == run_id))
    return ((row.data if row is not None else {}) or {}).copy()


def make_validate_tasks_handler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tools_path: str | None,
    agent_ref: str,
) -> Any:
    """Factory for the ``run_validate_tasks`` LocalExecutor handler."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not tools_path:
            return _SKIPPED_NO_TOOLS

        script = os.path.join(tools_path, "validate-tasks.py")
        if not os.path.isfile(script):
            logger.warning("run_validate_tasks: script not found at %s — skipping", script)
            return _SKIPPED_TOOL_NOT_FOUND

        from app.modules.ai.executors.github_artifacts import render_task_list_markdown
        from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory

        memory_data = await _read_memory(session_factory, ctx.run_id)
        lifecycle_mem = read_lifecycle_memory(memory_data)
        wi_id = lifecycle_mem.work_item.id if lifecycle_mem.work_item else "UNKNOWN"

        md = render_task_list_markdown(lifecycle_mem.tasks, wi_id)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            prefix=f"tasks-{wi_id}-",
            delete=False,
        ) as tmp:
            tmp.write(md)
            tmp_path = tmp.name

        try:
            exit_code, output = await _run_subprocess(["python", script, tmp_path])
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        passed = exit_code == 0
        result: dict[str, Any] = {"exit_code": exit_code, "output": output, "passed": passed}

        existing = await _read_memory(session_factory, ctx.run_id)
        validator_results: dict[str, Any] = dict(existing.get("validatorResults") or {})
        validator_results["tasks"] = result

        logger.info(
            "run_validate_tasks: run=%s passed=%s exit_code=%d",
            ctx.run_id, passed, exit_code,
        )
        return {
            "passed": passed,
            "exit_code": exit_code,
            "output": output,
            "__memory_patch": {"validatorResults": validator_results},
        }

    return _handler


def make_run_tests_handler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeout_seconds: int = 300,
    agent_ref: str,
) -> Any:
    """Factory for the ``run_tests`` LocalExecutor handler."""

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory

        memory_data = await _read_memory(session_factory, ctx.run_id)
        lifecycle_mem = read_lifecycle_memory(memory_data)
        task_id = lifecycle_mem.current_task_id or ctx.intake.get("taskId") or "unknown"

        exit_code, output = await _run_subprocess(
            ["uv", "run", "pytest", "tests/", "--tb=short", "-q", "--no-header"],
            timeout=float(timeout_seconds),
        )

        passed = exit_code == 0
        result: dict[str, Any] = {"exit_code": exit_code, "output": output, "passed": passed}

        existing = await _read_memory(session_factory, ctx.run_id)
        test_results: dict[str, Any] = dict(existing.get("testResults") or {})
        test_results[task_id] = result

        logger.info(
            "run_tests: run=%s task=%s passed=%s exit_code=%d",
            ctx.run_id, task_id, passed, exit_code,
        )
        return {
            "passed": passed,
            "exit_code": exit_code,
            "output": output,
            "task_id": task_id,
            "__memory_patch": {"testResults": test_results},
        }

    return _handler


def make_validate_specs_strict_handler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tools_path: str | None,
    repo_path: str | None,
) -> Any:
    """Factory for the ``run_validate_specs_strict`` LocalExecutor handler.

    On failure the handler writes a rejection patch to RunMemory so the
    ``confirm_docs_update`` intake builder surfaces the output as
    ``priorFeedback`` on the next dispatch.
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not tools_path:
            return {**_SKIPPED_NO_TOOLS, "passed": True}
        if not repo_path:
            return {**_SKIPPED_NO_REPO, "passed": True}

        script = os.path.join(tools_path, "validate-specs.py")
        if not os.path.isfile(script):
            logger.warning(
                "run_validate_specs_strict: script not found at %s — skipping", script
            )
            return {**_SKIPPED_TOOL_NOT_FOUND, "passed": True}

        docs_dir = os.path.join(repo_path, "docs")
        exit_code, output = await _run_subprocess(
            ["python", script, docs_dir, "--strict"],
            cwd=repo_path,
        )

        passed = exit_code == 0
        logger.info(
            "run_validate_specs_strict: run=%s passed=%s exit_code=%d",
            ctx.run_id, passed, exit_code,
        )

        if passed:
            memory_data = await _read_memory(session_factory, ctx.run_id)
            validator_results: dict[str, Any] = dict(memory_data.get("validatorResults") or {})
            validator_results["specs"] = {"exit_code": exit_code, "output": output, "passed": True}
            return {
                "passed": True,
                "exit_code": exit_code,
                "output": output,
                "__memory_patch": {"validatorResults": validator_results},
            }

        # Failure — write a rejection patch so confirm_docs_update shows
        # the validator output as priorFeedback on the next dispatch.
        from app.modules.ai.executors.lifecycle_manual_patches import _rejection_patch

        memory_data = await _read_memory(session_factory, ctx.run_id)
        validator_results = dict(memory_data.get("validatorResults") or {})
        validator_results["specs"] = {"exit_code": exit_code, "output": output, "passed": False}

        rejection = _rejection_patch("confirm_docs_update", output[:2000], memory_data)
        patch: dict[str, Any] = {"validatorResults": validator_results}
        patch.update(rejection)

        return {
            "passed": False,
            "exit_code": exit_code,
            "output": output,
            "__memory_patch": patch,
        }

    return _handler
