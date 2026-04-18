"""Lifecycle tool: ``generate_plan`` (FEAT-005 / T-093).

Writes a per-task implementation plan to
``plans/plan-<task_id>-<slug>.md`` atomically and records the path on
the matching task in memory.  Slug is LLM-provided or derived from the
task title.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.atomic_write import write_atomic
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory
from app.modules.ai.tools.lifecycle.slug import slugify

TOOL_NAME = "generate_plan"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Write an implementation plan for a task to plans/plan-<task_id>-<slug>.md "
            "and record the path on memory.tasks[<match>].plan_path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "plan_markdown": {
                    "type": "string",
                    "description": "Full rendered plan markdown.",
                },
                "slug": {
                    "type": "string",
                    "description": (
                        "Optional file-name slug; derived from the task title "
                        "when omitted."
                    ),
                },
            },
            "required": ["task_id", "plan_markdown"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id: str = args["task_id"]
    plan_markdown: str = args["plan_markdown"]
    slug_arg: str | None = args.get("slug")

    if not plan_markdown.strip():
        raise PolicyError("generated plan is empty")

    match = next((t for t in memory.tasks if t.id == task_id), None)
    if match is None:
        raise PolicyError(f"unknown task: {task_id}")

    if slug_arg:
        try:
            slug = slugify(slug_arg)
        except ValueError as exc:
            raise PolicyError(f"invalid slug for {task_id}: {exc}") from exc
    else:
        try:
            slug = slugify(match.title)
        except ValueError as exc:
            raise PolicyError(f"cannot derive slug for {task_id}: {exc}") from exc

    repo_root = get_settings().repo_root.resolve()
    target = repo_root / "plans" / f"plan-{task_id}-{slug}.md"
    write_atomic(target, plan_markdown, repo_root=repo_root)

    rel = str(target.relative_to(repo_root))
    tasks = [
        t.model_copy(update={"plan_path": rel}) if t.id == task_id else t
        for t in memory.tasks
    ]
    return memory.model_copy(update={"tasks": tasks})
