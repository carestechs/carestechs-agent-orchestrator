# Implementation Plan: T-047 — Doctor agents-dir check

## Task Reference
- **Task ID:** T-047
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-032

## Overview
Extend `doctor` with an "agents_dir readable" check. Missing dir is a soft warning (first-time setup); unreadable / invalid YAML is hard fail.

## Steps

### 1. Modify `src/app/doctor.py`
- Add new check function `check_agents_dir() -> DoctorResult`:
  - Read `settings.agents_dir`.
  - If dir does not exist → `status="warn"`, `detail=f"agents dir not found at {path}; place agent YAMLs there or set AGENTS_DIR"`.
  - Else call `agents.list_agents()` inside a try/except:
    - Success → `status="ok"`, `detail=f"{n} agent(s) found"`.
    - Exception → `status="fail"`, `detail=str(exc)`.
- Register in the check list (below existing env-var checks).
- Ensure `format_human` + `format_json` handle a `warn` status (printed as `⚠`, not counted as failure).
- Adjust exit logic: exit 2 iff any check has `status="fail"`. `warn` does not trigger exit 2.

### 2. Modify `src/app/doctor.py` — result type (if needed)
- Ensure `DoctorResult.status` can be one of `{"ok", "warn", "fail"}`. Add `"warn"` if the enum/Literal doesn't already include it.

### 3. Extend `tests/test_cli_doctor.py`
- Missing `AGENTS_DIR` → exit 0, output contains `⚠` and "agents dir not found".
- Valid agents dir with 1 YAML → exit 0, check shows `✓`.
- Dir exists but contains malformed YAML → exit 2, output contains the exception message.
- JSON output: `--json` mode includes the new check with its status.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/doctor.py` | Modify | Add `check_agents_dir` + `warn` status support. |
| `tests/test_cli_doctor.py` | Modify | Three new cases (missing / valid / malformed). |

## Edge Cases & Risks
- Permission-denied on dir read should be treated as `fail` (not `warn`) — we can't verify the setup is correct.
- Large AGENTS_DIR with many files: don't load all of them in `doctor` (performance). Calling `list_agents()` is acceptable because it's a read-on-demand loop; document that doctor's timing is O(N).

## Acceptance Verification
- [ ] Missing dir → warning, exit 0.
- [ ] Malformed YAML → fail, exit 2.
- [ ] Human + JSON outputs both carry the new check.
- [ ] Existing `TestDoctorHappy` / `TestDoctorMissingEnv` tests still pass (no regression).
