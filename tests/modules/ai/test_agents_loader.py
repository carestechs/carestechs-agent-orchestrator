"""Tests for the agent YAML loader (T-032)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.core.exceptions import NotFoundError
from app.modules.ai.agents import list_agents, load_agent

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_SAMPLE = _REPO_ROOT / "tests" / "fixtures" / "agents" / "sample-linear.yaml"


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Populate a temp agents dir with a renamed copy of the fixture."""
    target = tmp_path / "sample-linear@1.0.yaml"
    shutil.copy(_SAMPLE, target)
    return tmp_path


class TestLoadAgent:
    def test_versioned_ref(self, agents_dir: Path) -> None:
        agent = load_agent("sample-linear@1.0", agents_dir)
        assert agent.ref == "sample-linear"
        assert agent.version == "1.0"
        assert agent.agent_definition_hash is not None
        assert len(agent.agent_definition_hash) == 64  # sha256 hex

    def test_bare_ref_matches_versioned_file(self, agents_dir: Path) -> None:
        agent = load_agent("sample-linear", agents_dir)
        assert agent.version == "1.0"

    def test_unknown_ref_raises(self, agents_dir: Path) -> None:
        with pytest.raises(NotFoundError, match="agent not found"):
            load_agent("does-not-exist", agents_dir)

    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        with pytest.raises(NotFoundError):
            load_agent("sample-linear@1.0", missing)

    def test_hash_is_deterministic(self, agents_dir: Path) -> None:
        a = load_agent("sample-linear@1.0", agents_dir)
        b = load_agent("sample-linear@1.0", agents_dir)
        assert a.agent_definition_hash == b.agent_definition_hash

    def test_hash_changes_with_content(self, agents_dir: Path) -> None:
        original = load_agent("sample-linear@1.0", agents_dir)
        path = agents_dir / "sample-linear@1.0.yaml"
        path.write_text(path.read_text().replace("Minimal", "Modified"))
        mutated = load_agent("sample-linear@1.0", agents_dir)
        assert original.agent_definition_hash != mutated.agent_definition_hash

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "broken@1.0.yaml").write_text("ref: x\nversion: 1\nnodes: [")
        with pytest.raises(yaml.YAMLError):
            load_agent("broken@1.0", tmp_path)

    def test_schema_failure_raises_validation_error(self, tmp_path: Path) -> None:
        (tmp_path / "bad-schema@1.0.yaml").write_text(
            'ref: x\nversion: 1.0\ndescription: d\nnodes: []\nflow: {entryNode: x}\nterminalNodes: []\n'
        )
        with pytest.raises(ValidationError):
            load_agent("bad-schema@1.0", tmp_path)


class TestListAgents:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert list_agents(tmp_path / "absent") == []

    def test_lists_and_sorts(self, tmp_path: Path) -> None:
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")
        # second fixture: mutate the ref so sorting is observable
        src = _SAMPLE.read_text().replace("sample-linear", "zulu-agent")
        (tmp_path / "zulu-agent@1.0.yaml").write_text(src)

        agents = list_agents(tmp_path)
        assert [a.ref for a in agents] == ["sample-linear", "zulu-agent"]

    def test_skips_unreadable_files(self, tmp_path: Path) -> None:
        """A malformed YAML is skipped rather than aborting the listing."""
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")
        (tmp_path / "broken@1.0.yaml").write_text("not: {valid: yaml")
        agents = list_agents(tmp_path)
        assert len(agents) == 1
        assert agents[0].ref == "sample-linear"

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert list_agents(tmp_path) == []
