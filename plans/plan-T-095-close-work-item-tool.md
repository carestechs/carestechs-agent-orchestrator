# Implementation Plan: T-095 — `close_work_item` tool

## Task Reference
- **Task ID:** T-095
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-089, T-091

## Overview
The last tool in the lifecycle flow. Reads the work-item markdown, flips the Identity table's Status row from `In Progress` to `Completed`, inserts a new `Completed:` timestamp row, and writes the result atomically. A pre-edit Status check guards against concurrent operator edits.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/close_work_item.py`
```python
TOOL_NAME = "close_work_item"

_STATUS_LINE_RE = re.compile(r"^(\|\s*\*\*Status\*\*\s*\|\s*)(.+?)(\s*\|.*)$")

async def handle(args, *, memory: LifecycleMemory) -> LifecycleMemory:
    work_item_id = args["work_item_id"]
    if memory.work_item is None or memory.work_item.id != work_item_id:
        raise PolicyError(f"work_item_id mismatch: {work_item_id}")

    path = get_settings().repo_root / memory.work_item.path
    if not path.is_file():
        raise PolicyError(f"work item file not found: {path}")

    original_lines = path.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    edited = False
    for i, line in enumerate(original_lines):
        if not edited and i < 30:
            m = _STATUS_LINE_RE.match(line)
            if m:
                current = m.group(2).strip()
                if current != "In Progress":
                    raise PolicyError(f"work item not in progress: status={current}")
                new_lines.append(f"{m.group(1)}Completed{m.group(3)}")
                completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                new_lines.append(f"| **Completed** | {completed_at} |\n")
                edited = True
                continue
        new_lines.append(line)
    if not edited:
        raise PolicyError("identity table not parseable")

    # Atomic-overwrite: write_atomic refuses-if-exists, so use a different helper path.
    # Implement overwrite via temp + rename inline since write_atomic doesn't allow clobber.
    content = "".join(new_lines)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        tmp.write_text(content)
        os.replace(tmp, path)  # atomic overwrite-if-exists
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return memory
```

Tool schema: `work_item_id` (required string).

### 2. Extend `src/app/modules/ai/tools/lifecycle/atomic_write.py`
Add a sibling helper `overwrite_atomic(target, content)` so `close_work_item` doesn't duplicate the temp-file logic. Unlike `write_atomic`, this helper *is* allowed to clobber — used only when the caller intends to overwrite an existing file.

### 3. Create `tests/modules/ai/tools/lifecycle/test_close_work_item.py`
Six cases:
- Happy: Status `In Progress` → `Completed`; new `Completed:` row inserted; ISO-8601 timestamp valid.
- Refuse-if-not-in-progress: Status `Not Started` → `PolicyError`.
- Missing file.
- Malformed table (no Status row in first 30 lines).
- Round-trip: after editing, `parse_work_item` (T-090) reads the file cleanly.
- `work_item_id` mismatch with `memory.work_item.id` → `PolicyError`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/close_work_item.py` | Create | Tool adapter + inline atomic overwrite. |
| `src/app/modules/ai/tools/lifecycle/atomic_write.py` | Modify | Add `overwrite_atomic` sibling. |
| `tests/modules/ai/tools/lifecycle/test_close_work_item.py` | Create | 6 unit tests. |

## Edge Cases & Risks
- **`os.replace` vs `os.rename`**: `os.replace` is atomic on both POSIX and Windows and always clobbers. `os.rename` on Windows fails if the target exists. Use `os.replace` for the overwrite case.
- **Race with operator edit**: between read and replace, the operator could edit the file. Their changes are lost. Documented as v1 limitation per feature brief §9.
- **Status phrase variants**: the regex expects literal `In Progress`. If a brief has `in progress` (lowercase) or `INPROGRESS`, the guard rejects. This is intentional — consistency with the existing FEAT briefs' capitalization.
- **First-30-lines scope**: guards against body text matching the Status-line regex. 30 lines is generous; all existing briefs have the Identity table within the first 20.
- **Atomic overwrite path-escape**: `overwrite_atomic` must do the same `repo_root in parents` check as `write_atomic`.

## Acceptance Verification
- [ ] Status flipped `In Progress → Completed` via atomic rewrite.
- [ ] `Completed:` ISO-8601 UTC timestamp row inserted.
- [ ] Non-`In Progress` Status → `PolicyError`, file unchanged.
- [ ] Missing file / malformed table / `work_item_id` mismatch → distinct `PolicyError`s.
- [ ] Edited file re-parses cleanly via `parse_work_item`.
- [ ] 6 unit tests pass; `uv run pyright` clean.
