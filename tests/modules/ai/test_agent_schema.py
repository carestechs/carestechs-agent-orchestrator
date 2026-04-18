"""Tests for ``app.modules.ai.agents`` Pydantic schema (T-031)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.modules.ai.agents import (
    AgentDefinition,
    AgentFlow,
    AgentNode,
    AgentPolicy,
    BudgetDefaults,
    _parse_file,
)

_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "fixtures" / "agents" / "sample-linear.yaml"
)


def _valid_payload() -> dict[str, Any]:
    """Raw dict shape used across the negative tests."""
    return {
        "ref": "demo",
        "version": "1.0",
        "description": "demo",
        "nodes": [
            {"name": "n1", "description": "d1"},
            {"name": "n2", "description": "d2"},
        ],
        "flow": {"entryNode": "n1"},
        "terminalNodes": ["n2"],
    }


class TestFixtureLoadsCleanly:
    def test_sample_linear_parses(self) -> None:
        raw = yaml.safe_load(_FIXTURE_PATH.read_text())
        agent = AgentDefinition.model_validate(raw)

        assert agent.ref == "sample-linear"
        assert agent.version == "1.0"
        assert [n.name for n in agent.nodes] == ["analyze_brief", "draft_plan", "review_plan"]
        assert agent.flow.entry_node == "analyze_brief"
        assert agent.terminal_nodes == {"review_plan"}
        assert agent.default_budget.max_steps == 10


class TestFieldValidation:
    def test_missing_required_field_rejected(self) -> None:
        payload = _valid_payload()
        del payload["ref"]
        with pytest.raises(ValidationError):
            AgentDefinition.model_validate(payload)

    def test_empty_terminal_nodes_rejected(self) -> None:
        payload = _valid_payload()
        payload["terminalNodes"] = []
        with pytest.raises(ValidationError, match="terminal_nodes must be non-empty"):
            AgentDefinition.model_validate(payload)

    def test_unknown_terminal_node_rejected(self) -> None:
        payload = _valid_payload()
        payload["terminalNodes"] = ["does_not_exist"]
        with pytest.raises(ValidationError, match="unknown nodes"):
            AgentDefinition.model_validate(payload)

    def test_entry_node_not_in_nodes_rejected(self) -> None:
        payload = _valid_payload()
        payload["flow"]["entryNode"] = "phantom"
        with pytest.raises(ValidationError, match="entry_node"):
            AgentDefinition.model_validate(payload)

    def test_duplicate_node_names_rejected(self) -> None:
        payload = _valid_payload()
        payload["nodes"].append({"name": "n1", "description": "duplicate"})
        with pytest.raises(ValidationError, match="unique"):
            AgentDefinition.model_validate(payload)

    def test_reserved_terminate_name_rejected(self) -> None:
        payload = _valid_payload()
        payload["nodes"][0]["name"] = "terminate"
        payload["flow"]["entryNode"] = "terminate"
        payload["terminalNodes"] = ["n2"]
        with pytest.raises(ValidationError, match="reserved"):
            AgentDefinition.model_validate(payload)


class TestRoundTrip:
    def test_model_dump_stable(self) -> None:
        raw = yaml.safe_load(_FIXTURE_PATH.read_text())
        a = AgentDefinition.model_validate(raw)
        b = AgentDefinition.model_validate(a.model_dump(by_alias=True))
        assert a == b


class TestDefaults:
    def test_budget_defaults_empty(self) -> None:
        payload = _valid_payload()
        agent = AgentDefinition.model_validate(payload)
        assert agent.default_budget == BudgetDefaults()
        assert agent.default_budget.max_steps is None

    def test_node_timeout_default(self) -> None:
        node = AgentNode(name="x", description="y")
        assert node.timeout_seconds == 300

    def test_flow_transitions_default_empty(self) -> None:
        flow = AgentFlow(entry_node="x")
        assert flow.transitions == {}


class TestAgentPolicy:
    """FEAT-005 / T-087 — per-node system-prompt references."""

    def test_agent_policy_defaults_empty(self) -> None:
        payload = _valid_payload()
        agent = AgentDefinition.model_validate(payload)
        assert agent.policy == AgentPolicy()
        assert agent.policy.system_prompts == {}

    def test_system_prompts_rejects_unknown_node(self) -> None:
        payload = _valid_payload()
        payload["policy"] = {"systemPrompts": {"bogus": "prompts/p.md"}}
        with pytest.raises(ValidationError, match="references unknown nodes"):
            AgentDefinition.model_validate(payload)

    def test_system_prompts_happy_path_loads(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompts" / "analyze.md"
        prompt_file.parent.mkdir()
        prompt_file.write_text("# system prompt\n")

        payload = _valid_payload()
        payload["policy"] = {"systemPrompts": {"n1": "prompts/analyze.md"}}
        yaml_path = tmp_path / "sample.yaml"
        yaml_path.write_text(yaml.safe_dump(payload))

        agent = _parse_file(yaml_path, repo_root=tmp_path)
        assert agent.policy.system_prompts == {"n1": Path("prompts/analyze.md")}
        assert agent.agent_definition_hash is not None

    def test_system_prompts_rejects_missing_file(self, tmp_path: Path) -> None:
        payload = _valid_payload()
        payload["policy"] = {"systemPrompts": {"n1": "prompts/does-not-exist.md"}}
        yaml_path = tmp_path / "sample.yaml"
        yaml_path.write_text(yaml.safe_dump(payload))

        with pytest.raises(ValueError, match="prompt file not found"):
            _parse_file(yaml_path, repo_root=tmp_path)

    def test_system_prompts_rejects_escape_root(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.md"
        outside.write_text("# outside\n")
        try:
            payload = _valid_payload()
            payload["policy"] = {"systemPrompts": {"n1": "../outside.md"}}
            yaml_path = tmp_path / "sample.yaml"
            yaml_path.write_text(yaml.safe_dump(payload))

            with pytest.raises(ValueError, match="escapes repo root"):
                _parse_file(yaml_path, repo_root=tmp_path)
        finally:
            outside.unlink(missing_ok=True)
