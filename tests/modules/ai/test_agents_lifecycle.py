"""Tests for agents/lifecycle-agent@0.1.0.yaml (FEAT-005 / T-100).

Exercises the file directly via the loader to confirm:
- It parses into a valid ``AgentDefinition`` with 8 nodes.
- The three declared ``policy.system_prompts`` paths exist under the repo.
- ``agent_definition_hash`` is deterministic across invocations.
- Every declared node matches a local tool handler (the runtime can execute
  the whole flow without any engine dispatch).
"""

from __future__ import annotations

from pathlib import Path

from app.modules.ai.agents import load_agent
from app.modules.ai.tools.lifecycle.registry import LOCAL_TOOL_HANDLERS

_REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
_AGENTS_DIR = _REPO_ROOT / "agents"


class TestLifecycleAgentYaml:
    def test_loads(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        assert agent.ref == "lifecycle-agent@0.1.0"
        assert agent.version == "0.1.0"

    def test_has_eight_nodes(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        node_names = [n.name for n in agent.nodes]
        assert node_names == [
            "load_work_item",
            "generate_tasks",
            "assign_task",
            "generate_plan",
            "wait_for_implementation",
            "review_implementation",
            "corrections",
            "close_work_item",
        ]

    def test_entry_and_terminal_nodes(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        assert agent.flow.entry_node == "load_work_item"
        assert agent.terminal_nodes == {"close_work_item"}

    def test_transitions_declared(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        assert agent.flow.transitions["generate_plan"] == [
            "generate_plan",
            "wait_for_implementation",
        ]
        assert agent.flow.transitions["review_implementation"] == [
            "corrections",
            "close_work_item",
        ]
        assert agent.flow.transitions["close_work_item"] == []

    def test_max_steps_ceiling(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        assert agent.default_budget.max_steps == 300

    def test_prompt_files_resolve_and_exist(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        prompts = agent.policy.system_prompts
        assert set(prompts.keys()) == {
            "generate_tasks",
            "generate_plan",
            "review_implementation",
        }
        for node_name, rel_path in prompts.items():
            resolved = (_REPO_ROOT / rel_path).resolve()
            assert resolved.is_file(), f"prompt file missing for {node_name}: {resolved}"

    def test_hash_deterministic(self) -> None:
        first = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        second = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        assert first.agent_definition_hash == second.agent_definition_hash
        assert first.agent_definition_hash is not None
        assert len(first.agent_definition_hash) == 64  # sha256 hex

    def test_every_node_has_a_local_tool(self) -> None:
        """AD-3 reassurance: the agent is fully local-executable in v1."""
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        missing = [n.name for n in agent.nodes if n.name not in LOCAL_TOOL_HANDLERS]
        assert missing == [], f"nodes without local handlers: {missing}"

    def test_intake_schema_declares_work_item_path(self) -> None:
        agent = load_agent("lifecycle-agent@0.1.0", _AGENTS_DIR)
        intake = agent.intake_schema
        assert intake["required"] == ["workItemPath"]
        assert intake["properties"]["workItemPath"]["type"] == "string"
