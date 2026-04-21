# Implementation Plan: T-140 — GitHub credential config

## Task Reference
- **Task ID:** T-140
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-2 — factory must pick `App > PAT > Noop` based on env vars.

## Overview
Add three `Settings` fields (`GITHUB_PAT`, `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`), reject "both PAT and App set" at startup, and surface the selected strategy in `orchestrator doctor`. No `GITHUB_REPO` — target repo is parsed per-task from the PR URL.

## Implementation Steps

### Step 1: Add credential fields to `Settings`
**File:** `src/app/config.py`
**Action:** Modify

Add to the "GitHub integration" block (already contains `github_webhook_secret`):

```python
github_pat: SecretStr | None = None
github_app_id: str | None = None
github_private_key: SecretStr | None = None
```

Follow the existing `SecretStr | None` pattern — no `Field(alias=...)` needed; `pydantic-settings` lowercases env keys automatically.

### Step 2: Add cross-field validator
**File:** `src/app/config.py`
**Action:** Modify

Alongside `_validate_llm_provider`, add a `@model_validator(mode="after")` that raises `ValueError("configure GITHUB_PAT or GITHUB_APP_ID+GITHUB_PRIVATE_KEY, not both")` when both credential families are set. Required: `github_pat` AND (`github_app_id` OR `github_private_key`). Also fail-fast when App credentials are half-set (only one of id/key present).

### Step 3: Surface the strategy in `doctor`
**File:** `src/app/cli.py`
**Action:** Modify

Extend `doctor` to print a "GitHub merge gating: <app|pat|noop>" line. Strategy resolution matches T-144's factory order — rather than duplicate the logic, expose a small helper in a shared module (T-144 will own that helper; for this task, inline a minimal `_resolved_github_strategy(settings) -> Literal["app","pat","noop"]` local function and migrate when T-144 lands).

### Step 4: Cover the validator + defaults
**File:** `tests/test_config.py`
**Action:** Modify

Add cases:
- Defaults → all three fields `None`.
- PAT only → validator passes; strategy reported as `pat`.
- App id + private key → passes; `app`.
- Both → raises `ValidationError`.
- App id alone (no key) or vice versa → raises.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | Three `SecretStr`/`str` fields + validator. |
| `src/app/cli.py` | Modify | `doctor` reports strategy. |
| `tests/test_config.py` | Modify | Validator + defaults. |

## Edge Cases & Risks
- **PEM in env var.** T-142 will extend the validator to accept `@file:/path/to/key.pem`; this task accepts raw PEM only — document the follow-up.
- **Import cycle risk.** If `cli.py` imports from `modules/ai/github/*`, circular import. Keep the `doctor` helper in `src/app/core/github.py` (T-144 creates it) or inline here.

## Acceptance Verification
- [ ] Fields present in `Settings()` with correct types.
- [ ] `Settings(github_pat="x", github_app_id="y", github_private_key="z")` raises.
- [ ] `uv run orchestrator doctor` prints the strategy line.
- [ ] `.env.example` block (already stubbed) matches field names.
- [ ] `uv run pyright` + `ruff` + `pytest tests/test_config.py` green.
