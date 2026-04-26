"""Structural import-quarantine guard for ``executors/engine.py`` (FEAT-010 / T-237).

Sibling to ``tests/test_runtime_deterministic_is_pure.py`` (FEAT-009).
Asserts:

1. Importing ``app.modules.ai.runtime_deterministic`` does **not** pull
   ``app.modules.ai.executors.engine`` or
   ``app.modules.ai.lifecycle.engine_client`` into ``sys.modules``.
2. ``executors/engine.py`` only imports ``FlowEngineLifecycleClient``
   under a ``TYPE_CHECKING`` guard — file-level string check.

Subprocess-based to measure the import graph cleanly regardless of the
rest of the test session's ``sys.modules`` state.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_runtime_deterministic_does_not_pull_engine_executor() -> None:
    src = (
        "import sys\n"
        "from app.modules.ai import runtime_deterministic  # noqa: F401\n"
        "leaked = [\n"
        "    m for m in sys.modules\n"
        "    if m == 'app.modules.ai.executors.engine'\n"
        "    or m == 'app.modules.ai.lifecycle.engine_client'\n"
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
        "deterministic runtime transitively pulls in engine executor / engine client:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_engine_executor_only_imports_client_under_type_checking() -> None:
    """File-level string check: no module-scope ``FlowEngineLifecycleClient`` import."""
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "src" / "app" / "modules" / "ai" / "executors" / "engine.py"
    source = path.read_text(encoding="utf-8")
    assert "FlowEngineLifecycleClient" in source, "type used somewhere in engine.py"

    in_type_checking = False
    offending: list[tuple[int, str]] = []
    for i, raw in enumerate(source.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("if TYPE_CHECKING"):
            in_type_checking = True
            continue
        if in_type_checking and stripped and not raw.startswith((" ", "\t")):
            in_type_checking = False
        if "FlowEngineLifecycleClient" in stripped and stripped.startswith(("from ", "import ")):
            if not in_type_checking:
                offending.append((i, raw))

    assert not offending, "module-scope import of FlowEngineLifecycleClient detected: " f"{offending}"


def test_importing_engine_executor_alone_does_not_pull_engine_client() -> None:
    """Importing the executor module on its own must not eagerly pull the client.

    This is what makes the constructor-injection contract meaningful —
    the module should be importable in environments where the lifecycle
    engine client module is not loaded until actually wired.
    """
    src = (
        "import sys\n"
        "from app.modules.ai.executors import engine  # noqa: F401\n"
        "assert 'app.modules.ai.lifecycle.engine_client' not in sys.modules, (\n"
        "    'engine.py module-scope import pulls engine_client'\n"
        ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "engine.py module-scope imports leak engine_client:\n" f"stdout={result.stdout}\nstderr={result.stderr}"
    )
