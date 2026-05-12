# Implementation Plan: T-290 — `orchestrator import-work-items` operator helper + `POST /api/v1/work-items`

## Task Reference
- **Task ID:** T-290
- **Type:** CLI
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-8 — operators can populate a fresh DB from an existing `docs/work-items/` directory without manually starting runs. Mirrors FEAT-013 `migrate-traces` shape.

## Overview
Adds `POST /api/v1/work-items` (thin wrapper around `register_work_item` — the **one** new endpoint FEAT-014 ships, justified by import-without-running) and a CLI command that walks a directory, POSTs each brief, and reports inserted/reused/conflicted counts.

## Implementation Steps

### Step 1: New endpoint `POST /api/v1/work-items`
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
After existing `/api/v1/work-items/{id}/lock` routes, add:
```python
@router.post(
    "/api/v1/work-items",
    response_model=WorkItemEnvelope,
    status_code=201,
    responses={200: {"model": WorkItemEnvelope}},  # 200 on idempotent reuse
)
async def create_work_item(
    body: RunIntakeWorkItem,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    _key: str = Depends(get_api_key),
) -> WorkItemEnvelope:
    async with db.begin():
        existed_before = await db.scalar(
            select(WorkItem.id).where(WorkItem.external_ref == body.id)
        )
        wi = await register_work_item(db, body, opened_by="import")
    if existed_before is not None:
        response.status_code = 200
    return WorkItemEnvelope(data=WorkItemDto.model_validate(wi))
```
Add `WorkItemDto` and `WorkItemEnvelope` to `schemas.py` if not already present.

Per CLAUDE.md: 201 on insert, 200 on idempotent reuse; envelope `{ data }`; same registration function as the run-start path.

### Step 2: Report dataclass
**File:** `src/app/cli.py`
**Action:** Modify (add near top)
```python
@dataclass
class WorkItemImportReport:
    inserted: list[str] = field(default_factory=list[str])
    reused: list[str] = field(default_factory=list[str])
    conflicted: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # (file, reason)
    malformed: list[str] = field(default_factory=list[str])
    dry_run: bool = False
```

### Step 3: CLI command
**File:** `src/app/cli.py`
**Action:** Modify
```python
@main.command("import-work-items")
def import_work_items(
    directory: Annotated[Path, typer.Argument(help="Directory containing FEAT-/BUG-/IMP-*.md files")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report intended actions without POSTing")] = False,
) -> None:
    report = asyncio.run(_run_import(directory, dry_run=dry_run))
    typer.echo(_format_import_report(report))
    if report.conflicted:
        raise typer.Exit(code=1)
```

And the worker:
```python
async def _run_import(directory: Path, *, dry_run: bool) -> WorkItemImportReport:
    report = WorkItemImportReport(dry_run=dry_run)
    if not directory.is_dir():
        raise typer.BadParameter(f"not a directory: {directory}")
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.orchestrator_base_url, ...) as client:
        for f in sorted(directory.iterdir()):
            if not f.is_file() or f.suffix != ".md":
                continue
            try:
                ext_ref, kind = _parse_work_item_filename(f.name)
            except typer.BadParameter:
                report.malformed.append(f.name)
                continue
            body = f.read_text(encoding="utf-8")
            payload = {"id": ext_ref, "kind": kind, "content": body}
            if dry_run:
                report.inserted.append(f.name)  # tentative
                continue
            resp = await client.post("/api/v1/work-items", json=payload, headers=_auth_headers(settings))
            if resp.status_code == 201:
                report.inserted.append(f.name)
            elif resp.status_code == 200:
                report.reused.append(f.name)
            elif resp.status_code == 409:
                report.conflicted.append((f.name, resp.json().get("detail", "conflict")))
            else:
                resp.raise_for_status()
    return report
```

Per CLAUDE.md: the CLI is a client of the service, not a back door — this implementation honors that by going through HTTP, not direct DB access.

### Step 4: Format helper
**File:** `src/app/cli.py`
**Action:** Modify
```python
def _format_import_report(r: WorkItemImportReport) -> str:
    lines = [
        f"Inserted: {len(r.inserted)}",
        f"Reused:   {len(r.reused)}",
        f"Conflicted: {len(r.conflicted)}",
        f"Malformed (skipped): {len(r.malformed)}",
    ]
    if r.conflicted:
        lines.append("\nConflicts:")
        for f, reason in r.conflicted:
            lines.append(f"  - {f}: {reason}")
    if r.malformed:
        lines.append("\nSkipped (unrecognized filename):")
        for f in r.malformed:
            lines.append(f"  - {f}")
    if r.dry_run:
        lines.insert(0, "[DRY RUN — no POSTs made]")
    return "\n".join(lines)
```

### Step 5: Tests
**File:** `tests/modules/ai/test_cli_import_work_items.py`
**Action:** Create
Tests (using `CliRunner` + `respx`):
1. `test_imports_three_kinds` — fixtures: one FEAT-, one BUG-, one IMP-*.md in a tmp dir; assert all three POSTed with correct id/kind; assert 3 inserts in report.
2. `test_rerun_is_idempotent` — first call inserts; second call reports all as reused (`respx` returns 200 the second time).
3. `test_dry_run_makes_no_requests` — assert `respx.calls` is empty after dry-run; report shows expected actions.
4. `test_malformed_filename_skipped_with_warning` — put `random.md` in dir; assert it lands in `malformed` bucket and `inserted` only has real briefs.
5. `test_conflict_exits_with_code_1` — `respx` returns 409 for one file; assert exit code 1 and the conflict is listed in output.
6. `test_non_md_files_ignored` — `.txt`, `.png` etc. are skipped silently (not malformed).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/router.py` | Modify | `POST /api/v1/work-items` thin wrapper |
| `src/app/modules/ai/schemas.py` | Modify | `WorkItemDto`, `WorkItemEnvelope` if missing |
| `src/app/cli.py` | Modify | `WorkItemImportReport`, `import-work-items` command, helpers |
| `tests/modules/ai/test_cli_import_work_items.py` | Create | Six smoke tests |

## Edge Cases & Risks
- **Scope creep on the new endpoint.** `POST /api/v1/work-items` is explicitly the *only* new endpoint. A future `GET /api/v1/work-items` is gated behind a separate FEAT (brief 4.2). Keep this PR's surface tight.
- **`dry_run` semantics.** Reports actions but cannot detect conflict-vs-reuse without a HEAD/GET capability the API doesn't have. Documented limitation — dry-run is "what files would be sent", not "what would change in the DB".
- **Authentication.** The endpoint uses the same `get_api_key` dependency as the rest of the control plane.
- **Race against a parallel run-start.** If two clients try to register the same `FEAT-100` simultaneously (one via `import-work-items`, one via `POST /api/v1/runs`), the IntegrityError path in `register_work_item` handles it. Tested in T-284.

## Acceptance Verification
- [ ] All six tests pass.
- [ ] `orchestrator import-work-items docs/work-items/` on a fresh DB reports 26 inserts (current count of FEAT/BUG/IMP files).
- [ ] Rerun reports 26 reused, 0 inserts.
- [ ] `--dry-run` makes zero HTTP calls (verified by `respx`).
- [ ] Test #5 confirms exit code 1 on any 409.
