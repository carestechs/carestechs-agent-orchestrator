# Implementation Plan: T-070 — Doctor: validate `ANTHROPIC_API_KEY` shape

## Task Reference
- **Task ID:** T-070
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-063

## Overview
Tighten `doctor`'s LLM check: when `LLM_PROVIDER=anthropic`, verify the API key is non-empty, ≥20 chars, and starts with `sk-ant-`. Missing / malformed → `fail`; well-formed → `ok`. Stub provider path is unchanged. No network call.

## Steps

### 1. Modify `src/app/doctor.py`
- Replace the existing `_check_llm_config` body's anthropic branch:
  ```python
  def _check_llm_config() -> CheckResult:
      import os

      provider = os.environ.get("LLM_PROVIDER", "stub")
      if provider == "stub":
          return CheckResult("llm_provider", "ok", "Using stub provider (no API key needed)")
      if provider == "anthropic":
          key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
          if not key:
              return CheckResult(
                  "llm_provider",
                  "fail",
                  "Provider anthropic but ANTHROPIC_API_KEY is not set",
              )
          if not key.startswith("sk-ant-") or len(key) < 20:
              return CheckResult(
                  "llm_provider",
                  "fail",
                  "ANTHROPIC_API_KEY does not look like an Anthropic key "
                  "(expected 'sk-ant-…' with length ≥ 20). "
                  "A live check would require a network call and is skipped; "
                  "run `orchestrator run` to catch 401s.",
              )
          return CheckResult(
              "llm_provider",
              "ok",
              f"Provider anthropic; key looks well-formed ({len(key)} chars)",
          )
      return CheckResult("llm_provider", "warn", f"Unknown provider: {provider}")
  ```

### 2. Modify `tests/test_cli_doctor.py`
- In `TestDoctorMissingEnv`, add:
  - `test_anthropic_missing_key_fails` — env `LLM_PROVIDER=anthropic` and no key → exit 2 + message names `ANTHROPIC_API_KEY`.
  - `test_anthropic_malformed_key_fails` — env `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=short`. Exit 2 + message says "does not look like an Anthropic key".
  - `test_anthropic_valid_key_passes` — env `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=sk-ant-` + 20 `x`s. Exit 0; output contains `"well-formed"`.
  - `test_stub_provider_unchanged` — env `LLM_PROVIDER=stub`, no key. Exit 0 (regression guard).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/doctor.py` | Modify | Tighten anthropic branch in `_check_llm_config`. |
| `tests/test_cli_doctor.py` | Modify | 4 new cases. |

## Edge Cases & Risks
- Prefix check MUST be `sk-ant-` (hyphen, not underscore). Double-check when writing the test.
- Users on older deployments may have set `LLM_PROVIDER=anthropic` without the key — this check will now FAIL their doctor. That's intentional — the doctor output makes the remediation obvious.
- Real 401s are still only detectable at runtime; the doctor message explicitly says so.

## Acceptance Verification
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + no key → exit 2, message names the env var.
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + malformed key → exit 2, message says "does not look like an Anthropic key".
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + valid key → exit 0, `"well-formed"` in output.
- [ ] `doctor` with `LLM_PROVIDER=stub` + no key → exit 0 (unchanged).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
