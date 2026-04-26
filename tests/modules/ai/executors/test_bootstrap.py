"""Executor bootstrap + coverage-fail-fast tests (FEAT-009 / T-214 + T-218)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.modules.ai.executors.bootstrap import (
    ExecutorCoverageError,
    register_all_executors,
    run_coverage_validation,
)
from app.modules.ai.executors.registry import ExecutorRegistry

# Repo's real agents/ dir is a clean fixture for the happy path: it
# already contains lifecycle-agent@0.1.0.yaml and nothing else.
_REAL_AGENTS_DIR = Path(__file__).resolve().parents[4] / "agents"


def test_real_agents_dir_register_then_validate_passes() -> None:
    """Registering against the live agents/ dir leaves coverage clean."""
    registry = ExecutorRegistry()
    register_all_executors(registry, _REAL_AGENTS_DIR)
    keys = registry.registered_keys()
    # lifecycle-agent@0.1.0 declares 8 nodes — every one bound.
    v01_keys = {k for k in keys if k[0].startswith("lifecycle-agent@0.1")}
    assert len(v01_keys) == 8
    # Coverage validation does not raise.
    run_coverage_validation(registry, _REAL_AGENTS_DIR)


def test_misconfigured_agents_dir_fails_coverage(tmp_path: Path) -> None:
    """A node without a binding produces a coverage error listing it."""
    # Synthesize a minimal agent YAML that the loader will accept.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    spec = {
        "ref": "demo-agent@1.0.0",
        "version": "1.0.0",
        "description": "Demo agent for coverage failure test.",
        "nodes": [
            {
                "name": "alpha",
                "description": "First node",
                "inputSchema": {"type": "object"},
            },
            {
                "name": "beta",
                "description": "Second node",
                "inputSchema": {"type": "object"},
            },
        ],
        "flow": {
            "entryNode": "alpha",
            "transitions": {"alpha": ["beta"], "beta": []},
        },
        "intakeSchema": {"type": "object"},
        "terminalNodes": ["beta"],
    }
    (agents_dir / "demo-agent@1.0.0.yaml").write_text(yaml.safe_dump(spec))

    registry = ExecutorRegistry()
    # The bootstrap only auto-registers lifecycle-agent@0.1.x; this synthetic
    # agent has nothing registered for it.
    register_all_executors(registry, agents_dir)

    with pytest.raises(ExecutorCoverageError) as excinfo:
        run_coverage_validation(registry, agents_dir)
    msg = str(excinfo.value)
    assert "'demo-agent@1.0.0' :: 'alpha'" in msg
    assert "'demo-agent@1.0.0' :: 'beta'" in msg


def test_v01_placeholder_handler_raises_if_invoked() -> None:
    """The v0.1.0 placeholder handler must fail loud — actual wiring lands in PR 5."""
    import asyncio
    import uuid

    from app.modules.ai.executors.base import DispatchContext

    registry = ExecutorRegistry()
    register_all_executors(registry, _REAL_AGENTS_DIR)
    binding = registry.resolve("lifecycle-agent@0.1.0", "load_work_item")
    ctx = DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.1.0",
        node_name="load_work_item",
        intake={},
    )
    env = asyncio.run(binding.executor.dispatch(ctx))
    # LocalExecutor catches the NotImplementedError and renders it as
    # a failed envelope; that's the contract we promised in T-214.
    assert env.state.value == "failed"
    assert env.detail is not None
    assert "NotImplementedError" in env.detail
    assert "T-220" in env.detail
