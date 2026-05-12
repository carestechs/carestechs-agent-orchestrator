# Implementation Plan: T-292 — End-to-end "no filesystem access" test + structural guard

## Task Reference
- **Task ID:** T-292
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-5 directly; the structural guard prevents a future PR from reintroducing a disk read in any executor.

## Overview
Two artefacts:
1. **End-to-end test** that runs `lifecycle-agent@0.3.0` against an uploaded `WorkItem` and *fails* if any executor opens a file under `docs/work-items/`.
2. **Structural guard** (subprocess) that imports `executors/bootstrap.py` and asserts no `Path(...).read_text()` on a work-items path during module import or executor dispatch — same shape as `tests/test_runtime_deterministic_is_pure.py`.

## Implementation Steps

### Step 1: Open-audit context manager helper
**File:** `tests/integration/_filesystem_audit.py`
**Action:** Create
```python
from __future__ import annotations

import builtins
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager


class FilesystemAccessViolation(AssertionError):
    pass


@contextmanager
def assert_no_reads_under(forbidden_substr: str) -> Iterator[list[str]]:
    """Fail the test if any read targets a path containing `forbidden_substr`.

    Returns a list of all attempted reads for diagnostic output.
    """
    real_open = builtins.open
    real_read_text = pathlib.Path.read_text
    real_read_bytes = pathlib.Path.read_bytes
    accessed: list[str] = []

    def check(path: str | pathlib.Path) -> None:
        s = str(path)
        accessed.append(s)
        if forbidden_substr in s:
            raise FilesystemAccessViolation(
                f"forbidden filesystem read: {s} (matched substring '{forbidden_substr}')"
            )

    def wrapped_open(file, *a, **k):
        check(file)
        return real_open(file, *a, **k)

    def wrapped_read_text(self, *a, **k):
        check(self)
        return real_read_text(self, *a, **k)

    def wrapped_read_bytes(self, *a, **k):
        check(self)
        return real_read_bytes(self, *a, **k)

    builtins.open = wrapped_open
    pathlib.Path.read_text = wrapped_read_text
    pathlib.Path.read_bytes = wrapped_read_bytes
    try:
        yield accessed
    finally:
        builtins.open = real_open
        pathlib.Path.read_text = real_read_text
        pathlib.Path.read_bytes = real_read_bytes
```
Cheap, non-invasive — wraps the two functions for the test duration only.

### Step 2: End-to-end no-FS test
**File:** `tests/integration/test_runs_no_filesystem_access.py`
**Action:** Create
```python
@pytest.mark.asyncio
async def test_lifecycle_agent_advances_without_reading_work_items_dir(
    client: AsyncClient,
    session_factory,
) -> None:
    """A run started via upload reaches propose_tasks without reading
    docs/work-items/. AC-5."""
    payload = {
        "agentRef": "lifecycle-agent@0.3.0",
        "intake": {
            "workItem": {
                "id": "FEAT-TEST-001",
                "kind": "FEAT",
                "content": _load_fixture("minimal_feat_brief.md"),
            }
        },
    }

    with assert_no_reads_under("docs/work-items"):
        resp = await client.post("/api/v1/runs", json=payload)
        assert resp.status_code == 202
        run_id = resp.json()["data"]["id"]
        # Drive the run forward — at least past load_work_item
        await _drive_to_node(client, run_id, target_node="propose_tasks", timeout_s=10)

    # Verify the work_item was registered from upload, not disk
    async with session_factory() as session:
        wi = await session.scalar(
            select(WorkItem).where(WorkItem.external_ref == "FEAT-TEST-001")
        )
        assert wi is not None and wi.body_md is not None
```
Fixture file at `tests/fixtures/minimal_feat_brief.md` — a small valid brief that lets the lifecycle agent reach `propose_tasks`.

### Step 3: Structural guard (subprocess)
**File:** `tests/test_executors_dont_read_briefs.py`
**Action:** Create
```python
import subprocess
import sys
import textwrap


def test_bootstrap_module_does_not_read_briefs_at_import() -> None:
    """Importing executors.bootstrap must not touch any path under docs/work-items/.

    Same shape as tests/test_runtime_deterministic_is_pure.py (FEAT-009).
    """
    script = textwrap.dedent("""
        import builtins
        import pathlib

        violations = []

        def trap(path, *a, **k):
            s = str(path)
            if "docs/work-items" in s or "work-items/" in s:
                violations.append(s)
            return real_open(path, *a, **k)

        real_open = builtins.open
        builtins.open = trap
        real_read = pathlib.Path.read_text
        def trap_read(self, *a, **k):
            if "docs/work-items" in str(self):
                violations.append(str(self))
            return real_read(self, *a, **k)
        pathlib.Path.read_text = trap_read

        import app.modules.ai.executors.bootstrap  # noqa: F401

        import sys
        sys.exit(0 if not violations else 1)
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_load_work_item_handler_does_not_read_disk() -> None:
    """Calling _handle_request_work_item_load against a registered WorkItem
    must not invoke pathlib.Path.read_text."""
    # In-process unit test (not subprocess) — exercises the actual handler.
    # Implementation: use assert_no_reads_under("docs/work-items") around a
    # synthetic DispatchContext that points at a registered work_item.
    ...
```

### Step 4: Allow the one legitimate disk read site
**File:** `tests/integration/_filesystem_audit.py`
**Action:** Modify
T-288's `_read_legacy_brief` is the **only** allowed disk read of a work-item brief. Tests that go through the legacy path explicitly skip the audit (or use a narrower forbidden substring that doesn't catch it). Document this carve-out in the helper's docstring:
```python
"""... NOTE: T-288's deprecation shim is the only allowed disk read of a brief;
tests for the legacy path do not use this audit context."""
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/_filesystem_audit.py` | Create | `assert_no_reads_under` helper |
| `tests/integration/test_runs_no_filesystem_access.py` | Create | End-to-end test (AC-5) |
| `tests/test_executors_dont_read_briefs.py` | Create | Subprocess structural guard |
| `tests/fixtures/minimal_feat_brief.md` | Create | Minimal valid brief fixture |

## Edge Cases & Risks
- **Monkey-patching builtins is fragile.** Async I/O may use lower-level syscalls that bypass `builtins.open`. Acceptable: the audit catches `pathlib.Path.read_text` and `builtins.open`, which is what executors actually use. Document the limitation in the helper docstring.
- **Subprocess script string-escaping.** Triple-quoted strings work; using `textwrap.dedent` keeps the script readable. Watch for accidental brace conflicts if f-strings appear.
- **Test runtime cost.** The subprocess test forks Python. Acceptable: one fork per test run, ~100 ms. The end-to-end test drives a real run partway — keep `timeout_s=10` so flakes are visible early.
- **`_drive_to_node` helper.** May need to be implemented from scratch (poll `/api/v1/runs/{id}/trace`, look for `node_name == "propose_tasks"`). Reuse the existing trace-stream helpers from FEAT-004 tests if available.

## Acceptance Verification
- [ ] End-to-end test passes against a clean tmp Postgres with no `docs/work-items/` mounted.
- [ ] Structural guard catches a deliberate regression: temporarily add `Path("docs/work-items/FEAT-005.md").read_text()` to `_handle_request_work_item_load`; guard fails. Revert.
- [ ] Both tests run in the standard `uv run pytest` invocation.
- [ ] `_filesystem_audit.py` documents the T-288 legacy-shim carve-out.
