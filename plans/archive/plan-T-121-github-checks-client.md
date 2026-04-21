# Implementation Plan: T-121 — GitHub Checks API client + merge gating

## Task Reference
- **Task ID:** T-121
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-120

## Overview
Thin HTTP client for the GitHub Checks API. `create_check` registers `orchestrator/impl-review` as pending; `update_check` resolves it to `success` or `failure`. Factory picks App > PAT > Noop based on config. Composition-root wires into T-118's review endpoints.

## Steps

### 1. Modify `src/app/config.py`
- Add:
  ```python
  github_app_id: str | None = Field(default=None, alias="GITHUB_APP_ID")
  github_private_key: SecretStr | None = Field(default=None, alias="GITHUB_PRIVATE_KEY")
  github_pat: SecretStr | None = Field(default=None, alias="GITHUB_PAT")
  github_repo: str | None = Field(default=None, alias="GITHUB_REPO")  # "owner/repo"
  ```

### 2. Create `src/app/modules/ai/github/__init__.py`
- Empty.

### 3. Create `src/app/modules/ai/github/checks.py`
- Protocol:
  ```python
  class GitHubChecksClient(Protocol):
      async def create_check(self, *, repo: str, head_sha: str, name: str) -> str: ...  # returns check_id
      async def update_check(self, *, repo: str, check_id: str, conclusion: Literal["success","failure"]) -> None: ...
  ```
- `HttpxGitHubChecksClient`:
  - Accepts an `AuthStrategy` (App or PAT) that produces `Authorization` headers.
  - `create_check` → `POST /repos/{repo}/check-runs` with `{name, head_sha, status: "in_progress"}`.
  - `update_check` → `PATCH /repos/{repo}/check-runs/{check_id}` with `{status: "completed", conclusion}`.
  - Uses a shared `httpx.AsyncClient` (per `app.core` conventions).
- `NoopGitHubChecksClient`: logs one warning on first call; `create_check` returns `"noop"`; `update_check` no-op.
- `AppAuthStrategy`: signs a JWT (RS256) with `PyJWT[crypto]` for `iss=app_id`, exchanges for an installation token (cached per-repo for 50 min).
- `PatAuthStrategy`: static `Authorization: Bearer <pat>`.

### 4. Create `src/app/core/github.py`
- `def get_github_checks_client(settings: Settings) -> GitHubChecksClient`:
  - App config present → `HttpxGitHubChecksClient(AppAuthStrategy(...))`.
  - PAT present → `HttpxGitHubChecksClient(PatAuthStrategy(...))`.
  - Else → `NoopGitHubChecksClient()`.
- FastAPI dependency in `src/app/modules/ai/dependencies.py`:
  ```python
  async def get_github_checks_client_dep(settings = Depends(get_settings)) -> GitHubChecksClient:
      return get_github_checks_client(settings)
  ```
  (Replace the placeholder from T-118.)

### 5. Modify `src/app/modules/ai/service.py`
- Wire the injected client into `submit_implementation_signal`, `approve_review_signal`, `reject_review_signal` (already stubbed in T-118).

### 6. Create `tests/modules/ai/github/__init__.py`, `tests/modules/ai/github/test_checks.py`
- `respx` mocks for `POST /check-runs` and `PATCH /check-runs/{id}`.
- Tests: create returns id; update with success/failure serializes correctly; auth header formatted per strategy; Noop path logs once and returns.
- Transient 5xx → client raises for the caller to decide (service-layer logs + continues per T-118).

### 7. Modify `src/app/main.py` (if wiring lives here)
- Register the dependency on startup per existing `core.llm` pattern.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | GitHub config fields. |
| `src/app/core/github.py` | Create | Factory. |
| `src/app/modules/ai/github/__init__.py` | Create | Package init. |
| `src/app/modules/ai/github/checks.py` | Create | Protocol + clients + auth strategies. |
| `src/app/modules/ai/dependencies.py` | Modify | Replace T-118 placeholder with real dep. |
| `src/app/modules/ai/service.py` | Modify | Wire client into review adapters. |
| `tests/modules/ai/github/test_checks.py` | Create | Unit tests. |

## Edge Cases & Risks
- **Installation token cache** — cache per repo, TTL 50 min (tokens last 60). Don't refresh on every call.
- **Private-key format** — PEM in an env var is awkward; document loading via file path as an alternative (`GITHUB_PRIVATE_KEY_PATH`).
- **Rate limits** — GitHub API is rate-limited; a single review cycle calls 2 endpoints (create + update). Acceptable for v1. Document in the risks section of T-127.
- **Noop warning spam** — log once per process, not per call; use a module-level flag.

## Acceptance Verification
- [ ] Protocol + `HttpxGitHubChecksClient` + `NoopGitHubChecksClient`.
- [ ] Factory picks App > PAT > Noop.
- [ ] Unit tests with `respx` cover create + update.
- [ ] Noop path tested and documented.
- [ ] T-118's review endpoints exercised with a recording double see correct calls.
- [ ] `uv run pyright`, `ruff`, tests green.
