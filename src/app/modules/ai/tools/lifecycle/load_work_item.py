"""Lifecycle tool: ``load_work_item`` (FEAT-005 / T-090).

First stage of the lifecycle flow — reads the intake brief and populates
``memory.work_item``.  Thin adapter over :func:`parse_work_item`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory
from app.modules.ai.tools.lifecycle.work_items import parse_work_item

TOOL_NAME = "load_work_item"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Load a work-item markdown file from disk and populate "
            "memory.work_item with id/type/title/path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Repo-relative path to the work-item markdown, e.g. "
                        "'docs/work-items/IMP-002.md'."
                    ),
                },
            },
            "required": ["path"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    """Parse the work item at ``args['path']`` into ``memory.work_item``."""
    raw_path = Path(args["path"])
    repo_root = get_settings().repo_root.resolve()
    resolved = raw_path if raw_path.is_absolute() else repo_root / raw_path
    ref = parse_work_item(resolved, repo_root=repo_root)
    return memory.model_copy(update={"work_item": ref})
