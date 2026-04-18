"""Composition-integrity smoke test — shape-only, no end-to-end yet.

The full end-to-end AD-3 proof (scripted stub policy + echo engine → run
completes ``done_node``) is implemented in
``tests/integration/test_run_end_to_end.py`` (T-054).

This module only asserts the entry-point imports and the signature is
stable, so refactors that drift the shape fail here *before* the
integration suite takes the slower hit.
"""

from __future__ import annotations

import inspect

from app.core.llm import StubLLMProvider
from app.modules.ai.runtime import run_loop


class TestCompositionIntegritySmoke:
    def test_runtime_module_is_importable(self) -> None:
        assert callable(run_loop)

    def test_stub_policy_can_be_constructed(self) -> None:
        policy = StubLLMProvider([("some_tool", {"arg": 1})])
        assert policy.name == "stub"

    def test_run_loop_signature_is_stable(self) -> None:
        """Guard against accidental parameter drift.

        The integration test (T-054) is the behavioural proof; this one is a
        cheap tripwire for refactors.
        """
        sig = inspect.signature(run_loop)
        expected = {
            "run_id",
            "agent",
            "policy",
            "engine",
            "trace",
            "supervisor",
            "session_factory",
            "cancel_event",
        }
        assert set(sig.parameters) == expected
        for param in sig.parameters.values():
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
