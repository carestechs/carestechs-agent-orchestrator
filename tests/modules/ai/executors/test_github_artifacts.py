"""Unit tests for FEAT-019 — ``github_artifacts.py`` rendering functions.

Coverage areas:
1. Pure renderer functions — ``render_brief_markdown``, ``render_task_list_markdown``,
   ``render_plan_markdown``, ``render_review_markdown``, ``render_event_line``.
2. ``_slug`` utility.
3. ``_resolve_plan_text`` — reads from the correct dict path, gracefully returns
   empty string when data is absent.
4. Handler skip behaviour — all six ``make_*_handler`` factories return
   ``{"skipped": True}`` when ``pat`` is ``None`` (no network required).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.github_artifacts import (
    _resolve_plan_text,
    _slug,
    make_commit_brief_handler,
    make_commit_plan_handler,
    make_commit_review_handler,
    make_commit_tasks_handler,
    make_log_run_completed_handler,
    make_log_run_started_handler,
    render_brief_markdown,
    render_event_line,
    render_plan_markdown,
    render_review_markdown,
    render_task_list_markdown,
)
from app.modules.ai.tools.lifecycle.memory import LifecycleTask, WorkItemRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(intake: dict[str, Any] | None = None) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.6.0-human",
        node_name="test_node",
        intake=intake or {},
    )


def _simple_work_item(**kwargs: Any) -> WorkItemRef:
    defaults: dict[str, Any] = {
        "id": "FEAT-100",
        "type": "FEAT",
        "title": "My Feature",
        "path": "",
        "summary": "A feature summary",
        "acceptance_criteria": ["AC-1", "AC-2"],
    }
    defaults.update(kwargs)
    return WorkItemRef(**defaults)


def _simple_task(**kwargs: Any) -> LifecycleTask:
    defaults: dict[str, Any] = {
        "id": "T-001",
        "title": "Add login page",
        "kind": "feature",
        "complexity": "medium",
        "description": "Implement the login form",
        "depends_on": [],
        "files_hint": [],
    }
    defaults.update(kwargs)
    return LifecycleTask(**defaults)


# ---------------------------------------------------------------------------
# 1. _slug
# ---------------------------------------------------------------------------


class TestSlug:
    def test_lowercase_and_hyphenate(self) -> None:
        assert _slug("Add Login Page") == "add-login-page"

    def test_special_chars_become_hyphens(self) -> None:
        assert _slug("feat(auth): do_thing!") == "feat-auth-do-thing"

    def test_truncate_to_48_chars(self) -> None:
        long_text = "a" * 60
        result = _slug(long_text)
        assert len(result) <= 48

    def test_strips_leading_trailing_hyphens(self) -> None:
        result = _slug("  --Hello World--  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_empty_string(self) -> None:
        assert _slug("") == ""


# ---------------------------------------------------------------------------
# 2. render_brief_markdown
# ---------------------------------------------------------------------------


class TestRenderBriefMarkdown:
    def test_contains_id_and_title(self) -> None:
        wi = _simple_work_item()
        md = render_brief_markdown(wi)
        assert "FEAT-100" in md
        assert "My Feature" in md

    def test_contains_type(self) -> None:
        wi = _simple_work_item(type="BUG")
        md = render_brief_markdown(wi)
        assert "BUG" in md

    def test_summary_included(self) -> None:
        wi = _simple_work_item(summary="A detailed summary")
        md = render_brief_markdown(wi)
        assert "A detailed summary" in md

    def test_summary_fallback_when_empty(self) -> None:
        wi = _simple_work_item(summary="")
        md = render_brief_markdown(wi)
        assert "(no summary provided)" in md

    def test_acceptance_criteria_listed(self) -> None:
        wi = _simple_work_item(acceptance_criteria=["AC-1", "AC-2"])
        md = render_brief_markdown(wi)
        assert "- AC-1" in md
        assert "- AC-2" in md

    def test_no_acceptance_criteria_shows_none(self) -> None:
        wi = _simple_work_item(acceptance_criteria=[])
        md = render_brief_markdown(wi)
        assert "(none specified)" in md


# ---------------------------------------------------------------------------
# 3. render_task_list_markdown
# ---------------------------------------------------------------------------


class TestRenderTaskListMarkdown:
    def test_empty_tasks(self) -> None:
        md = render_task_list_markdown([], "FEAT-100")
        assert "FEAT-100" in md
        assert "(no tasks)" in md

    def test_single_task_basic_fields(self) -> None:
        task = _simple_task()
        md = render_task_list_markdown([task], "FEAT-100")
        assert "T-001" in md
        assert "Add login page" in md
        assert "feature" in md
        assert "medium" in md

    def test_dependencies_rendered(self) -> None:
        task = _simple_task(depends_on=["T-000"])
        md = render_task_list_markdown([task], "FEAT-100")
        assert "T-000" in md

    def test_no_dependencies_shows_none(self) -> None:
        task = _simple_task(depends_on=[])
        md = render_task_list_markdown([task], "FEAT-100")
        assert "None" in md

    def test_files_to_modify_rendered(self) -> None:
        task = _simple_task(files_hint=["src/auth.py", "tests/test_auth.py"])
        md = render_task_list_markdown([task], "FEAT-100")
        assert "src/auth.py" in md

    def test_mockup_kind_defaults_to_mockup_first_workflow(self) -> None:
        task = _simple_task(kind="mockup", workflow=None)
        md = render_task_list_markdown([task], "FEAT-100")
        assert "mockup-first" in md

    def test_explicit_workflow_overrides_kind_default(self) -> None:
        task = _simple_task(kind="mockup", workflow="standard")
        md = render_task_list_markdown([task], "FEAT-100")
        assert "standard" in md

    def test_feature_kind_defaults_to_standard_workflow(self) -> None:
        task = _simple_task(kind="feature", workflow=None)
        md = render_task_list_markdown([task], "FEAT-100")
        assert "standard" in md

    def test_multiple_tasks_all_present(self) -> None:
        tasks = [_simple_task(id=f"T-{i:03d}", title=f"Task {i}") for i in range(3)]
        md = render_task_list_markdown(tasks, "FEAT-100")
        for i in range(3):
            assert f"T-{i:03d}" in md


# ---------------------------------------------------------------------------
# 4. render_plan_markdown
# ---------------------------------------------------------------------------


class TestRenderPlanMarkdown:
    def test_contains_task_id_and_title(self) -> None:
        task = _simple_task()
        md = render_plan_markdown(task, "Step 1: do something\n\nStep 2: do another thing")
        assert "T-001" in md
        assert "Add login page" in md

    def test_contains_plan_body(self) -> None:
        task = _simple_task()
        md = render_plan_markdown(task, "Step 1: do something")
        assert "Step 1: do something" in md

    def test_strips_leading_trailing_whitespace_from_plan(self) -> None:
        task = _simple_task()
        md = render_plan_markdown(task, "  \nStep 1\n  ")
        assert md.endswith("\n")
        assert "  \nStep 1" not in md


# ---------------------------------------------------------------------------
# 5. render_review_markdown
# ---------------------------------------------------------------------------


class TestRenderReviewMarkdown:
    def test_contains_task_id(self) -> None:
        task = _simple_task()
        md = render_review_markdown(task, "pass", "alice", "Looks good")
        assert "T-001" in md

    def test_contains_verdict(self) -> None:
        task = _simple_task()
        md = render_review_markdown(task, "fail", "bob", None)
        assert "fail" in md

    def test_contains_reviewer(self) -> None:
        task = _simple_task()
        md = render_review_markdown(task, "pass", "alice", None)
        assert "alice" in md

    def test_notes_included_when_present(self) -> None:
        task = _simple_task()
        md = render_review_markdown(task, "pass", "alice", "All AC covered")
        assert "All AC covered" in md

    def test_notes_fallback_when_none(self) -> None:
        task = _simple_task()
        md = render_review_markdown(task, "pass", "alice", None)
        assert "(no notes)" in md


# ---------------------------------------------------------------------------
# 6. render_event_line
# ---------------------------------------------------------------------------


class TestRenderEventLine:
    def test_produces_valid_json(self) -> None:
        line = render_event_line(
            run_id="abc", agent_ref="lifecycle-agent@0.6.0-human",
            event="started", step="start",
            work_item_id="FEAT-100", task_id=None, detail=None,
        )
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_required_keys_present(self) -> None:
        line = render_event_line(
            run_id="abc", agent_ref="lifecycle-agent@0.6.0-human",
            event="started", step="start",
            work_item_id="FEAT-100", task_id=None, detail=None,
        )
        parsed = json.loads(line)
        for key in ("ts", "run_id", "agent_ref", "step", "event", "work_item_id", "task_id", "detail"):
            assert key in parsed, f"missing key: {key}"

    def test_event_value_preserved(self) -> None:
        line = render_event_line(
            run_id="r1", agent_ref="ref", event="artifact_committed",
            step="commit_brief", work_item_id="FEAT-1", task_id=None, detail="path@abc123",
        )
        parsed = json.loads(line)
        assert parsed["event"] == "artifact_committed"
        assert parsed["detail"] == "path@abc123"


# ---------------------------------------------------------------------------
# 7. _resolve_plan_text
# ---------------------------------------------------------------------------


class TestResolvePlanText:
    def test_reads_plan_markdown_from_correct_path(self) -> None:
        mem_data: dict[str, Any] = {
            "plans": {
                "T-001": {"plan_markdown": "# Plan\n\nStep 1."},
            }
        }
        assert _resolve_plan_text(mem_data, "T-001") == "# Plan\n\nStep 1."

    def test_returns_empty_when_task_id_missing(self) -> None:
        mem_data: dict[str, Any] = {"plans": {"T-001": {"plan_markdown": "something"}}}
        assert _resolve_plan_text(mem_data, "T-999") == ""

    def test_returns_empty_when_plans_absent(self) -> None:
        assert _resolve_plan_text({}, "T-001") == ""

    def test_returns_empty_when_plans_is_not_dict(self) -> None:
        assert _resolve_plan_text({"plans": "not a dict"}, "T-001") == ""

    def test_returns_empty_when_plan_markdown_is_none(self) -> None:
        mem_data: dict[str, Any] = {"plans": {"T-001": {"plan_markdown": None}}}
        assert _resolve_plan_text(mem_data, "T-001") == ""


# ---------------------------------------------------------------------------
# 8. Handler skip behaviour — no network calls
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_session_factory() -> Any:
    return MagicMock(name="session_factory")


class TestHandlerSkipWhenPatAbsent:
    """All handlers must short-circuit with skipped=True when PAT is None."""

    @pytest.mark.asyncio
    async def test_commit_brief_skips_without_pat(self, stub_session_factory: Any) -> None:
        handler = make_commit_brief_handler(
            stub_session_factory, pat=None, branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_commit_tasks_skips_without_pat(self, stub_session_factory: Any) -> None:
        handler = make_commit_tasks_handler(
            stub_session_factory, pat=None, branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_commit_plan_skips_without_pat(self, stub_session_factory: Any) -> None:
        handler = make_commit_plan_handler(
            stub_session_factory, pat=None, branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_commit_review_skips_without_pat(self, stub_session_factory: Any) -> None:
        handler = make_commit_review_handler(
            stub_session_factory, pat=None, branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_log_run_started_skips_without_pat(self) -> None:
        handler = make_log_run_started_handler(pat=None, branch="main", agent_ref="ref")
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_log_run_completed_skips_without_pat(self, stub_session_factory: Any) -> None:
        handler = make_log_run_completed_handler(
            stub_session_factory, pat=None, branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({"codeSource": {"repo": "owner/repo", "baseBranch": "main"}})
        result = await handler(ctx)
        assert result.get("skipped") is True


class TestHandlerSkipWhenCodeSourceAbsent:
    """Handlers skip when codeSource.repo is missing from intake (PAT is present)."""

    @pytest.mark.asyncio
    async def test_commit_brief_skips_without_code_source(self, stub_session_factory: Any) -> None:
        handler = make_commit_brief_handler(
            stub_session_factory, pat="ghp_fake", branch="main", agent_ref="ref"
        )
        ctx = _make_ctx({})  # no codeSource
        result = await handler(ctx)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_log_run_started_skips_without_code_source(self) -> None:
        handler = make_log_run_started_handler(pat="ghp_fake", branch="main", agent_ref="ref")
        ctx = _make_ctx({})
        result = await handler(ctx)
        assert result.get("skipped") is True
