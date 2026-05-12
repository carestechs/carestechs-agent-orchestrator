"""Structural guards for the Postgres trace backend (FEAT-013 / T-279).

AC-10: no module outside ``app.modules.ai.trace_postgres`` (and tests)
imports the new SQLAlchemy models ``EffectorCall`` or ``ExecutorCall``.
The runtime continues to depend only on the ``TraceStore`` protocol.

Subprocess-based so the import graph is measured cleanly regardless of
the rest of the test session's ``sys.modules`` state.  Mirrors the
import-quarantine pattern used by ``test_runtime_deterministic_is_pure``
and ``test_flow_resolver``.

If this test fails, look for code that reaches around the protocol seam
to read or write the trace tables directly — that's the drift this
guard is here to prevent.
"""

from __future__ import annotations

import subprocess
import sys


def _run(src: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )


def test_runtime_deterministic_does_not_import_trace_postgres() -> None:
    """The deterministic loop must depend only on the ``TraceStore`` protocol."""
    src = (
        "import sys\n"
        "from app.modules.ai import runtime_deterministic  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m == 'app.modules.ai.trace_postgres']\n"
        "assert not leaked, leaked\n"
    )
    result = _run(src)
    assert result.returncode == 0, (
        "deterministic runtime imports PostgresTraceStore directly:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_llm_policy_runtime_does_not_import_trace_postgres() -> None:
    """The LLM-policy loop must also stay behind the protocol."""
    src = (
        "import sys\n"
        "from app.modules.ai import runtime  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m == 'app.modules.ai.trace_postgres']\n"
        "assert not leaked, leaked\n"
    )
    result = _run(src)
    assert result.returncode == 0, (
        "LLM-policy runtime imports PostgresTraceStore directly:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_service_layer_does_not_import_trace_postgres() -> None:
    """``modules/ai/service.py`` is the runtime entry point — same rule."""
    src = (
        "import sys\n"
        "from app.modules.ai import service  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m == 'app.modules.ai.trace_postgres']\n"
        "assert not leaked, leaked\n"
    )
    result = _run(src)
    assert result.returncode == 0, (
        "service layer imports PostgresTraceStore directly:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_trace_jsonl_does_not_import_trace_postgres() -> None:
    """The two backends must not import each other — they are peers behind the seam."""
    src = (
        "import sys\n"
        "from app.modules.ai import trace_jsonl  # noqa: F401\n"
        "leaked = [m for m in sys.modules if m == 'app.modules.ai.trace_postgres']\n"
        "assert not leaked, leaked\n"
    )
    result = _run(src)
    assert result.returncode == 0, (
        "trace_jsonl imports trace_postgres:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_protocol_module_does_not_import_either_backend_at_import_time() -> None:
    """``trace.py`` must keep the backends out of its top-level import graph.

    The ``get_trace_store()`` factory is allowed to *function-locally*
    import each backend; the assertion is that ``import app.modules.ai.trace``
    on its own does not eagerly pull either backend in.
    """
    src = (
        "import sys\n"
        "from app.modules.ai import trace  # noqa: F401\n"
        "leaked = [\n"
        "    m for m in sys.modules\n"
        "    if m in ('app.modules.ai.trace_postgres', 'app.modules.ai.trace_jsonl')\n"
        "]\n"
        "assert not leaked, leaked\n"
    )
    result = _run(src)
    assert result.returncode == 0, (
        "TraceStore protocol module pulls in a backend at import time:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
