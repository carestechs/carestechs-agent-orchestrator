"""Lifecycle tool: ``generate_tasks`` (FEAT-005 / T-091).

Writes the LLM's rendered task list to ``tasks/<work_item_id>-tasks.md``
atomically and populates ``memory.tasks`` by parsing back the task rows.
Refuses to overwrite an existing file.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.atomic_write import write_atomic
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleTask

TOOL_NAME = "generate_tasks"

# Accepts the canonical `### T-001: Title` heading plus common variants
# models emit in practice: `## T-001: Title`, `- T-001: Title`,
# `**T-001**: Title`, `- [ ] **TASK-001** · Title`, etc.  The id token is
# `T-<digits>` or `TASK-<digits>`; we normalize the latter to `T-<digits>`
# so downstream tools (``generate_plan``, ``review_implementation``) see a
# single convention.
_TASK_ROW_RE = re.compile(
    r"^[\s\-*#`>\[\]x]*\**\s*(T(?:ASK)?-\d+)\**[:\s·|\-\u2013\u2014]+\s*(.+?)\s*$",
    re.MULTILINE,
)


def _normalize_task_id(raw: str) -> str:
    """``TASK-001`` → ``T-001``; ``T-001`` passes through unchanged."""
    return raw.replace("TASK-", "T-", 1) if raw.startswith("TASK-") else raw


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Write the generated task list to tasks/<work_item_id>-tasks.md "
            "and populate memory.tasks with (id, title) per task row. "
            "The markdown MUST declare each task using a heading of the "
            "form '### T-XXX: Task title' (numeric task id, colon, then "
            "a short one-line title on the same line). Every task you "
            "include under that shape becomes one entry in memory.tasks; "
            "rows in any other format are ignored."
        ),
        parameters={
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "string",
                    "description": "The work item id (e.g., 'IMP-002').",
                },
                "tasks_markdown": {
                    "type": "string",
                    "description": (
                        "Full rendered task-breakdown markdown. Each task "
                        "declared as '### T-XXX: Title' on its own line."
                    ),
                },
            },
            "required": ["work_item_id", "tasks_markdown"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    work_item_id: str = args["work_item_id"]
    tasks_markdown: str = args["tasks_markdown"]

    if not tasks_markdown.strip():
        raise PolicyError("generated task list is empty")

    rows = _TASK_ROW_RE.findall(tasks_markdown)
    if not rows:
        raise PolicyError("generated task list has no parseable '### T-XXX:' rows")

    repo_root = get_settings().repo_root.resolve()
    target = repo_root / "tasks" / f"{work_item_id}-tasks.md"
    write_atomic(target, tasks_markdown, repo_root=repo_root)

    tasks = [
        LifecycleTask(id=_normalize_task_id(tid), title=title) for tid, title in rows
    ]
    return memory.model_copy(update={"tasks": tasks})
