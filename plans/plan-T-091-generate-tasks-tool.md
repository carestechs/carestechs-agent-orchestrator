# Implementation Plan: T-091 — `generate_tasks` tool + shared `write_atomic` helper

## Task Reference
- **Task ID:** T-091
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-089

## Overview
The task-generation stage's output tool. Writes the LLM's rendered tasks document atomically to `tasks/<work-item-id>-tasks.md`, parses back the task IDs it contains to populate `memory.tasks`, and refuses to overwrite an existing file. Also lands the shared `write_atomic` helper that T-093, T-094, T-095 will reuse.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/atomic_write.py`
The one place that touches `tempfile` + `os.rename` + `os.fsync`:
```python
def write_atomic(target: Path, content: str, *, repo_root: Path) -> None:
    """Write *content* to *target* atomically.  Raises PolicyError on conflict."""
    resolved = target.resolve()
    if repo_root not in resolved.parents and resolved != repo_root:
        raise PolicyError(f"path escapes repo root: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    tmp = target.parent / tmp_name
    # O_EXCL guards against TOCTOU: if target exists the rename will clobber it,
    # so we check existence here and fail fast.
    if target.exists():
        raise PolicyError(f"file already exists: {target}")
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
```

### 2. Create `src/app/modules/ai/tools/lifecycle/generate_tasks.py`
Tool adapter:
```python
TOOL_NAME = "generate_tasks"

_TASK_ROW_RE = re.compile(r"^###\s+(T-\d+):\s*(.+?)\s*$", re.MULTILINE)

async def handle(args, *, memory: LifecycleMemory) -> LifecycleMemory:
    work_item_id = args["work_item_id"]
    tasks_markdown = args["tasks_markdown"]
    if not tasks_markdown.strip():
        raise PolicyError("generated task list is empty")
    target = get_settings().repo_root / "tasks" / f"{work_item_id}-tasks.md"
    write_atomic(target, tasks_markdown, repo_root=get_settings().repo_root)
    rows = _TASK_ROW_RE.findall(tasks_markdown)
    if not rows:
        raise PolicyError("generated task list is empty")
    tasks = [LifecycleTask(id=tid, title=title) for tid, title in rows]
    return memory.model_copy(update={"tasks": tasks})
```

Tool definition advertises `work_item_id` + `tasks_markdown` (both required strings).

### 3. Create `tests/modules/ai/tools/lifecycle/test_atomic_write.py`
Three cases:
- Happy: file written, content exact, temp file gone.
- Refuse-if-exists: target already exists → `PolicyError`, target unchanged.
- Path-escape-root: `../outside.md` → `PolicyError`.
- Bonus: exception during write → temp file cleaned up (use `monkeypatch` to make `os.fsync` raise).

### 4. Create `tests/modules/ai/tools/lifecycle/test_generate_tasks.py`
Four cases: happy (writes file + populates memory.tasks), refuse-to-overwrite (preexisting file), empty body → PolicyError, body with no parseable T-rows → PolicyError.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/atomic_write.py` | Create | `write_atomic` shared helper. |
| `src/app/modules/ai/tools/lifecycle/generate_tasks.py` | Create | Tool adapter. |
| `tests/modules/ai/tools/lifecycle/test_atomic_write.py` | Create | 4 cases. |
| `tests/modules/ai/tools/lifecycle/test_generate_tasks.py` | Create | 4 cases. |

## Edge Cases & Risks
- **TOCTOU between exists-check and rename**: `os.rename` on POSIX is atomic and clobbers; the exists-check is the only guard. Race with an external writer (another process) is theoretically possible but out of scope in v1 (single operator).
- **Directory fsync for crash consistency**: not implemented in v1 — `os.fsync(fd)` fsyncs the file but not the parent directory. A power loss mid-rename could leave the directory entry unflushed. Acceptable per the feature brief's single-operator assumption; revisit if crash-testing becomes a concern.
- **Regex for parsing T-rows**: assumes the LLM emits `### T-XXX: Title` headings matching the template. If the LLM deviates, the regex returns 0 matches → `PolicyError("generated task list is empty")`. Operator can regenerate.
- **Windows `os.rename` semantics**: on Windows, rename-over-existing fails. Since we check `target.exists()` first, this is a non-issue for our refuse-to-overwrite contract.

## Acceptance Verification
- [ ] `write_atomic` performs atomic temp-file-rename with O_EXCL + fsync.
- [ ] `generate_tasks` writes to `tasks/<id>-tasks.md` and populates `memory.tasks`.
- [ ] Refuse-to-overwrite enforced.
- [ ] Empty body / no T-rows → `PolicyError`.
- [ ] Temp file cleanup on exception.
- [ ] 8 unit tests green; `uv run pyright` clean.
