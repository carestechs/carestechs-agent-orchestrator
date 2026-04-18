"""Pure parser for ia-framework work-item markdown files.

Used by the lifecycle agent's ``load_work_item`` tool and by the review
stage's task-owning-work-item lookup.  No I/O beyond reading the target
file; no memory writes.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.memory import WorkItemRef

_ID_RE = re.compile(r"^\|\s*\*\*ID\*\*\s*\|\s*(\S+)\s*\|", re.MULTILINE)
_NAME_RE = re.compile(r"^\|\s*\*\*Name\*\*\s*\|\s*(.+?)\s*\|", re.MULTILINE)
_STATUS_RE = re.compile(r"^\|\s*\*\*Status\*\*\s*\|\s*(.+?)\s*\|", re.MULTILINE)
_TYPE_PREFIX_RE = re.compile(r"^(FEAT|BUG|IMP)-")
_TERMINAL_STATUSES = frozenset({"Completed", "Cancelled"})
_IDENTITY_SCAN_CHARS = 3000


def parse_work_item(path: Path, *, repo_root: Path | None = None) -> WorkItemRef:
    """Parse the Identity table from *path* and return a :class:`WorkItemRef`.

    Raises :class:`PolicyError` for: missing file, unsupported type,
    terminal status, malformed identity table, or filename/id mismatch.
    """
    if not path.is_file():
        raise PolicyError(f"work item file not found: {path}")

    body = path.read_text()
    head = body[:_IDENTITY_SCAN_CHARS]

    id_match = _ID_RE.search(head)
    if not id_match:
        raise PolicyError("missing identity table")
    work_item_id = id_match.group(1)

    type_match = _TYPE_PREFIX_RE.match(work_item_id)
    if not type_match:
        raise PolicyError(f"unsupported work item type: {work_item_id}")

    name_match = _NAME_RE.search(head)
    status_match = _STATUS_RE.search(head)
    if not (name_match and status_match):
        raise PolicyError("missing identity table")

    status = status_match.group(1).strip()
    if status in _TERMINAL_STATUSES:
        raise PolicyError(f"work item already terminal: status={status}")

    if not path.stem.startswith(work_item_id):
        raise PolicyError(
            f"filename/id mismatch: {path.stem} does not start with {work_item_id}"
        )

    if repo_root is not None:
        try:
            stored_path = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            # Path is outside repo_root — fall back to string form.
            stored_path = str(path)
    else:
        stored_path = str(path)

    return WorkItemRef(
        id=work_item_id,
        type=type_match.group(1),  # type: ignore[arg-type]  # regex pin
        title=name_match.group(1).strip(),
        path=stored_path,
    )
