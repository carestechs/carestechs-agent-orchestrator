# Implementation Plan: T-141 — PR URL parser

## Task Reference
- **Task ID:** T-141
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-3 — multi-repo routing needs a central helper to parse `owner/repo/pull_number` from each task's PR URL.

## Overview
Pure helper extracts `(owner, repo, pull_number)` from a GitHub PR URL and rejects malformed input with a typed error. Shared between the service layer (T-145) and tests.

## Implementation Steps

### Step 1: Create the `github/` package
**File:** `src/app/modules/ai/github/__init__.py`
**Action:** Create

Empty file; exports land in child modules.

### Step 2: Define `PullRequestRef` + `parse_pr_url`
**File:** `src/app/modules/ai/github/pr_urls.py`
**Action:** Create

```python
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlparse
from app.core.exceptions import ValidationError

_ALLOWED_HOSTS = {"github.com", "www.github.com"}

@dataclass(frozen=True, slots=True)
class PullRequestRef:
    owner: str
    repo: str
    pull_number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

def parse_pr_url(url: str) -> PullRequestRef:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in _ALLOWED_HOSTS:
        raise ValidationError(code="invalid-pr-url", detail="PR URL must be an https github.com link")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 4 or parts[2] != "pull":
        raise ValidationError(code="invalid-pr-url", detail="expected /{owner}/{repo}/pull/{n}")
    owner, repo, _, raw_n = parts
    try:
        n = int(raw_n)
    except ValueError as exc:
        raise ValidationError(code="invalid-pr-url", detail="pull number must be an integer") from exc
    if n <= 0 or not owner or not repo:
        raise ValidationError(code="invalid-pr-url", detail="invalid segments")
    return PullRequestRef(owner=owner, repo=repo, pull_number=n)
```

Uses the existing `ValidationError` with kebab-case code per `core/exceptions.py` conventions. No regex — `urlparse` handles query/fragment/trailing slash correctly.

### Step 3: Unit tests
**File:** `tests/modules/ai/github/__init__.py`, `tests/modules/ai/github/test_pr_urls.py`
**Action:** Create

Cases:
- `https://github.com/foo/bar/pull/42` → `PullRequestRef("foo","bar",42)`.
- Trailing slash, query, fragment → still parses.
- `http://` scheme → raises.
- `https://gitlab.com/...` → raises.
- Path too short / too long → raises.
- Non-integer pull number → raises.
- Empty owner or repo → raises.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/github/__init__.py` | Create | Package init. |
| `src/app/modules/ai/github/pr_urls.py` | Create | `PullRequestRef` + `parse_pr_url`. |
| `tests/modules/ai/github/__init__.py` | Create | Package init. |
| `tests/modules/ai/github/test_pr_urls.py` | Create | 7+ cases. |

## Edge Cases & Risks
- **Enterprise GitHub.** `_ALLOWED_HOSTS` hardcodes `github.com`. If GHES support is ever needed, lift this to a setting — flag in T-150.
- **Case sensitivity.** GitHub owner/repo slugs are case-insensitive in routing but case-preserving for display. Keep the parsed case as-is; GitHub API accepts either.
- **SSH / git protocol URLs.** Explicitly rejected — webhook payloads use HTTPS URLs and the `prUrl` field is user-supplied.

## Acceptance Verification
- [ ] Happy path returns correct tuple.
- [ ] All 5+ invalid cases raise `ValidationError` with code `invalid-pr-url`.
- [ ] `uv run pyright` + `ruff` + `pytest tests/modules/ai/github/test_pr_urls.py` green.
