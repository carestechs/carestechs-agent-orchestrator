"""Lifespan integration — effector exhaustiveness check (FEAT-008/T-171).

Three shape-level assertions:

* Cold start with the shipped bootstrap passes lifespan and leaves the
  registry on ``app.state``.
* Cold start with a deliberately-removed exemption fails with a
  ``RuntimeError`` listing the uncovered transition.
* The failure message includes the fix-it hint (``no_effector``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.ai.lifecycle.effectors.base import (
    _reset_exemptions_for_tests,
)
from app.modules.ai.lifecycle.effectors.registry import EffectorRegistry


@pytest.fixture(autouse=True)
def reset_exemptions() -> None:
    _reset_exemptions_for_tests()
    yield
    _reset_exemptions_for_tests()


def test_lifespan_boots_with_shipped_bootstrap() -> None:
    """Cold start against the repo's current bootstrap passes validation."""
    app = create_app()
    with TestClient(app):
        registry = getattr(app.state, "effector_registry", None)
        assert isinstance(registry, EffectorRegistry)


def test_lifespan_fails_when_a_transition_is_uncovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing an exemption from the bootstrap makes lifespan raise."""
    import app.modules.ai.lifecycle.effectors.bootstrap as bootstrap_mod

    original = bootstrap_mod._register_task_exemptions

    def broken_register_task_exemptions() -> None:
        """Omit ``task:proposed->approved`` on purpose."""
        # Call the original, then surgically remove one exemption.
        original()
        from app.modules.ai.lifecycle.effectors.base import _exemptions

        _exemptions.pop("task:proposed->approved", None)

    monkeypatch.setattr(
        bootstrap_mod,
        "_register_task_exemptions",
        broken_register_task_exemptions,
    )

    app = create_app()
    with pytest.raises(RuntimeError) as exc_info:
        with TestClient(app):  # lifespan runs here
            pass

    msg = str(exc_info.value)
    assert "Effector coverage incomplete" in msg
    assert "task:proposed->approved" in msg
    assert "no_effector" in msg
