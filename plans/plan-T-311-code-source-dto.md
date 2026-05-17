# Implementation Plan: T-311 — `CodeSourceDto` schema with format validators

## Task Reference
- **Task ID:** T-311
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Symmetric with FEAT-014's `WorkItemIntakeDto`. One DTO, two `field_validator`s, one extension to the run-start intake model.

## Overview
Add `CodeSourceDto` to `src/app/modules/ai/schemas.py` with field-level validators for `repo` (GitHub `owner/name` regex) and branch names (whitespace/control-char/escape-sequence rejection). Extend the existing run-start intake DTO with an optional `code_source: CodeSourceDto | None` field. Required-ness lives at the service layer (T-313), not the DTO — that keeps one DTO across the deprecation window.

## Implementation Steps

### Step 1: Locate the existing run-start intake DTO
**File:** `src/app/modules/ai/schemas.py`
**Action:** Read

Find the DTO that today carries `workItem` (FEAT-014). Likely named `IntakeDto`, `RunIntakeDto`, or `RunStartIntake`. Note its `model_config` (should be `_CAMEL_CONFIG`) and its declaration shape.

### Step 2: Add `CodeSourceDto`
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify

Insert near the existing intake DTOs:

```python
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class CodeSourceDto(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid",
    )

    repo: str
    base_branch: str
    work_branch: str | None = None

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("repo must be non-empty")
        if not _REPO_PATTERN.fullmatch(stripped):
            raise ValueError(
                "repo must match GitHub 'owner/name' shape — no URL prefix, "
                "no '.git' suffix (got: %r)" % value
            )
        if stripped.endswith(".git"):
            raise ValueError("repo must not include '.git' suffix")
        return stripped

    @field_validator("base_branch", "work_branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or not value.strip():
            raise ValueError("branch name must be non-empty")
        if value.startswith("/"):
            raise ValueError("branch name must not start with '/'")
        if ".." in value:
            raise ValueError("branch name must not contain '..'")
        if any(ch.isspace() for ch in value):
            raise ValueError("branch name must not contain whitespace")
        if any(ord(ch) < 0x20 for ch in value):
            raise ValueError("branch name must not contain control characters")
        return value
```

Note: the `.git` suffix matches `_REPO_PATTERN` (`.` is allowed in the segment), so the explicit `endswith(".git")` check is required — the regex alone won't catch it.

### Step 3: Extend the run-start intake DTO
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify

Add `code_source: CodeSourceDto | None = None` to the model identified in Step 1. The camelCase alias `codeSource` is generated automatically by `_CAMEL_CONFIG`.

### Step 4: Verify imports
**File:** `src/app/modules/ai/schemas.py`
**Action:** Verify

`re` is already imported (line 14). `ConfigDict`, `Field`, `field_validator` are already imported (line 16). `to_camel` is already imported (line 17). No new imports needed.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Add `CodeSourceDto` + extend run-start intake DTO. |

## Edge Cases & Risks
- **`.git` suffix passes regex.** The regex allows `.` and the segment `name.git` matches — the explicit `endswith(".git")` check is non-negotiable. Test it.
- **`extra="forbid"`** on the DTO catches operator typos like `workbranch` (lowercase). Without it, the typo silently no-ops because the camelCase alias matcher misses.
- **Required-ness deliberately deferred.** The DTO field is `CodeSourceDto | None = None`; the service layer (T-313) enforces required-ness based on the env flag. Don't move enforcement to the DTO — that would couple the schema to the deprecation flag.
- **Service-layer regex collisions.** None — this is the only regex on `repo` in the codebase. Confirm with grep.

## Acceptance Verification
- [ ] `CodeSourceDto(repo="org/name", baseBranch="main")` validates; `work_branch is None`.
- [ ] `CodeSourceDto(repo="https://github.com/org/name", baseBranch="main")` raises `ValidationError`.
- [ ] `CodeSourceDto(repo="org/name.git", baseBranch="main")` raises with message mentioning `.git`.
- [ ] `CodeSourceDto(repo="org/name", baseBranch=" main ")` raises (whitespace).
- [ ] `CodeSourceDto(repo="org/name", baseBranch="main", workBranch="../escape")` raises.
- [ ] `CodeSourceDto.model_validate({"repo": "org/name", "baseBranch": "main", "workBranch": "feat/x"})` round-trips; `dto.model_dump(by_alias=True)["workBranch"] == "feat/x"`.
- [ ] `CodeSourceDto.model_validate({"repo": "org/name", "baseBranch": "main", "bogus": "x"})` raises (extra forbidden).
- [ ] `pyright` clean.
