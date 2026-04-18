"""Protocol drift guard for ``app.modules.ai.service`` (T-051).

The real service is a module of ``async def`` functions; the reference
contract in :mod:`app.contracts.ai` is a :class:`Protocol` for readers.
This test walks both and asserts every public service function still
exposes the parameter names declared on the Protocol — a deliberate
signature change fails here so we update the contract in lockstep.
"""

from __future__ import annotations

import inspect

import pytest

from app.contracts.ai import IAIService
from app.modules.ai import service

# The Protocol's first parameter is always ``self``; strip it before comparing.
_IGNORE = {"self"}

_PUBLIC_FUNCTIONS = [
    "start_run",
    "list_runs",
    "get_run",
    "cancel_run",
    "list_steps",
    "list_policy_calls",
    "list_agents",
    "ingest_engine_event",
]


def _protocol_params(name: str) -> list[str]:
    method = getattr(IAIService, name)
    return [p for p in inspect.signature(method).parameters if p not in _IGNORE]


def _service_params(name: str) -> list[str]:
    fn = getattr(service, name)
    return list(inspect.signature(fn).parameters)


class TestServiceContractDrift:
    @pytest.mark.parametrize("name", _PUBLIC_FUNCTIONS)
    def test_parameter_names_match_protocol(self, name: str) -> None:
        expected = _protocol_params(name)
        actual = _service_params(name)
        assert actual == expected, (
            f"{name}: Protocol declares {expected} but service exposes {actual}"
        )

    @pytest.mark.parametrize("name", _PUBLIC_FUNCTIONS)
    def test_public_function_is_async(self, name: str) -> None:
        fn = getattr(service, name)
        assert inspect.iscoroutinefunction(fn), f"{name} must be async"
