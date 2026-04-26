"""``register_engine_executor`` bootstrap-helper tests (FEAT-010 / T-232)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.executors.bootstrap import register_engine_executor
from app.modules.ai.executors.registry import (
    ExecutorRegistry,
    ExecutorRegistryError,
)


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def stub_lifecycle_client() -> Any:
    # The helper only stores the client on the executor; no methods are
    # called during registration, so a MagicMock is sufficient.
    return MagicMock(name="FlowEngineLifecycleClient")


class TestHappyPath:
    def test_registers_engine_executor(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        binding = register_engine_executor(
            registry,
            "test-agent@0.1.0",
            "request_engine_transition",
            transition_key="work_item.W2",
            to_status="review",
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
        )
        assert binding.agent_ref == "test-agent@0.1.0"
        assert binding.node_name == "request_engine_transition"
        assert binding.executor.mode == "engine"
        assert binding.executor.name == "engine:work_item.W2"
        assert registry.has("test-agent@0.1.0", "request_engine_transition")

    def test_resolves_to_engine_executor(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        register_engine_executor(
            registry,
            "test-agent@0.1.0",
            "n",
            transition_key="task.T6",
            to_status="impl_review",
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
        )
        binding = registry.resolve("test-agent@0.1.0", "n")
        assert binding.executor.mode == "engine"


class TestErrorPaths:
    def test_none_lifecycle_client_raises_with_helpful_message(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        with pytest.raises(RuntimeError) as excinfo:
            register_engine_executor(
                registry,
                "test-agent@0.1.0",
                "n",
                transition_key="work_item.W2",
                to_status="review",
                lifecycle_client=None,
                session_factory=session_factory,
            )
        msg = str(excinfo.value)
        assert "test-agent@0.1.0" in msg
        assert "'n'" in msg
        # Operator should learn about the no_executor exemption fallback.
        assert "no_executor" in msg
        # The binding must NOT be registered when validation fails.
        assert not registry.has("test-agent@0.1.0", "n")

    def test_duplicate_registration_raises(
        self,
        stub_lifecycle_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        registry = ExecutorRegistry()
        register_engine_executor(
            registry,
            "test-agent@0.1.0",
            "n",
            transition_key="work_item.W2",
            to_status="review",
            lifecycle_client=stub_lifecycle_client,
            session_factory=session_factory,
        )
        with pytest.raises(ExecutorRegistryError):
            register_engine_executor(
                registry,
                "test-agent@0.1.0",
                "n",
                transition_key="work_item.W2",
                to_status="review",
                lifecycle_client=stub_lifecycle_client,
                session_factory=session_factory,
            )
