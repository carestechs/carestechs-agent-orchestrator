# Implementation Plan: T-313 — `LIFECYCLE_CODE_SOURCE_REQUIRED` setting + `start_run` enforcement

## Task Reference
- **Task ID:** T-313
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** One-minor-release deprecation window keeps scripted callers alive while the contract tightens. Soft-path warning turns silent drift into visible drift.

## Overview
Add `lifecycle_code_source_required: bool = False` to `app.config.Settings`. Add a branch at the top of `service.start_run` that either rejects the run (strict mode) or logs a one-time deprecation warning (soft mode) when `intake.codeSource` is absent. Field-level validation on shape is already covered by `CodeSourceDto` (T-311).

## Implementation Steps

### Step 1: Add the setting
**File:** `src/app/config.py`
**Action:** Modify

Find the existing `Settings` class. Append a new field next to similar lifecycle-feature flags (e.g. `LIFECYCLE_REVIEWER`, `LIFECYCLE_MAX_CORRECTIONS`):

```python
lifecycle_code_source_required: bool = Field(
    default=False,
    description=(
        "When True, POST /api/v1/runs rejects intakes missing the "
        "codeSource block. When False (deprecation window), missing "
        "codeSource logs a warning and accepts the run."
    ),
)
```

Pydantic-settings auto-maps to env `LIFECYCLE_CODE_SOURCE_REQUIRED` per the project's existing naming convention (UPPER_SNAKE, project-prefixed).

### Step 2: Locate `start_run`
**File:** `src/app/modules/ai/service.py`
**Action:** Read

Find `async def start_run(...)`. Identify where intake is parsed into the DTO and where `Run` is constructed. The new branch lives between those two points.

### Step 3: Add the enforcement branch
**File:** `src/app/modules/ai/service.py`
**Action:** Modify

Insert after the intake DTO is fully validated and before the run is persisted:

```python
settings = get_settings()
if intake.code_source is None:
    if settings.lifecycle_code_source_required:
        raise ValidationError(
            code="intake-validation-failed",
            detail="intake.codeSource is required",
        )
    logger.warning(
        "intake.codeSource missing — falling back to deprecation window; "
        "flip LIFECYCLE_CODE_SOURCE_REQUIRED=true to enforce",
        extra={
            "agent_ref": agent_ref,
            "work_item_id": (
                intake.work_item.id if intake.work_item else None
            ),
        },
    )
```

`ValidationError` is the typed exception from `core.exceptions` that already maps to 400 RFC-7807 — no new exception type.

### Step 4: Verify the warning fires once per run
**File:** `src/app/modules/ai/service.py`
**Action:** Verify

The branch sits in `start_run`, which is called exactly once per `POST /api/v1/runs`. No loop, no retry — one warning per missing-codeSource run start.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | New `lifecycle_code_source_required` setting. |
| `src/app/modules/ai/service.py` | Modify | New branch in `start_run`. |

## Edge Cases & Risks
- **Don't read `os.environ` directly.** Always go through `get_settings()` — the existing pattern. Direct env reads break test isolation.
- **Don't validate shape here.** Shape errors come from the DTO (T-311). This branch only checks presence.
- **Log key choice:** `work_item_id` is the most operator-recognizable correlator when `run_id` isn't assigned yet. If the intake also lacks `work_item` (legacy intake mode), the log line still fires with `work_item_id: None` — acceptable; the message itself is the value.
- **Persistence:** `Run.intake` stores whatever was on the DTO via `model_dump(by_alias=True)` (existing pattern). When `code_source is None`, the persisted JSONB simply omits the key — no migration concern.
- **CLI parity:** `orchestrator run` builds its intake the same way as the HTTP route (both call `start_run`). The warning fires identically for CLI callers — by design.

## Acceptance Verification
- [ ] `LIFECYCLE_CODE_SOURCE_REQUIRED=false` (default): a `POST /api/v1/runs` body without `intake.codeSource` returns 202; a `WARNING` log line appears with `agent_ref` and `work_item_id`.
- [ ] `LIFECYCLE_CODE_SOURCE_REQUIRED=true`: the same body returns 400 with Problem Details body, `code=intake-validation-failed`, `detail` mentioning `codeSource`.
- [ ] With `intake.codeSource` present and well-formed, both settings accept the run; the persisted `Run.intake.codeSource` round-trips through `GET /api/v1/runs/{id}`.
- [ ] Malformed `codeSource` (e.g. `repo="https://..."`) returns 400 regardless of the setting (DTO path, not service path).
- [ ] Setting reachable via `app.config.get_settings()`.
- [ ] No direct `os.environ` access in `service.py`.
- [ ] Warning fires exactly once per run start (no loop, no retry path adds duplicates).
