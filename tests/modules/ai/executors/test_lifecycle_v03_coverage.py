"""AC-6 coverage validator test for ``register_lifecycle_v03`` (FEAT-011 / T-265).

After ``register_lifecycle_v03`` is called with stubs, every node in the
v0.3.0 YAML is bound (or explicitly exempted) — the coverage validator
boots cleanly.  Without the call, the validator raises a
:class:`ExecutorCoverageError` naming every unbound node.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.agents import list_agents
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.bootstrap import (
    ExecutorCoverageError,
    register_lifecycle_v03,
    run_coverage_validation,
)
from app.modules.ai.executors.composite import CompositeLLMEngineExecutor
from app.modules.ai.executors.engine import EngineExecutor
from app.modules.ai.executors.engine_create import EngineCreateExecutor
from app.modules.ai.executors.human import HumanExecutor
from app.modules.ai.executors.llm_content import LLMContentExecutor
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.propose_tasks import ProposeTasksExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.executors.sequence import SequenceEngineExecutor
from app.modules.ai.executors.submit_implementation import (
    SubmitImplementationExecutor,
)

_AGENTS_DIR = Path(__file__).resolve().parents[4] / "agents"
_AGENT_REF = "lifecycle-agent@0.3.0"


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def stub_lifecycle_client() -> Any:
    return MagicMock(name="FlowEngineLifecycleClient")


@pytest.fixture
def stub_llm_provider() -> Any:
    provider = MagicMock(name="LLMProvider")
    provider.name = "stub"
    provider.model = "stub-1"
    return provider


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    """Each test starts with a clean exemption table."""
    _reset_exemptions_for_tests()


class TestCoverageWiredV03:
    def test_register_lifecycle_v03_satisfies_coverage(
        self,
        stub_lifecycle_client: Any,
        stub_llm_provider: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        register_lifecycle_v03(
            registry,
            _AGENT_REF,
            lifecycle_client=stub_lifecycle_client,
            llm_provider=stub_llm_provider,
            session_factory=session_factory,
            workflow_ids={
                "work_item_workflow": uuid.uuid4(),
                "task_workflow": uuid.uuid4(),
            },
        )

        # Restrict coverage validation to just the v0.3.0 agent.
        agents = [a for a in list_agents(_AGENTS_DIR) if a.ref == _AGENT_REF]
        decls = [{"ref": a.ref, "nodes": [{"name": n.name} for n in a.nodes]} for a in agents]
        # ``run_coverage_validation`` reads the directory; reuse its
        # internal helper via the public ``validate_executor_coverage``.
        from app.modules.ai.executors.coverage import validate_executor_coverage

        validate_executor_coverage(registry, decls)

    def test_executor_topology_matches_design_doc(
        self,
        stub_lifecycle_client: Any,
        stub_llm_provider: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Every node binds to the type the design-doc mapping table promises."""
        registry = ExecutorRegistry()
        register_lifecycle_v03(
            registry,
            _AGENT_REF,
            lifecycle_client=stub_lifecycle_client,
            llm_provider=stub_llm_provider,
            session_factory=session_factory,
            workflow_ids={
                "work_item_workflow": uuid.uuid4(),
                "task_workflow": uuid.uuid4(),
            },
        )

        expected: dict[str, type[Any]] = {
            "load_work_item": LLMContentExecutor,
            "register_work_item": EngineCreateExecutor,
            "generate_tasks": LLMContentExecutor,
            "propose_tasks": ProposeTasksExecutor,
            "assign_task": EngineExecutor,
            "generate_plan": CompositeLLMEngineExecutor,
            "approve_plan": EngineExecutor,
            "request_implementation": HumanExecutor,
            "submit_implementation": SubmitImplementationExecutor,
            "review_implementation": LLMContentExecutor,
            "approve_review": EngineExecutor,
            "correct_implementation": LocalExecutor,
            "close_work_item": SequenceEngineExecutor,
        }
        for node_name, expected_type in expected.items():
            binding = registry.resolve(_AGENT_REF, node_name)
            assert isinstance(binding.executor, expected_type), (
                f"node {node_name!r}: expected {expected_type.__name__}, "
                f"got {type(binding.executor).__name__}"
            )


class TestCoverageWithoutV03Wiring:
    def test_unwired_v03_raises_coverage_error(self) -> None:
        registry = ExecutorRegistry()
        # No call to register_lifecycle_v03; no exemptions either.
        with pytest.raises(ExecutorCoverageError) as exc_info:
            run_coverage_validation(registry, _AGENTS_DIR)
        message = str(exc_info.value)
        # Every v0.3.0 node should show up in the offender list.
        for node_name in (
            "start",
            "load_work_item",
            "register_work_item",
            "generate_tasks",
            "propose_tasks",
            "assign_task",
            "generate_plan",
            "approve_plan",
            "request_implementation",
            "submit_implementation",
            "review_implementation",
            "approve_review",
            "correct_implementation",
            "close_work_item",
            "terminate_correction_budget",
        ):
            assert node_name in message, f"missing node {node_name!r} in coverage error: {message}"
