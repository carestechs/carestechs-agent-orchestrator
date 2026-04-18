"""Lifecycle tool: ``close_work_item`` (FEAT-005 / T-095).

Final tool in the lifecycle flow.  Flips the Identity table's ``Status``
row from ``In Progress`` to ``Completed`` and inserts a ``Completed``
timestamp row immediately after.  Atomic overwrite via ``overwrite_atomic``.

The pre-edit Status check guards against a concurrent operator edit that
manually advanced the brief to Completed or Cancelled.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.atomic_write import overwrite_atomic
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

TOOL_NAME = "close_work_item"

_STATUS_LINE_RE = re.compile(
    r"^(?P<prefix>\|\s*\*\*Status\*\*\s*\|\s*)(?P<value>.+?)(?P<suffix>\s*\|.*)$"
)
_IDENTITY_SCAN_LINES = 30


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Flip the work-item brief's Status to 'Completed' and record a "
            "Completed timestamp.  Refuses if the current Status isn't 'In Progress'."
        ),
        parameters={
            "type": "object",
            "properties": {"work_item_id": {"type": "string"}},
            "required": ["work_item_id"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    work_item_id: str = args["work_item_id"]

    if memory.work_item is None or memory.work_item.id != work_item_id:
        raise PolicyError(f"work_item_id mismatch: {work_item_id}")

    repo_root = get_settings().repo_root.resolve()
    path = repo_root / memory.work_item.path
    if not path.is_file():
        raise PolicyError(f"work item file not found: {path}")

    original_lines = path.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    edited = False
    for i, line in enumerate(original_lines):
        if not edited and i < _IDENTITY_SCAN_LINES:
            match = _STATUS_LINE_RE.match(line)
            if match:
                current = match.group("value").strip()
                if current != "In Progress":
                    raise PolicyError(f"work item not in progress: status={current}")
                prefix = match.group("prefix")
                suffix = match.group("suffix")
                new_lines.append(f"{prefix}Completed{suffix}")
                if not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"
                timestamp = datetime.now(UTC).isoformat(timespec="seconds")
                new_lines.append(f"| **Completed** | {timestamp} |\n")
                edited = True
                continue
        new_lines.append(line)

    if not edited:
        raise PolicyError("identity table not parseable")

    overwrite_atomic(path, "".join(new_lines), repo_root=repo_root)
    return memory
