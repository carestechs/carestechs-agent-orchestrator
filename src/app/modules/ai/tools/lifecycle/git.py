"""The *only* ``subprocess`` boundary in the codebase (FEAT-005 / T-094).

Wraps ``git diff`` for the lifecycle agent's review stage.  Every exit
mode is translated into a typed :class:`~app.core.exceptions.PolicyError`
so the runtime terminates cleanly if git is missing, the working tree
isn't a repo, or the diff blows up.

The adapter-thin quarantine test (FEAT-005 / T-106) forbids ``subprocess``
imports outside this module.
"""

from __future__ import annotations

import shutil
import subprocess  # quarantined import — see module docstring
from pathlib import Path

from app.core.exceptions import PolicyError

_DIFF_MAX_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 10


def get_diff(
    paths: list[str],
    *,
    base: str = "main",
    cwd: Path,
) -> str:
    """Return the ``git diff {base}...HEAD`` output scoped to *paths*.

    Returns trimmed-to-64 KB output with a trailing truncation marker when
    the full diff exceeds that ceiling.  Raises :class:`PolicyError` for:
    missing git binary, not-a-repo, unknown base revision, generic diff
    failure, or timeout.

    All arguments are passed as argv (no ``shell=True``); git is the only
    binary invoked.
    """
    if shutil.which("git") is None:
        raise PolicyError("git not available")

    argv = ["git", "diff", f"{base}...HEAD", "--", *paths]
    try:
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired as exc:
        raise PolicyError(f"git diff timed out after {_TIMEOUT_SECONDS}s") from exc
    except FileNotFoundError as exc:
        raise PolicyError("git not available") from exc

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if "not a git repository" in stderr_lower:
            raise PolicyError("not a git repository")
        if "unknown revision" in stderr_lower or "bad revision" in stderr_lower:
            raise PolicyError(f"git diff failed: unknown base {base!r}")
        trimmed = result.stderr.strip()[:200]
        raise PolicyError(f"git diff failed: {trimmed}")

    diff = result.stdout
    encoded = diff.encode("utf-8")
    if len(encoded) <= _DIFF_MAX_BYTES:
        return diff
    head = encoded[:_DIFF_MAX_BYTES].decode("utf-8", errors="replace")
    remaining = len(encoded) - _DIFF_MAX_BYTES
    return f"{head}\n...<truncated {remaining} bytes>...\n"
