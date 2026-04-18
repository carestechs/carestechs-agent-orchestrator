# Implementation Plan: T-094 — `review_implementation` tool + `git.py` diff boundary

## Task Reference
- **Task ID:** T-094
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-089, T-091

## Overview
Two pieces in one PR. First, the *only* `subprocess` import in the codebase — `git.py` — provides `get_diff` for the review stage. Second, the `review_implementation` tool writes a structured verdict + feedback markdown. The diff itself is fetched by the runtime's prompt-assembly code (T-100 wires this); this tool only records the review.

## Steps

### 1. Create `src/app/modules/ai/tools/lifecycle/git.py`
The quarantined subprocess module:
```python
import subprocess

_DIFF_MAX_BYTES = 64 * 1024

def get_diff(paths: list[str], *, base: str = "main", cwd: Path | None = None) -> str:
    if shutil.which("git") is None:
        raise PolicyError("git not available")
    try:
        result = subprocess.run(
            ["git", "diff", f"{base}...HEAD", "--", *paths],
            check=False, text=True, capture_output=True, timeout=10,
            cwd=str(cwd or get_settings().repo_root),
        )
    except subprocess.TimeoutExpired:
        raise PolicyError("git diff timed out after 10s")
    except FileNotFoundError:
        raise PolicyError("git not available")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr.lower():
            raise PolicyError("not a git repository")
        if "unknown revision" in stderr.lower() or "bad revision" in stderr.lower():
            raise PolicyError(f"git diff failed: unknown base {base!r}")
        raise PolicyError(f"git diff failed: {stderr[:200]}")
    diff = result.stdout
    if len(diff.encode()) > _DIFF_MAX_BYTES:
        trunc = diff.encode()[:_DIFF_MAX_BYTES].decode(errors="replace")
        remaining = len(diff.encode()) - _DIFF_MAX_BYTES
        diff = trunc + f"\n...<truncated {remaining} bytes>...\n"
    return diff
```

### 2. Create `src/app/modules/ai/tools/lifecycle/review_implementation.py`
```python
TOOL_NAME = "review_implementation"

async def handle(args, *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id = args["task_id"]
    verdict = args["verdict"]
    feedback = args["feedback"]

    if verdict not in {"pass", "fail"}:
        raise PolicyError(f"invalid review verdict: {verdict!r}")

    match = next((t for t in memory.tasks if t.id == task_id), None)
    if match is None:
        raise PolicyError(f"unknown task: {task_id}")

    existing_attempts = [r for r in memory.review_history if r.task_id == task_id]
    attempt = len(existing_attempts) + 1
    slug = slugify(match.title)
    target = get_settings().repo_root / "plans" / f"plan-{task_id}-{slug}-review-{attempt}.md"
    content = f"# Review {attempt} — {task_id}\n\n**Verdict:** {verdict}\n\n{feedback}\n"
    write_atomic(target, content, repo_root=get_settings().repo_root)

    rel = str(target.relative_to(get_settings().repo_root))
    new_review = LifecycleReview(task_id=task_id, attempt=attempt, verdict=verdict,
                                  feedback=feedback, written_to=rel)
    return memory.model_copy(update={"review_history": [*memory.review_history, new_review]})
```

### 3. Create `tests/modules/ai/tools/lifecycle/test_git.py`
Four cases (the first that actually shells out):
- `test_get_diff_happy` — build a temp git repo in `tmp_path`, commit a file on `main`, create branch, modify + commit, assert `get_diff(["file"], base="main", cwd=tmp_path)` returns a non-empty string containing `+` and `-` lines.
- `test_get_diff_timeout` — monkeypatch `subprocess.run` to raise `TimeoutExpired`.
- `test_get_diff_missing_binary` — monkeypatch `shutil.which` to return `None`.
- `test_get_diff_not_a_repo` — call against `tmp_path` that has no `.git`; assert `PolicyError("not a git repository")`.

### 4. Create `tests/modules/ai/tools/lifecycle/test_review_implementation.py`
Five cases: happy pass, happy fail, invalid verdict, writes-to-correct-attempt-filename (call twice with same task_id → `-review-1.md` + `-review-2.md`), memory append correctness.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/lifecycle/git.py` | Create | Only `subprocess` import in the codebase. |
| `src/app/modules/ai/tools/lifecycle/review_implementation.py` | Create | Tool adapter. |
| `tests/modules/ai/tools/lifecycle/test_git.py` | Create | 4 cases (1 real git). |
| `tests/modules/ai/tools/lifecycle/test_review_implementation.py` | Create | 5 cases. |

## Edge Cases & Risks
- **Subprocess quarantine**: this is the one module allowed to import `subprocess`. T-106 extends the adapter-thin test to enforce this.
- **Git not in CI image**: verify `Dockerfile` installs `git`. If ephemeral CI workers strip it, the `test_get_diff_happy` case needs `@pytest.mark.skipif(shutil.which("git") is None)`.
- **Diff truncation cutoff at mid-UTF-8**: `.decode(errors="replace")` handles partial sequences; a replacement char in the trace is better than a crash.
- **Base branch name**: hardcoded `"main"`. If the project uses `master` or a feature-specific base, parameterize via `LIFECYCLE_REVIEW_BASE` env var in a future task; v1 assumes `main`.
- **Unknown-revision stderr matching**: Git's error strings differ by version. The substring checks are best-effort; fall-through is a generic `git diff failed` — still a `PolicyError`, run terminates.

## Acceptance Verification
- [ ] `get_diff` returns stdout on happy path; truncates at 64 KB with marker.
- [ ] Each failure mode → distinct `PolicyError` message.
- [ ] `review_implementation` writes `plan-<task>-<slug>-review-<attempt>.md`; attempt increments per task.
- [ ] Invalid verdict → `PolicyError`.
- [ ] `LifecycleReview` appended to `memory.review_history`.
- [ ] 9 unit tests pass (4 git + 5 review).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
