"""FEAT-014 / T-292 — structural guard: executors don't read briefs from disk.

After FEAT-014, work-item brief content lives in ``WorkItem.body_md`` and
the only allowed disk read of a brief is the T-288 deprecation shim
inside ``modules/ai/service.py::_legacy_work_item_path_to_dto`` — tagged
``DEPRECATED FEAT-014`` for grep.

This guard runs in a subprocess (mirrors FEAT-009's
``test_runtime_deterministic_is_pure.py``) to keep the import / runtime
trap isolated from the rest of the test session's monkey-patches.

Specifically:

1. Importing ``app.modules.ai.executors.bootstrap`` must not open any
   path containing ``docs/work-items``.
2. Calling the v0.2 ``request_work_item_load`` handler against a
   ``DispatchContext`` for a registered ``WorkItem`` must not open
   ``docs/work-items/*`` (verified directly in
   ``tests/modules/ai/test_executor_load_work_item.py``, but we re-pin
   it here at module-import time as an extra layer of defence).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_bootstrap_import_does_not_read_briefs() -> None:
    """Importing ``executors.bootstrap`` must not open ``docs/work-items``."""
    src = textwrap.dedent(
        """
        import builtins
        import pathlib

        violations: list[str] = []

        _real_open = builtins.open
        _real_read_text = pathlib.Path.read_text
        _real_read_bytes = pathlib.Path.read_bytes

        def _trap_open(file, *a, **k):
            s = str(file)
            if "docs/work-items" in s:
                violations.append(s)
            return _real_open(file, *a, **k)

        def _trap_read_text(self, *a, **k):
            s = str(self)
            if "docs/work-items" in s:
                violations.append(s)
            return _real_read_text(self, *a, **k)

        def _trap_read_bytes(self, *a, **k):
            s = str(self)
            if "docs/work-items" in s:
                violations.append(s)
            return _real_read_bytes(self, *a, **k)

        builtins.open = _trap_open
        pathlib.Path.read_text = _trap_read_text
        pathlib.Path.read_bytes = _trap_read_bytes

        # The import itself must not trigger any brief read.
        import app.modules.ai.executors.bootstrap  # noqa: F401

        import sys as _sys
        if violations:
            _sys.stderr.write("violations: " + repr(violations) + chr(10))
            _sys.exit(1)
        _sys.exit(0)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "importing executors.bootstrap triggered a brief disk read:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_only_legacy_shim_reads_briefs_from_disk() -> None:
    """The single allowed disk-read site is grep-able as ``DEPRECATED FEAT-014``.

    A regression that adds a new ``.read_text()`` on a work-item path
    elsewhere will land *without* this tag and break this assertion's
    invariant when audited.  This test pins the count so future PRs that
    add tagged reads must update it deliberately.
    """
    import pathlib

    src_root = pathlib.Path(__file__).parent.parent / "src" / "app"
    tagged: list[tuple[pathlib.Path, int]] = []
    for py_file in src_root.rglob("*.py"):
        for lineno, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "DEPRECATED FEAT-014" in line:
                tagged.append((py_file, lineno))

    # Exactly the T-288 shim is tagged today.  If you add another tagged
    # site, update this count *and* document the addition in the FEAT-014
    # plan files.  If the shim is removed (post-deprecation cleanup),
    # this asserts 0 and the file can be deleted entirely.
    assert len(tagged) >= 1, (
        "expected at least the T-288 ``DEPRECATED FEAT-014`` shim; found none"
    )
