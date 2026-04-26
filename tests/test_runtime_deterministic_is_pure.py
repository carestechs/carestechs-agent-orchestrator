"""Structural guard for the deterministic-runtime module (FEAT-009 / T-228).

Asserts that ``app.modules.ai.runtime_deterministic`` does not import
``app.core.llm`` (the LLM provider abstraction) or any executor-handler
module — node selection in the deterministic path is the FlowResolver's
job; artifact production is the executor's job; neither belongs in the
loop.

Subprocess-based to measure import-graph cleanly regardless of the rest
of the test session's ``sys.modules`` state.  Mirrors the
``test_flow_resolver`` import-quarantine pattern.

The LLM-policy path (``runtime.py``) is intentionally excluded — it
imports ``core.llm`` *because that's its mode*. T-228 polices the
deterministic path only, per the AC-5 revision.
"""

from __future__ import annotations

import subprocess
import sys


def test_runtime_deterministic_does_not_import_llm() -> None:
    src = (
        "import sys\n"
        "from app.modules.ai import runtime_deterministic  # noqa: F401\n"
        "leaked = [\n"
        "    m for m in sys.modules\n"
        "    if m == 'app.core.llm'\n"
        "    or m == 'anthropic' or m.startswith('anthropic.')\n"
        "    or m == 'openai' or m.startswith('openai.')\n"
        "]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "deterministic runtime pulls in an LLM SDK or core.llm:\n" f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_runtime_deterministic_does_not_import_lifecycle_tools() -> None:
    """Artefact-producing tool modules belong inside executors, not the loop."""
    src = (
        "import sys\n"
        "from app.modules.ai import runtime_deterministic  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m.startswith('app.modules.ai.tools.lifecycle')]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "deterministic runtime pulls in lifecycle tool modules:\n" f"stdout={result.stdout}\nstderr={result.stderr}"
    )
