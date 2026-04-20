"""Tests for the pure approval-matrix helper (FEAT-006 / T-113)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.modules.ai.enums import ActorRole, ApprovalStage, AssigneeType
from app.modules.ai.lifecycle.approval_matrix import approval_matrix


def _task() -> object:
    return SimpleNamespace(id=uuid.uuid4())


def _assignment(assignee_type: AssigneeType) -> object:
    return SimpleNamespace(assignee_type=assignee_type.value)


class TestProposed:
    @pytest.mark.parametrize("solo_dev", [True, False])
    @pytest.mark.parametrize(
        "assignment",
        [None, _assignment(AssigneeType.DEV), _assignment(AssigneeType.AGENT)],
    )
    def test_always_admin(self, assignment: object, solo_dev: bool) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                assignment,  # type: ignore[arg-type]
                ApprovalStage.PROPOSED,
                solo_dev=solo_dev,
            )
            == ActorRole.ADMIN
        )


class TestPlan:
    @pytest.mark.parametrize("solo_dev", [True, False])
    def test_dev_assigned_returns_dev(self, solo_dev: bool) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                _assignment(AssigneeType.DEV),  # type: ignore[arg-type]
                ApprovalStage.PLAN,
                solo_dev=solo_dev,
            )
            == ActorRole.DEV
        )

    @pytest.mark.parametrize("solo_dev", [True, False])
    def test_agent_assigned_returns_admin(self, solo_dev: bool) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                _assignment(AssigneeType.AGENT),  # type: ignore[arg-type]
                ApprovalStage.PLAN,
                solo_dev=solo_dev,
            )
            == ActorRole.ADMIN
        )

    def test_unassigned_returns_admin(self) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                None,
                ApprovalStage.PLAN,
                solo_dev=True,
            )
            == ActorRole.ADMIN
        )


class TestImpl:
    def test_solo_dev_returns_admin(self) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                _assignment(AssigneeType.DEV),  # type: ignore[arg-type]
                ApprovalStage.IMPL,
                solo_dev=True,
            )
            == ActorRole.ADMIN
        )

    def test_multi_dev_returns_dev(self) -> None:
        assert (
            approval_matrix(
                _task(),  # type: ignore[arg-type]
                _assignment(AssigneeType.DEV),  # type: ignore[arg-type]
                ApprovalStage.IMPL,
                solo_dev=False,
            )
            == ActorRole.DEV
        )
