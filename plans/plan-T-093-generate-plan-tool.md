# Implementation Plan: T-093 — `generate_plan` tool + slugify helper

## Task Reference
- **Task ID:** T-093
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-089, T-091 (for `write_atomic`)

## Overview
The plan-creation stage's output tool. Writes a per-task plan markdown file to `plans/plan-<task_id>-<slug>.md` atomically and records the path on the matching task in memory. Slug is either LLM-provided or derived from the task title.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/slug.py`
Pure helper:
```python
def slugify(title: str, max_len: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    hyphened = re.sub(r"\s+", "-", lowered.strip())
    cleaned = re.sub(r"[^a-z0-9-]+", "", hyphened)
    trimmed = re.sub(r"-+", "-", cleaned).strip("-")[:max_len].rstrip("-")
    if not trimmed:
        raise ValueError("slug cannot be empty")
    return trimmed
```

### 2. Create `src/app/modules/ai/tools/lifecycle/generate_plan.py`
Tool adapter:
```python
TOOL_NAME = "generate_plan"

async def handle(args, *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id = args["task_id"]
    plan_markdown = args["plan_markdown"]
    slug_arg = args.get("slug")

    if not plan_markdown.strip():
        raise PolicyError("generated plan is empty")

    match = next((t for t in memory.tasks if t.id == task_id), None)
    if match is None:
        raise PolicyError(f"unknown task: {task_id}")

    try:
        slug = slug_arg or slugify(match.title)
    except ValueError:
        raise PolicyError(f"cannot derive slug for task {task_id}")

    target = get_settings().repo_root / "plans" / f"plan-{task_id}-{slug}.md"
    write_atomic(target, plan_markdown, repo_root=get_settings().repo_root)

    rel = str(target.relative_to(get_settings().repo_root))
    tasks = [t.model_copy(update={"plan_path": rel}) if t.id == task_id else t
             for t in memory.tasks]
    return memory.model_copy(update={"tasks": tasks})
```

Tool schema: `task_id` (required), `plan_markdown` (required), `slug` (optional).

### 3. Create `tests/modules/ai/tools/lifecycle/test_slug.py`
Four cases:
- Happy: `slugify("Add Delivery Fee Service") == "add-delivery-fee-service"`.
- Unicode: `slugify("Aí, beleza") == "ai-beleza"`.
- All-punctuation: `slugify("!!!")` raises `ValueError`.
- Exceeds max_len: truncates cleanly at a hyphen boundary, no trailing dash.

### 4. Create `tests/modules/ai/tools/lifecycle/test_generate_plan.py`
Six cases:
- Happy with explicit slug argument.
- Happy with derived slug (from task title).
- Unknown `task_id` → `PolicyError`.
- Empty `plan_markdown` → `PolicyError`.
- Refuse-to-overwrite (pre-existing plan file).
- Slug collision across two tasks: two tasks whose titles derive to the same slug; second `generate_plan` call hits refuse-to-overwrite.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/slug.py` | Create | Pure slugify function. |
| `src/app/modules/ai/tools/lifecycle/generate_plan.py` | Create | Tool adapter. |
| `tests/modules/ai/tools/lifecycle/test_slug.py` | Create | 4 cases. |
| `tests/modules/ai/tools/lifecycle/test_generate_plan.py` | Create | 6 cases. |

## Edge Cases & Risks
- **Unicode normalization**: NFKD + ASCII-ignore handles Portuguese/Spanish/French diacritics cleanly. Chinese/Japanese titles would produce an empty slug → `ValueError` → `PolicyError`. Acceptable trade-off; operators can pass `slug` explicitly.
- **Slug collision**: deliberately surfaces as a PolicyError via refuse-to-overwrite. The LLM should pass distinct `slug` args to avoid this; if it doesn't, the run fails and the operator fixes the task titles.
- **Repo-relative `plan_path`**: stored as a string in `memory.tasks[].plan_path`. The review tool (T-094) passes this to `git diff` verbatim — so the relative path must be correct, not absolute.
- **Path traversal via slug**: slug contains only `[a-z0-9-]` by construction — no `..`, no slashes — so the filename is safe.

## Acceptance Verification
- [ ] `slugify` passes all 4 cases.
- [ ] `generate_plan` writes to `plans/plan-<task_id>-<slug>.md` atomically.
- [ ] `memory.tasks[<match>].plan_path` populated.
- [ ] Unknown task / empty plan / refuse-to-overwrite / slug collision each → `PolicyError`.
- [ ] 10 unit tests pass (4 slug + 6 generate_plan).
- [ ] `uv run pyright` clean.
