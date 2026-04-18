# Implementation Plan: T-090 — `load_work_item` tool

## Task Reference
- **Task ID:** T-090
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-089

## Overview
First tool in the lifecycle flow. Parses a work-item markdown file's Identity table, validates the work-item is in a runnable state (type FEAT/BUG/IMP, Status not Completed/Cancelled), and populates `memory.work_item`. All failure modes surface as `PolicyError` so the runtime terminates the run cleanly.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/work_items.py`
Pure parser module (no tool wiring, no memory writes):
```python
_ID_RE = re.compile(r"^\|\s*\*\*ID\*\*\s*\|\s*(\S+)\s*\|", re.MULTILINE)
_NAME_RE = re.compile(r"^\|\s*\*\*Name\*\*\s*\|\s*(.+?)\s*\|", re.MULTILINE)
_STATUS_RE = re.compile(r"^\|\s*\*\*Status\*\*\s*\|\s*(.+?)\s*\|", re.MULTILINE)
_TYPE_RE = re.compile(r"^(FEAT|BUG|IMP)-")

def parse_work_item(path: Path) -> WorkItemRef:
    if not path.is_file():
        raise PolicyError(f"work item file not found: {path}")
    body = path.read_text()
    id_match = _ID_RE.search(body[:3000])  # scope to first 3 KB — Identity table is always at top
    if not id_match:
        raise PolicyError("missing identity table")
    work_item_id = id_match.group(1)
    type_match = _TYPE_RE.match(work_item_id)
    if not type_match:
        raise PolicyError(f"unsupported work item type: {work_item_id}")
    name_match = _NAME_RE.search(body[:3000])
    status_match = _STATUS_RE.search(body[:3000])
    if not (name_match and status_match):
        raise PolicyError("missing identity table")
    status = status_match.group(1).strip()
    if status in {"Completed", "Cancelled"}:
        raise PolicyError(f"work item already terminal: status={status}")
    # Filename ↔ ID cross-check
    stem = path.stem
    if not stem.startswith(work_item_id):
        raise PolicyError(f"filename/id mismatch: {stem} vs {work_item_id}")
    return WorkItemRef(
        id=work_item_id,
        type=type_match.group(1),
        title=name_match.group(1),
        path=str(path.relative_to(get_settings().repo_root)),
    )
```

### 2. Create `src/app/modules/ai/tools/lifecycle/load_work_item.py`
Thin tool adapter following the FEAT-002 tool-module pattern:
```python
TOOL_NAME = "load_work_item"

def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description="Load a work item markdown file and populate memory.work_item.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
    )

async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    path = Path(args["path"])
    if not path.is_absolute():
        path = get_settings().repo_root / path
    ref = parse_work_item(path)
    return memory.model_copy(update={"work_item": ref})
```

### 3. Create `tests/fixtures/work-items/` with 3 fixtures
- `tests/fixtures/work-items/FEAT-fixture.md` — minimal valid FEAT brief.
- `tests/fixtures/work-items/BUG-fixture.md` — minimal valid BUG.
- `tests/fixtures/work-items/IMP-fixture.md` — minimal valid IMP (Status: In Progress).
Each fixture has just the Identity table plus a short body — ~20 lines.

### 4. Create `tests/modules/ai/tools/lifecycle/test_load_work_item.py`
Eight cases:
- Happy FEAT / BUG / IMP (each asserts `memory.work_item.type`).
- Missing file → `PolicyError("work item file not found")`.
- Status=Completed → `PolicyError("work item already terminal")`.
- Status=Cancelled → same.
- Unsupported type (e.g. `DOC-001.md`) → `PolicyError("unsupported work item type")`.
- Malformed file (no identity table) → `PolicyError("missing identity table")`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/work_items.py` | Create | `parse_work_item` + regex constants. |
| `src/app/modules/ai/tools/lifecycle/load_work_item.py` | Create | Tool adapter. |
| `tests/fixtures/work-items/{FEAT,BUG,IMP}-fixture.md` | Create | 3 fixture briefs. |
| `tests/modules/ai/tools/lifecycle/test_load_work_item.py` | Create | 8 unit tests. |

## Edge Cases & Risks
- **Regex scope**: limiting to first 3 KB prevents an Identity-table-like line elsewhere in the body from false-matching. If real briefs ever exceed 3 KB for the Identity section, bump the cap.
- **Filename/ID mismatch**: explicit check guards against operators accidentally renaming a brief file without updating the Identity table.
- **Windows line endings**: `re.MULTILINE` handles `\n` and `\r\n` fine; no special-casing needed.
- **Trailing whitespace in Name/Status cells**: regex's `(.+?)\s*\|` trims trailing spaces; `.strip()` on the match group is belt-and-suspenders.
- **Type safety of the `type` field**: `Literal["FEAT", "BUG", "IMP"]` on `WorkItemRef` means Pydantic validates — a regex group that somehow captures something else (impossible given the regex shape) fails loudly.

## Acceptance Verification
- [ ] Parses 3 fixture briefs correctly.
- [ ] `PolicyError` for each of: missing file, completed/cancelled status, bad type, missing identity table.
- [ ] Tool schema advertises `path` parameter.
- [ ] 8 unit tests pass.
- [ ] `subprocess` not imported anywhere in the new modules.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
