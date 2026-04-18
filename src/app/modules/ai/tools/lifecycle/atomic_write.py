"""Atomic file-write helpers for the lifecycle tools (FEAT-005 / T-091).

The lifecycle agent writes repo artifacts (task lists, plans, reviews) from
a long-running process.  A crash mid-write must never leave a half-formed
file that blocks a re-run, and we must not clobber an existing file when
the tool contract says "refuse-to-overwrite."

:func:`write_atomic` — O_EXCL-protected temp-file-rename.  Refuses if the
target already exists.  Used by ``generate_tasks``, ``generate_plan``, and
``review_implementation``.

:func:`overwrite_atomic` — temp-file-replace.  Allowed to clobber.  Used
only by ``close_work_item``, where the target is known to exist and the
caller has already validated pre-conditions.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.exceptions import PolicyError


def _validate_under_root(target: Path, repo_root: Path) -> Path:
    """Return the resolved *parent dir* for *target*, asserting it sits under *repo_root*."""
    root = repo_root.resolve()
    # Resolve parent dir (it may not yet exist up to the immediate parent,
    # but its ancestors must).
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise PolicyError(f"path escapes repo root: {target}")
    return resolved_parent


def write_atomic(target: Path, content: str, *, repo_root: Path) -> None:
    """Write *content* to *target* atomically.  Refuses if *target* exists.

    Steps: validate path under ``repo_root`` → create parent dirs →
    open a private temp file in the same dir with ``O_EXCL`` →
    write + ``fsync`` → ``os.rename`` into place.  Any exception cleans up
    the temp file.  The target's own O_EXCL precondition is enforced via
    an explicit existence check (``os.rename`` would clobber otherwise).
    """
    resolved_parent = _validate_under_root(target, repo_root)

    if target.exists():
        raise PolicyError(f"file already exists: {target}")

    tmp = resolved_parent / f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.rename(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def overwrite_atomic(target: Path, content: str, *, repo_root: Path) -> None:
    """Atomically overwrite *target* with *content*.

    Like :func:`write_atomic` but ``os.replace`` is used instead of
    ``os.rename`` — clobbers an existing file atomically on both POSIX and
    Windows.  Reserved for callers that have explicitly validated they want
    to overwrite (e.g., ``close_work_item`` flipping Status after reading
    and validating the current content).
    """
    resolved_parent = _validate_under_root(target, repo_root)

    tmp = resolved_parent / f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
