"""Unit tests for ``read_code_source`` accessor (IMP-005 / T-312).

The accessor centralizes the memory-sidecar → intake precedence so no
executor reimplements the merge.  Tests are pure — ``DispatchContext``
is constructed manually with a minimal field set.
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.code_source import read_code_source

_INTAKE_NO_WB: dict[str, object] = {
    "codeSource": {"repo": "org/name", "baseBranch": "main"},
}
_INTAKE_WITH_WB: dict[str, object] = {
    "codeSource": {
        "repo": "org/name",
        "baseBranch": "main",
        "workBranch": "intake/x",
    },
}


def _ctx(intake: dict[str, object]) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="test-agent@0.0.1",
        node_name="test_node",
        intake=intake,
    )


class TestReadCodeSource:
    def test_intake_only_no_work_branch(self) -> None:
        dto = read_code_source(_ctx(_INTAKE_NO_WB))
        assert dto.repo == "org/name"
        assert dto.base_branch == "main"
        assert dto.work_branch is None

    def test_intake_supplies_work_branch(self) -> None:
        dto = read_code_source(_ctx(_INTAKE_WITH_WB))
        assert dto.work_branch == "intake/x"

    def test_memory_overrides_when_intake_work_branch_absent(self) -> None:
        dto = read_code_source(
            _ctx(_INTAKE_NO_WB),
            memory={"codeSource": {"workBranch": "memory/x"}},
        )
        assert dto.work_branch == "memory/x"

    def test_memory_overrides_even_when_intake_supplies_work_branch(self) -> None:
        """Memory sidecar wins — the precedence is fixed."""
        dto = read_code_source(
            _ctx(_INTAKE_WITH_WB),
            memory={"codeSource": {"workBranch": "memory/x"}},
        )
        assert dto.work_branch == "memory/x"

    def test_memory_none_work_branch_does_not_override(self) -> None:
        dto = read_code_source(
            _ctx(_INTAKE_WITH_WB),
            memory={"codeSource": {"workBranch": None}},
        )
        assert dto.work_branch == "intake/x"

    def test_memory_empty_string_does_not_override(self) -> None:
        dto = read_code_source(
            _ctx(_INTAKE_WITH_WB),
            memory={"codeSource": {"workBranch": ""}},
        )
        assert dto.work_branch == "intake/x"

    def test_memory_without_code_source_key(self) -> None:
        dto = read_code_source(_ctx(_INTAKE_WITH_WB), memory={})
        assert dto.work_branch == "intake/x"

    def test_missing_intake_raises(self) -> None:
        with pytest.raises(ValueError, match="codeSource missing from intake"):
            read_code_source(_ctx({}))

    def test_does_not_mutate_inputs(self) -> None:
        intake = copy.deepcopy(_INTAKE_WITH_WB)
        memory = {"codeSource": {"workBranch": "memory/x"}}
        intake_snap = copy.deepcopy(intake)
        memory_snap = copy.deepcopy(memory)
        read_code_source(_ctx(intake), memory=memory)
        assert intake == intake_snap
        assert memory == memory_snap
