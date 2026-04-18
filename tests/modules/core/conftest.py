"""Shared fixtures for core-module tests.

The Anthropic provider's retry loop sleeps with exponential backoff + jitter
in production.  Tests that don't specifically assert on timing should not
pay that cost — the autouse fixture below zeroes both base and jitter so
retries complete as fast as ``asyncio.sleep(0)`` allows.  Tests that DO
care about timing (see ``test_llm_anthropic_retries.py``) restore real
values via their own monkeypatch inside the test body.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fast_anthropic_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable real sleeps in the provider's retry loop."""
    monkeypatch.setattr(
        "app.core.llm_anthropic._BACKOFF_BASE_SECONDS", 0.0
    )
    monkeypatch.setattr(
        "app.core.llm_anthropic._JITTER_SECONDS", 0.0
    )
