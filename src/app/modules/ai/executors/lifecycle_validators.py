"""FEAT-020 — Validator executor handlers for lifecycle-agent@0.6.0-human.

Two LocalExecutor handler factories that fetch validator scripts from the
working repo via the GitHub Contents/tarball API and run them:

- ``make_validate_tasks_handler`` — fetches ``.ai-framework/tools/validate-tasks.py``
  from the run's ``codeSource.repo``, renders the in-memory task list to a temp
  markdown file, and runs the script.  Result written to
  ``validatorResults.tasks`` in RunMemory.

- ``make_validate_specs_strict_handler`` — downloads a shallow tarball of the
  working repo, extracts it to a temp directory, and runs
  ``.ai-framework/tools/validate-specs.py docs/ --strict`` from the extracted
  root.  On failure: writes a rejection patch to RunMemory so
  ``confirm_docs_update`` surfaces the output as ``priorFeedback`` on next
  dispatch.  Returns ``{"passed": bool}`` for the ``validator_passed``
  predicate to route on.

Both handlers skip non-fatally when ``GITHUB_PAT`` is absent or the run has no
``codeSource`` — validator unavailability never terminates a run.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tarfile
import tempfile
from collections.abc import Mapping
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.code_source import read_code_source

logger = logging.getLogger(__name__)

_SKIPPED_NO_PAT = {"skipped": True, "passed": True, "reason": "GITHUB_PAT not configured"}
_SKIPPED_NO_SOURCE = {"skipped": True, "passed": True, "reason": "codeSource missing from run intake"}
_SKIPPED_SCRIPT_NOT_FOUND = {"skipped": True, "passed": True, "reason": "validator script not found in repo"}


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


async def _fetch_file_from_github(
    pat: str,
    repo: str,
    path: str,
    ref: str,
) -> str | None:
    """Fetch a single file's raw content from the GitHub Contents API.

    Returns the file content as a string, or ``None`` when the file is not
    found (404) or any other HTTP error occurs.
    """
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params={"ref": ref})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError as exc:
        logger.warning("_fetch_file_from_github: HTTP error fetching %s@%s: %s", path, ref, exc)
        return None


async def _download_repo_tarball(
    pat: str,
    repo: str,
    ref: str,
) -> bytes | None:
    """Download a gzip tarball of the repo at *ref*.

    Returns raw bytes on success, ``None`` on any HTTP / network error.
    The tarball may be large; caller is responsible for cleanup.
    """
    url = f"https://api.github.com/repos/{repo}/tarball/{ref}"
    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError as exc:
        logger.warning("_download_repo_tarball: HTTP error downloading %s@%s: %s", repo, ref, exc)
        return None


def _extract_tarball(data: bytes, dest: str) -> str:
    """Extract a gzip tarball into *dest*, return the extracted root dir.

    GitHub tarballs contain a single top-level directory named
    ``{owner}-{repo}-{sha}/``.  Returns the full path to that directory.
    """
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest)
    entries = sorted(
        e for e in os.listdir(dest) if os.path.isdir(os.path.join(dest, e))
    )
    return os.path.join(dest, entries[0]) if entries else dest


def make_validate_tasks_handler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    github_pat: str | None,
    agent_ref: str,
) -> Any:
    """Factory for the ``run_validate_tasks`` LocalExecutor handler.

    Fetches ``.ai-framework/tools/validate-tasks.py`` from the run's
    ``codeSource.repo`` via the GitHub Contents API, renders the in-memory
    task list to a temp markdown file, and executes the script.  Skips
    non-fatally when ``GITHUB_PAT`` or ``codeSource`` are absent.
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not github_pat:
            logger.info("run_validate_tasks: GITHUB_PAT not configured — skipping")
            return _SKIPPED_NO_PAT

        try:
            cs = read_code_source(ctx)
        except (ValueError, Exception):
            logger.info("run_validate_tasks: codeSource missing from intake — skipping")
            return _SKIPPED_NO_SOURCE

        branch = cs.work_branch or cs.base_branch

        script_content = await _fetch_file_from_github(
            github_pat,
            cs.repo,
            ".ai-framework/tools/validate-tasks.py",
            branch,
        )
        if script_content is None:
            logger.info(
                "run_validate_tasks: validate-tasks.py not found in %s@%s — skipping",
                cs.repo, branch,
            )
            return {**_SKIPPED_SCRIPT_NOT_FOUND, "repo": cs.repo, "ref": branch}

        from app.modules.ai.executors.github_artifacts import render_task_list_markdown
        from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory

        memory_data = await _read_memory(session_factory, ctx.run_id)
        lifecycle_mem = read_lifecycle_memory(memory_data)
        wi_id = lifecycle_mem.work_item.id if lifecycle_mem.work_item else "UNKNOWN"

        md = render_task_list_markdown(lifecycle_mem.tasks, wi_id)

        with tempfile.TemporaryDirectory(prefix="orchestrator-validate-tasks-") as tmp_dir:
            script_path = os.path.join(tmp_dir, "validate-tasks.py")
            tasks_path = os.path.join(tmp_dir, f"tasks-{wi_id}.md")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_content)
            with open(tasks_path, "w", encoding="utf-8") as f:
                f.write(md)

            exit_code, output = await _run_subprocess(
                ["python", script_path, tasks_path],
                cwd=tmp_dir,
            )

        passed = exit_code == 0
        result: dict[str, Any] = {"exit_code": exit_code, "output": output, "passed": passed}

        existing = await _read_memory(session_factory, ctx.run_id)
        validator_results: dict[str, Any] = dict(existing.get("validatorResults") or {})
        validator_results["tasks"] = result

        logger.info(
            "run_validate_tasks: run=%s repo=%s passed=%s exit_code=%d",
            ctx.run_id, cs.repo, passed, exit_code,
        )
        return {
            "passed": passed,
            "exit_code": exit_code,
            "output": output,
            "__memory_patch": {"validatorResults": validator_results},
        }

    return _handler


def make_validate_specs_strict_handler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    github_pat: str | None,
) -> Any:
    """Factory for the ``run_validate_specs_strict`` LocalExecutor handler.

    Downloads a shallow tarball of the run's ``codeSource.repo``, extracts
    it to a temp directory, and runs ``.ai-framework/tools/validate-specs.py
    docs/ --strict`` from the extracted root.  On failure the handler writes
    a rejection patch to RunMemory so the ``confirm_docs_update`` intake
    builder surfaces the output as ``priorFeedback`` on the next dispatch.
    Returns ``{"passed": bool}`` for the ``validator_passed`` predicate.
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        if not github_pat:
            logger.info("run_validate_specs_strict: GITHUB_PAT not configured — skipping")
            return _SKIPPED_NO_PAT

        try:
            cs = read_code_source(ctx)
        except (ValueError, Exception):
            logger.info("run_validate_specs_strict: codeSource missing from intake — skipping")
            return _SKIPPED_NO_SOURCE

        branch = cs.work_branch or cs.base_branch

        logger.info(
            "run_validate_specs_strict: downloading tarball for %s@%s",
            cs.repo, branch,
        )
        tarball = await _download_repo_tarball(github_pat, cs.repo, branch)
        if tarball is None:
            logger.warning(
                "run_validate_specs_strict: tarball download failed for %s@%s — skipping",
                cs.repo, branch,
            )
            return {**_SKIPPED_NO_SOURCE, "reason": "tarball download failed"}

        loop = asyncio.get_event_loop()
        with tempfile.TemporaryDirectory(prefix="orchestrator-validate-specs-") as tmp_dir:
            root_dir = await loop.run_in_executor(
                None, lambda: _extract_tarball(tarball, tmp_dir)
            )

            script = os.path.join(root_dir, ".ai-framework", "tools", "validate-specs.py")
            if not os.path.isfile(script):
                logger.info(
                    "run_validate_specs_strict: validate-specs.py not found in %s@%s — skipping",
                    cs.repo, branch,
                )
                return {**_SKIPPED_SCRIPT_NOT_FOUND, "repo": cs.repo, "ref": branch}

            docs_dir = os.path.join(root_dir, "docs")
            exit_code, output = await _run_subprocess(
                ["python", script, docs_dir, "--strict"],
                cwd=root_dir,
            )

        passed = exit_code == 0
        logger.info(
            "run_validate_specs_strict: run=%s repo=%s passed=%s exit_code=%d",
            ctx.run_id, cs.repo, passed, exit_code,
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
        from app.modules.ai.executors.lifecycle_manual_patches import _rejection_patch  # pyright: ignore[reportPrivateUsage]

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
