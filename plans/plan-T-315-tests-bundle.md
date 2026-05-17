# Implementation Plan: T-315 — Test bundle (DTO + accessor + service + fixtures + structural guard)

## Task Reference
- **Task ID:** T-315
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Five focused surfaces, one PR. DTO + accessor are pure; service-layer tests cover the deprecation flag; fixture updates keep existing suites green; structural guard catches future mutation of `Run.intake`.

## Overview
Add new test modules for the DTO and accessor, extend the existing `start_run` test module with deprecation-flag behavior, update integration test fixtures to supply `codeSource`, and extend (or add a sibling to) the existing executor structural guard to forbid `Run.intake` mutation.

## Implementation Steps

### Step 1: DTO tests
**File:** `tests/modules/ai/test_schemas_code_source.py`
**Action:** Create

```python
import pytest
from pydantic import ValidationError
from app.modules.ai.schemas import CodeSourceDto


class TestCodeSourceDto:
    def test_minimal(self) -> None:
        dto = CodeSourceDto(repo="org/name", baseBranch="main")
        assert dto.repo == "org/name"
        assert dto.base_branch == "main"
        assert dto.work_branch is None

    def test_alias_roundtrip(self) -> None:
        dto = CodeSourceDto.model_validate(
            {"repo": "org/name", "baseBranch": "main", "workBranch": "feat/x"}
        )
        assert dto.work_branch == "feat/x"
        assert dto.model_dump(by_alias=True)["workBranch"] == "feat/x"

    @pytest.mark.parametrize("bad", [
        "https://github.com/org/name",
        "git@github.com:org/name",
        "org/name.git",
        "",
        "   ",
        "org",
        "/org/name",
        "org//name",
    ])
    def test_invalid_repo_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo=bad, baseBranch="main")

    @pytest.mark.parametrize("bad", [
        "",
        "   ",
        " main ",
        "/main",
        "feat/..escape",
        "feat\tx",
        "feat\nx",
    ])
    def test_invalid_branch_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo="org/name", baseBranch=bad)

    def test_invalid_work_branch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo="org/name", baseBranch="main", workBranch="../escape")

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto.model_validate(
                {"repo": "org/name", "baseBranch": "main", "bogus": "x"}
            )
```

### Step 2: Accessor tests
**File:** `tests/modules/ai/executors/test_code_source_accessor.py`
**Action:** Create

Build a minimal `DispatchContext` (or a `SimpleNamespace` mock if the real one is heavy to construct in tests — check existing test patterns first):

```python
import copy
import pytest
from app.modules.ai.executors.code_source import read_code_source

INTAKE_BASE = {
    "codeSource": {"repo": "org/name", "baseBranch": "main"}
}
INTAKE_WITH_WB = {
    "codeSource": {"repo": "org/name", "baseBranch": "main", "workBranch": "intake/x"}
}


def _ctx(intake: dict) -> object:
    from types import SimpleNamespace
    return SimpleNamespace(intake=intake)


class TestReadCodeSource:
    def test_intake_only(self) -> None:
        dto = read_code_source(_ctx(INTAKE_WITH_WB))
        assert dto.work_branch == "intake/x"

    def test_memory_override_when_intake_workbranch_none(self) -> None:
        dto = read_code_source(
            _ctx(INTAKE_BASE),
            memory={"codeSource": {"workBranch": "memory/x"}},
        )
        assert dto.work_branch == "memory/x"

    def test_operator_workbranch_wins_when_memory_absent(self) -> None:
        dto = read_code_source(_ctx(INTAKE_WITH_WB), memory={})
        assert dto.work_branch == "intake/x"

    def test_memory_none_workbranch_does_not_override(self) -> None:
        dto = read_code_source(
            _ctx(INTAKE_WITH_WB),
            memory={"codeSource": {"workBranch": None}},
        )
        assert dto.work_branch == "intake/x"

    def test_memory_empty_workbranch_does_not_override(self) -> None:
        dto = read_code_source(
            _ctx(INTAKE_WITH_WB),
            memory={"codeSource": {"workBranch": ""}},
        )
        assert dto.work_branch == "intake/x"

    def test_missing_intake_raises(self) -> None:
        with pytest.raises(ValueError, match="codeSource missing from intake"):
            read_code_source(_ctx({}))

    def test_no_mutation(self) -> None:
        intake = copy.deepcopy(INTAKE_WITH_WB)
        memory = {"codeSource": {"workBranch": "memory/x"}}
        intake_snap = copy.deepcopy(intake)
        memory_snap = copy.deepcopy(memory)
        read_code_source(_ctx(intake), memory=memory)
        assert intake == intake_snap
        assert memory == memory_snap
```

### Step 3: Service-layer tests
**File:** `tests/modules/ai/test_service_start_run.py` (or canonical equivalent)
**Action:** Read, then Modify

Locate the existing tests. Add cases:
- `test_start_run_soft_mode_accepts_missing_code_source_and_warns` — `LIFECYCLE_CODE_SOURCE_REQUIRED=false`; `caplog.records` contains one WARNING.
- `test_start_run_strict_mode_rejects_missing_code_source` — flag=true; expect `ValidationError` / 400.
- `test_start_run_persists_code_source` — well-formed payload round-trips to `Run.intake.codeSource`.
- `test_start_run_rejects_malformed_repo_regardless_of_setting` — both flag values, payload with `repo="https://..."`; both reject.

Use the project's existing `monkeypatch.setenv` or settings-override fixture; don't read env directly.

### Step 4: Integration fixture updates
**File:** `tests/integration/test_lifecycle_v04_manual.py`, `tests/integration/test_lifecycle_v03.py`
**Action:** Modify

Every `POST /api/v1/runs` body in these tests grows a `codeSource` block:

```python
"codeSource": {
    "repo": "carestechs/orchestrator-test-fixture",
    "baseBranch": "main",
}
```

No assertion changes — this is a non-behavioral fixture update. Existing tests stay green.

If a shared `_make_intake(...)` helper exists, update it once instead of touching every call site.

### Step 5: Structural guard
**File:** `tests/test_executors_dont_read_briefs.py` (or new sibling `tests/test_executors_dont_mutate_intake.py`)
**Action:** Read, then Modify or Create

Extend the existing AST-walk pattern (or write a small visitor) that:
1. Iterates every module under `src/app/modules/ai/executors/`.
2. Parses each with `ast.parse`.
3. Walks for `ast.Assign` / `ast.AugAssign` whose targets include a `Subscript` whose value chain ends in an attribute named `intake`.
4. Fails the test if any are found.

If the existing brief-read guard already walks executor modules, layer the new check into the same visitor — one file pass, two checks.

### Step 6: Run the suite
**File:** N/A
**Action:** Run

```bash
uv run pytest tests/modules/ai/test_schemas_code_source.py \
              tests/modules/ai/executors/test_code_source_accessor.py \
              tests/modules/ai/test_service_start_run.py \
              tests/integration/ \
              tests/test_executors_dont_read_briefs.py -v
```

All green. Then a full suite:

```bash
uv run pytest
```

No regressions.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_schemas_code_source.py` | Create | DTO validator tests. |
| `tests/modules/ai/executors/test_code_source_accessor.py` | Create | Precedence + no-mutation tests. |
| `tests/modules/ai/test_service_start_run.py` | Modify | Strict/soft/persist/malformed cases. |
| `tests/integration/test_lifecycle_v04_manual.py` | Modify | Fixture: add `codeSource`. |
| `tests/integration/test_lifecycle_v03.py` | Modify | Fixture: add `codeSource`. |
| `tests/test_executors_dont_read_briefs.py` (or sibling) | Modify or Create | AST guard for `Run.intake` mutation. |

## Edge Cases & Risks
- **`DispatchContext` construction in accessor tests:** if `DispatchContext` is a dataclass requiring many fields, prefer `SimpleNamespace` for the tests above — the accessor only reads `.intake`, and the test stays simple. If the existing test file imports the real one, follow that.
- **Integration fixture drift:** if other integration tests (e.g. FEAT-014, FEAT-011) also POST runs, they may need the fixture update too. Grep `"/api/v1/runs"` under `tests/integration/` and update all hits.
- **`caplog` ordering:** the deprecation warning test asserts presence of one WARNING with the expected message substring — don't assert it's the only log line. Production code may emit info logs in the same path.
- **Structural guard false positives:** `dispatch.intake` (the Dispatch model's column attribute) might also match if not careful. Scope the walk strictly to AST `Assign` targets ending in `Subscript` against attribute chains rooted at common executor parameters (`ctx`, `run`). A tighter walker beats a broad regex.
- **Test parallelization:** if the service-layer test mutates settings, use `monkeypatch.setattr(settings_module, "lifecycle_code_source_required", True)` rather than `setenv` — env changes don't always re-evaluate cached `get_settings()`.

## Acceptance Verification
- [ ] All new test functions pass.
- [ ] `uv run pytest tests/` passes end-to-end.
- [ ] Deliberately inserting `ctx.intake["codeSource"] = ...` into any executor module causes the structural guard to fail (manually verify, then revert).
- [ ] Deliberately removing the `if memory_work_branch:` guard in `read_code_source` causes the empty-string and None tests to fail.
- [ ] Deliberately removing the deprecation warning in `start_run` causes the `caplog` test to fail.
- [ ] No `# type: ignore` introduced.
- [ ] No FEAT-014 / FEAT-015 / FEAT-011 regressions.
