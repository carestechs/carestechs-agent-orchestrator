"""Edge-case tests for the agent YAML loader (T-048).

The happy path is covered by :mod:`tests.modules.ai.test_agents_loader`.
This module exercises the corners: missing / misshapen directories,
reserved-name rule, hash stability, unicode, and unreadable files.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.exceptions import NotFoundError
from app.modules.ai.agents import list_agent_records, list_agents, load_agent

_SAMPLE = (
    Path(__file__).parent.parent.parent / "fixtures" / "agents" / "sample-linear.yaml"
)


# ---------------------------------------------------------------------------
# Missing / misshapen directories
# ---------------------------------------------------------------------------


class TestMissingDir:
    def test_missing_subpath_yields_empty_list(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent"
        assert list_agents(missing) == []
        assert list_agent_records(missing) == []

    def test_missing_subpath_load_agent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(NotFoundError):
            load_agent("sample-linear", tmp_path / "absent")

    def test_path_that_is_a_file_not_a_dir_yields_empty(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "file.txt"
        not_a_dir.write_text("")
        assert list_agents(not_a_dir) == []
        with pytest.raises(NotFoundError):
            load_agent("any", not_a_dir)


# ---------------------------------------------------------------------------
# Duplicate refs / filename precedence
# ---------------------------------------------------------------------------


class TestDuplicateRefs:
    def test_versioned_file_wins_over_bare_when_both_present(
        self, tmp_path: Path
    ) -> None:
        shutil.copy(_SAMPLE, tmp_path / "sample-linear.yaml")
        shutil.copy(_SAMPLE, tmp_path / "sample-linear@1.0.yaml")

        # Bare ref resolves to the versioned file — documented precedence.
        agent = load_agent("sample-linear", tmp_path)
        assert agent.version == "1.0"


# ---------------------------------------------------------------------------
# Hash stability
# ---------------------------------------------------------------------------


class TestHashStability:
    def test_identical_files_hash_identically(self, tmp_path: Path) -> None:
        a_path = tmp_path / "a@1.0.yaml"
        b_path = tmp_path / "b@1.0.yaml"
        a_path.write_text(_SAMPLE.read_text())
        b_path.write_text(_SAMPLE.read_text())

        a = load_agent("a@1.0", tmp_path)
        b = load_agent("b@1.0", tmp_path)
        assert a.agent_definition_hash == b.agent_definition_hash

    def test_whitespace_only_change_keeps_hash(self, tmp_path: Path) -> None:
        """Canonicalization parses YAML → re-serializes sorted JSON, so
        whitespace differences on the source are absorbed."""
        path = tmp_path / "x@1.0.yaml"
        path.write_text(_SAMPLE.read_text())
        first = load_agent("x@1.0", tmp_path).agent_definition_hash

        # Add a trailing blank line; re-write.
        path.write_text(_SAMPLE.read_text() + "\n\n")
        second = load_agent("x@1.0", tmp_path).agent_definition_hash
        assert first == second

    def test_content_change_changes_hash(self, tmp_path: Path) -> None:
        path = tmp_path / "y@1.0.yaml"
        path.write_text(_SAMPLE.read_text())
        before = load_agent("y@1.0", tmp_path).agent_definition_hash

        path.write_text(_SAMPLE.read_text().replace("Minimal", "Modified"))
        after = load_agent("y@1.0", tmp_path).agent_definition_hash
        assert before != after


# ---------------------------------------------------------------------------
# Unicode / i18n
# ---------------------------------------------------------------------------


class TestUnicode:
    def test_non_ascii_description_round_trips(self, tmp_path: Path) -> None:
        # Wrap the replacement in quotes so the ``:`` doesn't trip YAML.
        src = _SAMPLE.read_text().replace(
            "description: Minimal 3-node linear agent for composition-integrity tests.",
            'description: "Agente de três nós — não-ASCII café, ångström, 日本語."',
        )
        path = tmp_path / "unicode@1.0.yaml"
        path.write_text(src, encoding="utf-8")

        a = load_agent("unicode@1.0", tmp_path)
        assert "日本語" in a.description
        b = load_agent("unicode@1.0", tmp_path)
        assert a.agent_definition_hash == b.agent_definition_hash


# ---------------------------------------------------------------------------
# Reserved ``terminate`` node name
# ---------------------------------------------------------------------------


class TestReservedName:
    def test_node_named_terminate_is_rejected(self, tmp_path: Path) -> None:
        src = """
ref: bad-agent
version: "1.0"
description: uses reserved name
nodes:
  - name: terminate
    description: should be rejected
    inputSchema: {}
terminalNodes: [terminate]
flow:
  entryNode: terminate
""".strip()
        (tmp_path / "bad-agent@1.0.yaml").write_text(src)

        with pytest.raises(ValidationError):
            load_agent("bad-agent@1.0", tmp_path)


# ---------------------------------------------------------------------------
# Unreadable file
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file-mode restrictions")
class TestUnreadableFile:
    def test_list_agents_skips_unreadable_file(self, tmp_path: Path) -> None:
        good = tmp_path / "good@1.0.yaml"
        good.write_text(_SAMPLE.read_text())
        bad = tmp_path / "bad@1.0.yaml"
        bad.write_text(_SAMPLE.read_text())
        os.chmod(bad, 0o000)

        try:
            agents = list_agents(tmp_path)
            # Good file loads; bad file is skipped with a WARNING log.
            assert any(a.ref == "sample-linear" for a in agents)
        finally:
            os.chmod(bad, 0o600)

    def test_load_agent_on_unreadable_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad@1.0.yaml"
        bad.write_text(_SAMPLE.read_text())
        os.chmod(bad, 0o000)

        try:
            with pytest.raises(PermissionError):
                load_agent("bad@1.0", tmp_path)
        finally:
            os.chmod(bad, 0o600)
