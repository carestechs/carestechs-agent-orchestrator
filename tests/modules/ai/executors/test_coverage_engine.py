"""Coverage-validator engine-mode tests (FEAT-010 / T-239).

The FEAT-009 ``validate_executor_coverage`` is mode-agnostic by design —
this test confirms it handles engine-bound bindings without modification.
A deterministic agent declaring an engine-bound node must either register
an ``EngineExecutor`` or carry an explicit ``no_executor("≥10-char reason")``
exemption.  Same enforcement bar as local/remote/human paths.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.executors import (
    ExecutorCoverageError,
    ExecutorRegistry,
    no_executor,
    validate_executor_coverage,
)
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.bootstrap import register_engine_executor


@pytest.fixture(autouse=True)
def _reset() -> None:
    _reset_exemptions_for_tests()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def stub_lifecycle_client() -> Any:
    return MagicMock(name="FlowEngineLifecycleClient")


def _agent(ref: str, *node_names: str) -> dict[str, object]:
    return {"ref": ref, "nodes": [{"name": n} for n in node_names]}


class TestEngineCoverage:
    def test_unbound_engine_node_fails_coverage(self) -> None:
        """A deterministic agent with an engine-bound node and no registration fails."""
        registry = ExecutorRegistry()
        with pytest.raises(ExecutorCoverageError) as excinfo:
            validate_executor_coverage(
                registry,
                [_agent("test-agent@0.1.0", "request_engine_transition")],
            )
        msg = str(excinfo.value)
        assert "'test-agent@0.1.0'" in msg
        assert "'request_engine_transition'" in msg

    def test_no_executor_exemption_satisfies_coverage(self) -> None:
        """Engine-absent dev-mode fallback: explicit ``no_executor`` exemption works."""
        registry = ExecutorRegistry()
        no_executor(
            "test-agent@0.1.0",
            "request_engine_transition",
            reason="transitions are skipped in dev mode",
        )
        # Must not raise.
        validate_executor_coverage(
            registry,
            [_agent("test-agent@0.1.0", "request_engine_transition")],
        )

    def test_register_engine_executor_satisfies_coverage(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Wired ``register_engine_executor`` flows through the same coverage path."""
        registry = ExecutorRegistry()
        register_engine_executor(
            registry,
            "test-agent@0.1.0",
            "request_engine_transition",
            transition_key="work_item.W2",
            to_status="review",
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
        )
        # Must not raise.
        validate_executor_coverage(
            registry,
            [_agent("test-agent@0.1.0", "request_engine_transition")],
        )

    def test_mixed_modes_aggregate_unbound_correctly(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """One engine-bound node is bound; one local-mode-intended node is unbound."""
        registry = ExecutorRegistry()
        register_engine_executor(
            registry,
            "test-agent@0.1.0",
            "request_engine_transition",
            transition_key="work_item.W2",
            to_status="review",
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
        )
        with pytest.raises(ExecutorCoverageError) as excinfo:
            validate_executor_coverage(
                registry,
                [_agent("test-agent@0.1.0", "request_engine_transition", "needs_local")],
            )
        msg = str(excinfo.value)
        assert "'needs_local'" in msg
        # The engine-bound node should NOT show up as offending.
        assert "'request_engine_transition'" not in msg
