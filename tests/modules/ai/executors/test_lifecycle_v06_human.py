"""Unit tests for FEAT-018 + FEAT-019 — ``lifecycle-agent@0.6.0-human``.

Coverage areas:
1. Extended payload schemas — ``_WorkItemCorrection`` with summary/
   acceptanceCriteria, ``_TaskInput`` with kind, ``MockupApprovedPayload``
   with mockupHtml.
2. Patch builder behaviour — create-from-scratch path in
   ``apply_brief_correction``; ``apply_mockup_approval`` writing mockupHtml.
3. FEAT-019 builders — ``apply_task_review_verdict``,
   ``apply_docs_update_verdict``, ``intake_for_confirm_task_review``,
   ``intake_for_confirm_docs_update``.
4. Executor coverage — ``register_lifecycle_v06_human`` satisfies the
   coverage validator for every node in the YAML.
5. No LLM imports — registration path must not import ``core.llm``.
   Uses a subprocess for a clean sys.modules baseline (same pattern as
   ``test_runtime_deterministic_is_pure.py``).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.agents import list_agents
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.bootstrap import (
    ExecutorCoverageError,
    register_lifecycle_v06_human,
    run_coverage_validation,
)
from app.modules.ai.executors.engine import EngineExecutor
from app.modules.ai.executors.engine_create import EngineCreateExecutor
from app.modules.ai.executors.human import HumanExecutor
from app.modules.ai.executors.lifecycle_manual_patches import (
    DocsUpdateConfirmedPayload,
    MockupApprovedPayload,
    TasksReviewedPayload,
    apply_brief_correction,
    apply_docs_update_verdict,
    apply_mockup_approval,
    apply_task_review_verdict,
    apply_tasks_correction,
    intake_for_confirm_docs_update,
    intake_for_confirm_task_review,
)
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.propose_tasks import ProposeTasksExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.executors.sequence import SequenceEngineExecutor
from app.modules.ai.executors.submit_implementation import SubmitImplementationExecutor
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleTask,
    WorkItemRef,
    to_run_memory,
)

_AGENTS_DIR = Path(__file__).resolve().parents[4] / "agents"
_AGENT_REF = "lifecycle-agent@0.6.0-human"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def stub_lifecycle_client() -> Any:
    return MagicMock(name="FlowEngineLifecycleClient")


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_memory() -> dict[str, Any]:
    mem = LifecycleMemory.empty()
    return {LIFECYCLE_MEMORY_NS: to_run_memory(mem)}


def _memory_with_work_item(
    *,
    ref: str = "FEAT-100",
    title: str = "Title",
    type_: str = "FEAT",
    summary: str = "",
    acceptance_criteria: list[str] | None = None,
) -> dict[str, Any]:
    mem = LifecycleMemory(
        work_item=WorkItemRef(
            id=ref,
            type=type_,  # type: ignore[arg-type]
            title=title,
            path="",
            summary=summary,
            acceptance_criteria=acceptance_criteria or [],
        )
    )
    return {LIFECYCLE_MEMORY_NS: to_run_memory(mem)}


def _memory_with_current_task(task_id: str) -> dict[str, Any]:
    task = LifecycleTask(id=task_id, title="Task One", kind="feature")
    mem = LifecycleMemory(
        work_item=WorkItemRef(id="FEAT-100", type="FEAT", title="Title", path=""),
        tasks=[task],
        current_task_id=task_id,
    )
    return {LIFECYCLE_MEMORY_NS: to_run_memory(mem)}


# ---------------------------------------------------------------------------
# 1. Extended payload schemas
# ---------------------------------------------------------------------------


class TestBriefConfirmedPayloadExtensions:
    def test_summary_field_accepted(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import BriefConfirmedPayload

        p = BriefConfirmedPayload.model_validate(
            {"verdict": "approve", "workItem": {"id": "FEAT-1", "title": "T", "type": "FEAT", "summary": "Summary text"}}
        )
        assert p.work_item is not None
        assert p.work_item.summary == "Summary text"

    def test_acceptance_criteria_field_accepted(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import BriefConfirmedPayload

        p = BriefConfirmedPayload.model_validate(
            {
                "workItem": {
                    "id": "FEAT-1",
                    "title": "T",
                    "type": "FEAT",
                    "acceptanceCriteria": ["criterion A", "criterion B"],
                }
            }
        )
        assert p.work_item is not None
        assert p.work_item.acceptance_criteria == ["criterion A", "criterion B"]

    def test_extra_field_rejected(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import BriefConfirmedPayload

        with pytest.raises(ValidationError):
            BriefConfirmedPayload.model_validate({"unknownField": "x"})


class TestTaskInputKindField:
    def test_kind_feature_accepted(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import _TaskInput

        t = _TaskInput.model_validate({"id": "T-1", "title": "Task", "kind": "feature"})
        assert t.kind == "feature"

    def test_kind_mockup_accepted(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import _TaskInput

        t = _TaskInput.model_validate({"id": "T-1", "title": "Task", "kind": "mockup"})
        assert t.kind == "mockup"

    def test_kind_defaults_to_none_when_absent(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import _TaskInput

        t = _TaskInput.model_validate({"id": "T-1", "title": "Task"})
        assert t.kind is None

    def test_invalid_kind_raises(self) -> None:
        from app.modules.ai.executors.lifecycle_manual_patches import _TaskInput

        with pytest.raises(ValidationError):
            _TaskInput.model_validate({"id": "T-1", "title": "Task", "kind": "sprint"})


class TestMockupApprovedPayloadExtensions:
    def test_mockup_html_on_approve_accepted(self) -> None:
        p = MockupApprovedPayload.model_validate(
            {"verdict": "approve", "mockupHtml": "<!DOCTYPE html><html></html>"}
        )
        assert p.mockup_html == "<!DOCTYPE html><html></html>"

    def test_mockup_html_on_reject_accepted(self) -> None:
        p = MockupApprovedPayload.model_validate(
            {"verdict": "reject", "feedback": "redo it", "mockupHtml": "<html></html>"}
        )
        assert p.verdict == "reject"
        assert p.mockup_html == "<html></html>"

    def test_mockup_html_absent_defaults_to_none(self) -> None:
        p = MockupApprovedPayload.model_validate({"verdict": "approve"})
        assert p.mockup_html is None


# ---------------------------------------------------------------------------
# 2. Patch builder behaviour
# ---------------------------------------------------------------------------


class TestApplyBriefCorrectionV06Human:
    def test_create_from_scratch_when_memory_empty(self) -> None:
        """@0.6.0-human mode: no prior load_work_item → WorkItemRef created from payload."""
        mem = _empty_memory()
        patch = apply_brief_correction(
            {
                "workItem": {
                    "id": "FEAT-200",
                    "title": "New Feature",
                    "type": "FEAT",
                    "summary": "Operator-authored summary",
                    "acceptanceCriteria": ["AC-1", "AC-2"],
                }
            },
            mem,
        )
        assert LIFECYCLE_MEMORY_NS in patch
        wi = patch[LIFECYCLE_MEMORY_NS]["workItem"]
        assert wi["id"] == "FEAT-200"
        assert wi["title"] == "New Feature"
        assert wi["type"] == "FEAT"
        assert wi["summary"] == "Operator-authored summary"
        assert wi["acceptanceCriteria"] == ["AC-1", "AC-2"]

    def test_create_from_scratch_missing_id_raises(self) -> None:
        mem = _empty_memory()
        with pytest.raises(ValueError, match="requires workItem.id"):
            apply_brief_correction({"workItem": {"title": "T", "type": "FEAT"}}, mem)

    def test_create_from_scratch_missing_title_raises(self) -> None:
        mem = _empty_memory()
        with pytest.raises(ValueError, match="requires workItem.id"):
            apply_brief_correction({"workItem": {"id": "FEAT-1", "type": "FEAT"}}, mem)

    def test_summary_merged_on_top_of_existing_work_item(self) -> None:
        """Prior-variant mode: summary field is merged on top of LLM draft."""
        mem = _memory_with_work_item(summary="Old summary")
        patch = apply_brief_correction({"workItem": {"summary": "Updated summary"}}, mem)
        assert patch[LIFECYCLE_MEMORY_NS]["workItem"]["summary"] == "Updated summary"

    def test_acceptance_criteria_merged_on_top_of_existing(self) -> None:
        mem = _memory_with_work_item(acceptance_criteria=["old AC"])
        patch = apply_brief_correction(
            {"workItem": {"acceptanceCriteria": ["new AC-1", "new AC-2"]}}, mem
        )
        assert patch[LIFECYCLE_MEMORY_NS]["workItem"]["acceptanceCriteria"] == ["new AC-1", "new AC-2"]


class TestApplyTasksKindPropagation:
    def test_kind_written_to_memory_for_new_task(self) -> None:
        mem = _empty_memory()
        patch = apply_tasks_correction(
            {"tasks": [{"id": "T-1", "title": "Mockup task", "kind": "mockup"}]},
            mem,
        )
        tasks = patch[LIFECYCLE_MEMORY_NS]["tasks"]
        assert tasks[0]["kind"] == "mockup"

    def test_kind_defaults_to_feature_when_absent(self) -> None:
        mem = _empty_memory()
        patch = apply_tasks_correction(
            {"tasks": [{"id": "T-1", "title": "Plain task"}]},
            mem,
        )
        tasks = patch[LIFECYCLE_MEMORY_NS]["tasks"]
        assert tasks[0]["kind"] == "feature"


class TestApplyMockupApprovalWithHtml:
    def test_approve_with_html_writes_mockups_sidecar(self) -> None:
        mem = _memory_with_current_task("T-001")
        html = "<!DOCTYPE html><html><body>Mockup</body></html>"
        patch = apply_mockup_approval({"verdict": "approve", "mockupHtml": html}, mem)
        assert "mockups" in patch
        assert patch["mockups"]["T-001"]["mockup_html"] == html

    def test_approve_without_html_returns_empty_patch(self) -> None:
        mem = _memory_with_current_task("T-001")
        patch = apply_mockup_approval({"verdict": "approve"}, mem)
        assert patch == {}

    def test_reject_returns_rejection_patch_not_mockups(self) -> None:
        mem = _memory_with_current_task("T-001")
        patch = apply_mockup_approval({"verdict": "reject", "feedback": "redo"}, mem)
        assert "rejections" in patch
        assert "mockups" not in patch


# ---------------------------------------------------------------------------
# 3. Executor coverage
# ---------------------------------------------------------------------------


class TestRegisterLifecycleV06HumanCoverage:
    def test_satisfies_coverage_validator(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        register_lifecycle_v06_human(
            registry,
            _AGENT_REF,
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
            workflow_ids={
                "work_item_workflow": uuid.uuid4(),
                "task_workflow": uuid.uuid4(),
            },
        )
        agents = [a for a in list_agents(_AGENTS_DIR) if a.ref == _AGENT_REF]
        decls = [{"ref": a.ref, "nodes": [{"name": n.name} for n in a.nodes]} for a in agents]

        from app.modules.ai.executors.coverage import validate_executor_coverage

        validate_executor_coverage(registry, decls)

    def test_executor_topology(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Every node binds to the expected executor type."""
        registry = ExecutorRegistry()
        register_lifecycle_v06_human(
            registry,
            _AGENT_REF,
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
            workflow_ids={
                "work_item_workflow": uuid.uuid4(),
                "task_workflow": uuid.uuid4(),
            },
        )

        expected: dict[str, type[Any]] = {
            # engine-create
            "register_work_item": EngineCreateExecutor,
            # fanout
            "propose_tasks": ProposeTasksExecutor,
            # human checkpoints
            "confirm_brief": HumanExecutor,
            "confirm_tasks": HumanExecutor,
            "confirm_task_review": HumanExecutor,
            "confirm_assignment": HumanExecutor,
            "confirm_mockup": HumanExecutor,
            "confirm_plan": HumanExecutor,
            "request_implementation": HumanExecutor,
            "human_review_implementation": HumanExecutor,
            "confirm_docs_update": HumanExecutor,
            # sequence engine (multi-hop)
            "approve_plan": SequenceEngineExecutor,
            "close_work_item": SequenceEngineExecutor,
            # single-hop engine executors
            "assign_task": EngineExecutor,
            "approve_review": EngineExecutor,
            # special idempotent T9
            "submit_implementation": SubmitImplementationExecutor,
            # artifact commit nodes (FEAT-019)
            "log_run_started": LocalExecutor,
            "commit_brief": LocalExecutor,
            "commit_tasks": LocalExecutor,
            "commit_plan": LocalExecutor,
            "commit_review": LocalExecutor,
            "log_run_completed": LocalExecutor,
            # local state machines
            "mark_task_done": LocalExecutor,
            "correct_implementation": LocalExecutor,
            "terminate_correction_budget": LocalExecutor,
            "terminate_rejection_budget": LocalExecutor,
        }
        for node_name, expected_type in expected.items():
            binding = registry.resolve(_AGENT_REF, node_name)
            assert isinstance(binding.executor, expected_type), (
                f"node {node_name!r}: expected {expected_type.__name__}, "
                f"got {type(binding.executor).__name__}"
            )

    def test_lifecycle_client_none_raises_runtime_error(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        with pytest.raises(RuntimeError, match="lifecycle_client is required"):
            register_lifecycle_v06_human(
                registry,
                _AGENT_REF,
                lifecycle_client=None,
                session_factory=session_factory,
                workflow_ids={"work_item": uuid.uuid4(), "task": uuid.uuid4()},
            )

    def test_missing_workflow_ids_raises_runtime_error(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        with pytest.raises(RuntimeError, match="workflow_ids must contain"):
            register_lifecycle_v06_human(
                registry,
                _AGENT_REF,
                lifecycle_client=stub_lifecycle_client,
                session_factory=session_factory,
                workflow_ids={"wrong_key": uuid.uuid4()},
            )

    def test_unwired_raises_coverage_error(self) -> None:
        registry = ExecutorRegistry()
        with pytest.raises(ExecutorCoverageError) as exc_info:
            run_coverage_validation(registry, _AGENTS_DIR)
        message = str(exc_info.value)
        for node_name in (
            "confirm_brief",
            "register_work_item",
            "confirm_tasks",
            "propose_tasks",
            "confirm_assignment",
            "confirm_plan",
            "approve_plan",
            "request_implementation",
            "submit_implementation",
            "human_review_implementation",
            "approve_review",
            "mark_task_done",
            "close_work_item",
        ):
            assert node_name in message, (
                f"missing node {node_name!r} in coverage error"
            )


# ---------------------------------------------------------------------------
# 3b. FEAT-019 builder and intake tests
# ---------------------------------------------------------------------------


class TestTasksReviewedPayload:
    def test_default_verdict_is_approve(self) -> None:
        p = TasksReviewedPayload.model_validate({})
        assert p.verdict == "approve"

    def test_reject_with_feedback(self) -> None:
        p = TasksReviewedPayload.model_validate({"verdict": "reject", "feedback": "too many tasks"})
        assert p.verdict == "reject"
        assert p.feedback == "too many tasks"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TasksReviewedPayload.model_validate({"unknownField": True})


class TestDocsUpdateConfirmedPayload:
    def test_default_verdict_is_approve(self) -> None:
        p = DocsUpdateConfirmedPayload.model_validate({})
        assert p.verdict == "approve"

    def test_reject_with_feedback(self) -> None:
        p = DocsUpdateConfirmedPayload.model_validate(
            {"verdict": "reject", "feedback": "brief not committed"}
        )
        assert p.verdict == "reject"
        assert p.feedback == "brief not committed"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DocsUpdateConfirmedPayload.model_validate({"notes": "x", "extra": "y"})


class TestApplyTaskReviewVerdict:
    def test_approve_returns_empty_patch(self) -> None:
        mem = _empty_memory()
        patch = apply_task_review_verdict({"verdict": "approve"}, mem)
        assert patch == {}

    def test_approve_default_returns_empty_patch(self) -> None:
        mem = _empty_memory()
        patch = apply_task_review_verdict({}, mem)
        assert patch == {}

    def test_reject_writes_rejection_sidecar(self) -> None:
        mem = _empty_memory()
        patch = apply_task_review_verdict(
            {"verdict": "reject", "feedback": "missing chore tasks"}, mem
        )
        assert "rejections" in patch
        assert patch["rejections"]["confirm_task_review"]["feedback"] == "missing chore tasks"
        assert patch["rejections"]["confirm_task_review"]["attempt"] == 1

    def test_reject_increments_attempt_on_second_rejection(self) -> None:
        mem: dict[str, Any] = {
            **_empty_memory(),
            "rejections": {"confirm_task_review": {"feedback": "first", "attempt": 1}},
        }
        patch = apply_task_review_verdict(
            {"verdict": "reject", "feedback": "still missing"}, mem
        )
        assert patch["rejections"]["confirm_task_review"]["attempt"] == 2

    def test_reject_preserves_other_rejection_keys(self) -> None:
        mem: dict[str, Any] = {
            **_empty_memory(),
            "rejections": {"confirm_brief": {"feedback": "old", "attempt": 1}},
        }
        patch = apply_task_review_verdict({"verdict": "reject", "feedback": "nope"}, mem)
        assert "confirm_brief" in patch["rejections"]
        assert "confirm_task_review" in patch["rejections"]


class TestApplyDocsUpdateVerdict:
    def test_approve_returns_empty_patch(self) -> None:
        mem = _empty_memory()
        patch = apply_docs_update_verdict({"verdict": "approve"}, mem)
        assert patch == {}

    def test_reject_writes_rejection_sidecar(self) -> None:
        mem = _empty_memory()
        patch = apply_docs_update_verdict(
            {"verdict": "reject", "feedback": "brief not committed"}, mem
        )
        assert "rejections" in patch
        assert patch["rejections"]["confirm_docs_update"]["feedback"] == "brief not committed"
        assert patch["rejections"]["confirm_docs_update"]["attempt"] == 1

    def test_reject_increments_attempt_on_second_rejection(self) -> None:
        mem: dict[str, Any] = {
            **_empty_memory(),
            "rejections": {"confirm_docs_update": {"feedback": "first time", "attempt": 1}},
        }
        patch = apply_docs_update_verdict({"verdict": "reject", "feedback": "again"}, mem)
        assert patch["rejections"]["confirm_docs_update"]["attempt"] == 2


class TestIntakeForConfirmTaskReview:
    def test_surfaces_tasks_list(self) -> None:
        task = LifecycleTask(id="T-1", title="Task One", kind="feature")
        from app.modules.ai.tools.lifecycle.memory import WorkItemRef

        mem_obj = __import__(
            "app.modules.ai.tools.lifecycle.memory", fromlist=["LifecycleMemory"]
        ).LifecycleMemory(
            work_item=WorkItemRef(id="FEAT-100", type="FEAT", title="Title", path=""),
            tasks=[task],
        )
        from app.modules.ai.tools.lifecycle.memory import to_run_memory

        mem = {LIFECYCLE_MEMORY_NS: to_run_memory(mem_obj)}

        intake = intake_for_confirm_task_review(mem)
        assert len(intake["tasks"]) == 1
        assert intake["tasks"][0]["id"] == "T-1"
        assert intake["priorFeedback"] is None

    def test_surfaces_prior_rejection_feedback(self) -> None:
        mem: dict[str, Any] = {
            **_empty_memory(),
            "rejections": {
                "confirm_task_review": {"feedback": "missing chore tasks", "attempt": 1}
            },
        }
        intake = intake_for_confirm_task_review(mem)
        assert intake["priorFeedback"] == "missing chore tasks"

    def test_no_prior_feedback_when_rejection_key_missing(self) -> None:
        mem: dict[str, Any] = {
            **_empty_memory(),
            "rejections": {"confirm_brief": {"feedback": "other", "attempt": 1}},
        }
        intake = intake_for_confirm_task_review(mem)
        assert intake["priorFeedback"] is None


class TestIntakeForConfirmDocsUpdate:
    def test_surfaces_work_item_and_tasks(self) -> None:
        mem = _memory_with_work_item(ref="FEAT-200", title="My Feature")
        intake = intake_for_confirm_docs_update(mem)
        assert intake["workItem"] is not None
        assert intake["workItem"]["id"] == "FEAT-200"
        assert isinstance(intake["tasks"], list)
        assert intake["completedTaskIds"] == []
        assert intake["priorFeedback"] is None

    def test_surfaces_completed_task_ids(self) -> None:
        from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, WorkItemRef, to_run_memory

        task = LifecycleTask(id="T-1", title="Task One", kind="feature")
        mem_obj = LifecycleMemory(
            work_item=WorkItemRef(id="FEAT-100", type="FEAT", title="Title", path=""),
            tasks=[task],
            completed_task_ids=["T-1"],
        )
        mem = {LIFECYCLE_MEMORY_NS: to_run_memory(mem_obj)}
        intake = intake_for_confirm_docs_update(mem)
        assert "T-1" in intake["completedTaskIds"]

    def test_surfaces_prior_docs_rejection_feedback(self) -> None:
        mem: dict[str, Any] = {
            **_memory_with_work_item(),
            "rejections": {
                "confirm_docs_update": {"feedback": "brief not committed", "attempt": 1}
            },
        }
        intake = intake_for_confirm_docs_update(mem)
        assert intake["priorFeedback"] == "brief not committed"

    def test_no_prior_feedback_when_other_rejection_present(self) -> None:
        mem: dict[str, Any] = {
            **_memory_with_work_item(),
            "rejections": {"confirm_tasks": {"feedback": "something", "attempt": 1}},
        }
        intake = intake_for_confirm_docs_update(mem)
        assert intake["priorFeedback"] is None


# ---------------------------------------------------------------------------
# 4. No LLM imports — subprocess-based for a clean sys.modules baseline.
# ---------------------------------------------------------------------------


def test_register_v06_human_does_not_import_core_llm() -> None:
    """Registration path must never import core.llm or any LLM SDK.

    Runs in a subprocess so the test harness's own ``app.core.llm`` load
    (via ``_test_env`` → settings → provider factory) doesn't pollute the
    measurement.  Mirrors ``test_runtime_deterministic_is_pure.py``.
    """
    src = (
        "import sys\n"
        "from app.modules.ai.executors.bootstrap import register_lifecycle_v06_human  # noqa: F401\n"
        "leaked = [\n"
        "    m for m in sys.modules\n"
        "    if m == 'app.core.llm'\n"
        "    or m == 'anthropic' or m.startswith('anthropic.')\n"
        "    or m == 'openai' or m.startswith('openai.')\n"
        "    or m == 'app.core.llm_anthropic'\n"
        "]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [__import__("sys").executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "register_lifecycle_v06_human pulls in an LLM SDK or core.llm:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
