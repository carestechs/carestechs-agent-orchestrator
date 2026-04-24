"""Registration-coverage assertions for the effector bootstrap.

Light-touch regression guards: if a future PR drops a permanent
registration by accident, these tests catch it before the exhaustiveness
validator does (nicer failure message, narrower test).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.modules.ai.lifecycle.effectors.base import (
    _reset_exemptions_for_tests,
)
from app.modules.ai.lifecycle.effectors.bootstrap import register_all_effectors
from app.modules.ai.lifecycle.effectors.registry import EffectorRegistry


@pytest.fixture(autouse=True)
def reset_exemptions() -> None:
    _reset_exemptions_for_tests()
    yield
    _reset_exemptions_for_tests()


def _boot() -> EffectorRegistry:
    reg = EffectorRegistry(trace=cast("Any", MagicMock()))
    register_all_effectors(reg, trace=cast("Any", MagicMock()))
    return reg


def test_request_assignment_registered_on_entry_assigning() -> None:
    reg = _boot()
    assert "task:entry:assigning" in reg.registered_keys()


def test_generate_tasks_registered_on_work_item_entry_open() -> None:
    reg = _boot()
    assert "work_item:entry:open" in reg.registered_keys()
