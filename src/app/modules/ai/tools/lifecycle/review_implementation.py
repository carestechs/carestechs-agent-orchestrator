"""Lifecycle tool: ``review_implementation`` (FEAT-005 / T-094).

Records a structured pass/fail verdict + feedback for a task's
implementation attempt.  Writes ``plans/plan-<task_id>-<slug>-review-<attempt>.md``
atomically and appends a :class:`LifecycleReview` to
``memory.review_history``.

This tool does NOT fetch the git diff — that happens in the runtime's
prompt-assembly layer for the ``review`` node (T-100).  The tool's sole
responsibility is recording the verdict.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.atomic_write import write_atomic
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory, LifecycleReview
from app.modules.ai.tools.lifecycle.slug import slugify

TOOL_NAME = "review_implementation"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Record a review verdict (pass/fail) plus freeform feedback for a "
            "task's current implementation attempt.  Writes a markdown review file "
            "and appends the verdict to memory.review_history."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "feedback": {"type": "string"},
            },
            "required": ["task_id", "verdict", "feedback"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id: str = args["task_id"]
    verdict: str = args["verdict"]
    feedback: str = args["feedback"]

    if verdict not in {"pass", "fail"}:
        raise PolicyError(f"invalid review verdict: {verdict!r}")

    match = next((t for t in memory.tasks if t.id == task_id), None)
    if match is None:
        raise PolicyError(f"unknown task: {task_id}")

    prior_attempts = sum(1 for r in memory.review_history if r.task_id == task_id)
    attempt = prior_attempts + 1

    try:
        slug = slugify(match.title)
    except ValueError as exc:
        raise PolicyError(f"cannot derive slug for {task_id}: {exc}") from exc

    repo_root = get_settings().repo_root.resolve()
    target = (
        repo_root / "plans" / f"plan-{task_id}-{slug}-review-{attempt}.md"
    )
    content = (
        f"# Review {attempt} — {task_id}\n\n"
        f"**Verdict:** {verdict}\n\n"
        f"{feedback}\n"
    )
    write_atomic(target, content, repo_root=repo_root)

    rel = str(target.relative_to(repo_root))
    new_review = LifecycleReview(
        task_id=task_id,
        attempt=attempt,
        verdict=verdict,  # type: ignore[arg-type]  # validated above
        feedback=feedback,
        written_to=rel,
    )
    return memory.model_copy(
        update={"review_history": [*memory.review_history, new_review]}
    )
