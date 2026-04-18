"""Tests for the tool-definition builder (T-034)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.llm import ToolDefinition
from app.modules.ai.agents import AgentDefinition
from app.modules.ai.tools import TERMINATE_TOOL_NAME, build_tools

_FIXTURE = (
    Path(__file__).parent.parent.parent / "fixtures" / "agents" / "sample-linear.yaml"
)


@pytest.fixture
def agent() -> AgentDefinition:
    return AgentDefinition.model_validate(yaml.safe_load(_FIXTURE.read_text()))


class TestGating:
    def test_all_nodes_available(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[n.name for n in agent.nodes])

        names = [t.name for t in tools]
        assert names == ["analyze_brief", "draft_plan", "review_plan", TERMINATE_TOOL_NAME]

    def test_partial_gate(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=["analyze_brief"])

        names = [t.name for t in tools]
        assert names == ["analyze_brief", TERMINATE_TOOL_NAME]

    def test_empty_gate_yields_only_terminate(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[])

        assert len(tools) == 1
        assert tools[0].name == TERMINATE_TOOL_NAME

    def test_unknown_name_in_available_is_ignored(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=["does_not_exist", "analyze_brief"])

        names = [t.name for t in tools]
        assert names == ["analyze_brief", TERMINATE_TOOL_NAME]


class TestToolShape:
    def test_parameters_come_from_node_input_schema(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=["analyze_brief"])
        analyze = tools[0]

        assert analyze.name == "analyze_brief"
        assert analyze.parameters["type"] == "object"
        assert "brief" in analyze.parameters["properties"]

    def test_terminate_tool_has_empty_schema(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[])
        assert tools[0].parameters == {"type": "object", "properties": {}}

    def test_every_tool_is_tool_definition(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[n.name for n in agent.nodes])
        assert all(isinstance(t, ToolDefinition) for t in tools)


class TestDeterminism:
    def test_order_matches_agent_nodes(self, agent: AgentDefinition) -> None:
        """Tool list preserves agent-declaration order, regardless of available_nodes order."""
        tools_forward = build_tools(agent, available_nodes=["analyze_brief", "draft_plan"])
        tools_reverse = build_tools(agent, available_nodes=["draft_plan", "analyze_brief"])

        assert [t.name for t in tools_forward] == [t.name for t in tools_reverse]

    def test_two_calls_return_independent_lists(self, agent: AgentDefinition) -> None:
        a = build_tools(agent, available_nodes=[n.name for n in agent.nodes])
        b = build_tools(agent, available_nodes=[n.name for n in agent.nodes])

        assert a is not b
        assert a == b


# ---------------------------------------------------------------------------
# Order + positional invariants (T-049)
# ---------------------------------------------------------------------------


class TestExactOrder:
    def test_terminate_is_always_last(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[n.name for n in agent.nodes])
        assert tools[-1].name == TERMINATE_TOOL_NAME

    def test_positions_match_agent_node_order(self, agent: AgentDefinition) -> None:
        tools = build_tools(agent, available_nodes=[n.name for n in agent.nodes])
        assert tools[0].name == "analyze_brief"
        assert tools[1].name == "draft_plan"
        assert tools[2].name == "review_plan"
        assert tools[3].name == TERMINATE_TOOL_NAME

    def test_gated_subset_preserves_declaration_order(
        self, agent: AgentDefinition
    ) -> None:
        tools = build_tools(agent, available_nodes=["review_plan", "analyze_brief"])
        assert [t.name for t in tools] == [
            "analyze_brief",
            "review_plan",
            TERMINATE_TOOL_NAME,
        ]


class TestModuleConstants:
    def test_terminate_name_is_exported_and_stable(self) -> None:
        assert TERMINATE_TOOL_NAME == "terminate"
