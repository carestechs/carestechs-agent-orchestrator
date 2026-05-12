# Implementation Plan: T-293 — CLI smoke tests for `--work-item` and `import-work-items`

## Task Reference
- **Task ID:** T-293
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-6, AC-8 at the CLI boundary. T-291/T-292 cover the server-side; this covers the client-side.

## Overview
`typer.testing.CliRunner` + `respx` smoke tests for the two new CLI surfaces (`run --work-item` from T-289 and `import-work-items` from T-290). All HTTP is stubbed — no live network. CLAUDE.md priority 3 testing layer.

## Implementation Steps

### Step 1: Shared `respx` fixture
**File:** `tests/modules/ai/conftest.py`
**Action:** Modify (or `tests/conftest.py`)
Add a session-scoped or function-scoped `respx_mock` fixture if not already present:
```python
@pytest.fixture
def stub_orchestrator(respx_mock):
    """Catches all calls to the configured base URL and returns 202/201/200."""
    respx_mock.post("http://localhost:8000/api/v1/runs").mock(
        return_value=httpx.Response(202, json={"data": {"id": str(uuid.uuid4()), "status": "running"}})
    )
    respx_mock.post("http://localhost:8000/api/v1/work-items").mock(
        return_value=httpx.Response(201, json={"data": {"id": str(uuid.uuid4()), "externalRef": "FEAT-X"}})
    )
    return respx_mock
```
Use the test's `respx_mock` to override per-test responses (200 for reuse, 409 for conflict, etc.).

### Step 2: `--work-item` tests
**File:** `tests/modules/ai/test_cli_run_work_item.py`
**Action:** Create (extends T-289 — that plan stubbed five tests; this one finalizes the file)
The five tests from T-289's plan are owned by this task:
1. `test_happy_path_uploads_content` — assert POST body has `intake.workItem.{id, kind, content}`.
2. `test_missing_file_exits_2_no_http_call` — `respx_mock.calls.call_count == 0`.
3. `test_unrecognized_filename_exits_2` — invoke with `random.md`.
4. `test_mutually_exclusive_with_legacy_flag`.
5. `test_kind_parse_bug_imp` — both `BUG-1-x.md` and `IMP-7.md` parse correctly.

### Step 3: `import-work-items` tests
**File:** `tests/modules/ai/test_cli_import_work_items.py`
**Action:** Create (extends T-290)
Six tests from T-290's plan:
1. `test_imports_three_kinds`.
2. `test_rerun_is_idempotent`.
3. `test_dry_run_makes_no_requests`.
4. `test_malformed_filename_skipped_with_warning`.
5. `test_conflict_exits_with_code_1`.
6. `test_non_md_files_ignored`.

### Step 4: Tmp-directory fixture for `import-work-items`
**File:** `tests/modules/ai/test_cli_import_work_items.py`
**Action:** Modify
```python
@pytest.fixture
def work_items_dir(tmp_path: Path) -> Path:
    (tmp_path / "FEAT-100-test.md").write_text("# Test FEAT\nbody")
    (tmp_path / "BUG-001-test.md").write_text("# Test BUG\nbody")
    (tmp_path / "IMP-001-test.md").write_text("# Test IMP\nbody")
    (tmp_path / "random.md").write_text("not a work item")
    (tmp_path / "notes.txt").write_text("ignored")
    return tmp_path
```

### Step 5: Result assertions verifying POST shape
**File:** `tests/modules/ai/test_cli_import_work_items.py`
**Action:** Modify
Each happy-path test asserts on `respx_mock.calls[0].request` to verify the JSON body shape — `id`, `kind`, `content` all present and correctly camelCased.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/conftest.py` | Modify (or create) | Shared `stub_orchestrator` respx fixture |
| `tests/modules/ai/test_cli_run_work_item.py` | Create | Five smoke tests (consolidated from T-289 plan) |
| `tests/modules/ai/test_cli_import_work_items.py` | Create | Six smoke tests (consolidated from T-290 plan) |

## Edge Cases & Risks
- **`CliRunner` and `asyncio.run` interaction.** Typer commands that wrap `asyncio.run` inside their handler are fine with `CliRunner`; verified by FEAT-013's `migrate-traces` test layout. If a command uses an async typer command directly, the runner still works — but check the project's existing CLI tests for the pattern.
- **`respx` and `httpx.AsyncClient`.** `respx_mock` patches `httpx` at the transport layer; works for both sync and async clients. The CLI may use either — confirm by reading T-289/T-290 implementation.
- **Exit-code assertions.** `CliRunner.invoke(...).exit_code` is canonical. Don't assert on `result.exception` for `typer.Exit` cases — typer captures cleanly.
- **Fixture overlap with integration tests.** Keep `stub_orchestrator` in the CLI test conftest (`tests/modules/ai/conftest.py`), not the integration one — server-side tests use a real client, not respx.

## Acceptance Verification
- [ ] All eleven tests (5 + 6) pass under `uv run pytest tests/modules/ai/test_cli_run_work_item.py tests/modules/ai/test_cli_import_work_items.py`.
- [ ] No live HTTP — `respx` covers all outbound calls.
- [ ] Test #2 (CLI run) confirms `respx_mock.calls.call_count == 0` for missing-file.
- [ ] Test #3 (import) confirms `respx_mock.calls.call_count == 0` for dry-run.
