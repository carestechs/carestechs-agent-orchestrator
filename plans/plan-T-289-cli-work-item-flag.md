# Implementation Plan: T-289 — `orchestrator run --work-item <path>` (client-side file read)

## Task Reference
- **Task ID:** T-289
- **Type:** CLI
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-6 — operators run against a remote orchestrator with no shared filesystem. AC-7 — the legacy flag still works.

## Overview
Adds a `--work-item PATH` option to `orchestrator run`. The CLI reads the file (UTF-8) client-side, parses `id` + `kind` from the filename, and POSTs `intake.workItem = {id, kind, content}`. The file lives on the *caller's* host; the orchestrator process never sees the path.

## Implementation Steps

### Step 1: Add the `--work-item` option to the `run` command
**File:** `src/app/cli.py`
**Action:** Modify
Find the existing `@main.command("run")` (or `@app.command`) decorator. Add a new option:
```python
work_item: Annotated[
    Optional[Path],
    typer.Option(
        "--work-item",
        help="Path to a FEAT-/BUG-/IMP-*.md brief. Read client-side; uploaded as intake.workItem.",
        exists=False,  # we handle missing-file ourselves for a clearer error
    ),
] = None,
```
Per CLAUDE.md: typer command, snake_case Python attribute, kebab-case CLI flag.

### Step 2: Mutual exclusion with `--intake workItemPath=...`
**File:** `src/app/cli.py`
**Action:** Modify
At the top of the command body:
```python
intake_dict = _parse_intake_flags(intake or [])
if work_item is not None and "workItemPath" in intake_dict:
    raise typer.BadParameter(
        "--work-item and --intake workItemPath=... are mutually exclusive; pick one"
    )
```
Exit code 2 is typer's default for `BadParameter`.

### Step 3: Client-side file read + filename parsing
**File:** `src/app/cli.py`
**Action:** Modify
```python
if work_item is not None:
    if not work_item.is_file():
        typer.echo(f"Error: --work-item file not found: {work_item}", err=True)
        raise typer.Exit(code=2)
    body = work_item.read_text(encoding="utf-8")
    ext_ref, kind = _parse_work_item_filename(work_item.name)
    intake_dict["workItem"] = {"id": ext_ref, "kind": kind, "content": body}
```

And the helper, near the bottom of the module:
```python
_WORK_ITEM_RE = re.compile(r"^(FEAT|BUG|IMP)-(\d+)(?:-[a-z0-9-]+)?\.md$")

def _parse_work_item_filename(name: str) -> tuple[str, str]:
    m = _WORK_ITEM_RE.match(name)
    if not m:
        raise typer.BadParameter(
            f"--work-item filename must match FEAT-XXX[-slug].md, BUG-XXX[-slug].md, or "
            f"IMP-XXX[-slug].md; got: {name}. Use --intake workItem.id=... for non-standard names."
        )
    kind, num = m.groups()
    return f"{kind}-{num}", kind
```

### Step 4: POST the intake unchanged
**File:** `src/app/cli.py`
**Action:** Modify (read first)
The existing `run` command already POSTs `intake_dict` to `POST /api/v1/runs` via the HTTP client. No change needed beyond having `intake_dict["workItem"]` populated. **Confirm by reading** the existing post-flag-parse block to ensure it doesn't strip unrecognized keys.

### Step 5: Smoke tests
**File:** `tests/modules/ai/test_cli_run_work_item.py`
**Action:** Create
Using `typer.testing.CliRunner` + `respx` for HTTP stubbing. Tests:
1. `test_happy_path_uploads_content` — write a tmp `FEAT-100-example.md`, invoke `run --work-item <tmp>`, assert the POSTed body had `intake.workItem.id == "FEAT-100"`, `kind == "FEAT"`, `content == "<file body>"`.
2. `test_missing_file_exits_2_no_http_call` — invoke with a non-existent path, assert exit code 2; `respx.calls` count is zero.
3. `test_unrecognized_filename_exits_2` — invoke with `random.md`, assert clear error.
4. `test_mutually_exclusive_with_legacy_flag` — invoke `--work-item ... --intake workItemPath=...`, assert exit code 2.
5. `test_kind_parse_bug_imp` — verify BUG-123-x.md and IMP-7.md both parse correctly.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/cli.py` | Modify | New option + parsing + mutual-exclusion check |
| `tests/modules/ai/test_cli_run_work_item.py` | Create | Five smoke tests |

## Edge Cases & Risks
- **Filename with non-canonical slugging.** Briefs whose filenames don't match the regex must use `--intake workItem.id=...` (free-form intake parsing). Acceptable since `docs/work-items/` follows the convention. The error message points users at the workaround.
- **Large files.** No client-side cap — the server returns 413 if oversized (T-285). The CLI surfaces the server's 413 cleanly via the existing error path.
- **UTF-8 only.** `read_text(encoding="utf-8")` will raise on non-UTF-8 input. Acceptable — all briefs in the project are UTF-8.
- **`--intake` parsing details.** The existing `_parse_intake_flags` likely takes `key=value` pairs and parses into a nested dict; confirm the dot-notation (`workItem.id=...`) is supported, or document its absence.

## Acceptance Verification
- [ ] All five smoke tests pass under `uv run pytest tests/modules/ai/test_cli_run_work_item.py`.
- [ ] `orchestrator run lifecycle-agent@0.3.0 --work-item docs/work-items/FEAT-005-lifecycle-agent.md` against a local server returns a run id (smoke-tested manually before merge).
- [ ] Test #2 confirms no HTTP call on missing-file error (verifies the early exit).
- [ ] Test #4 confirms mutual exclusion exits with code 2 before any HTTP.
