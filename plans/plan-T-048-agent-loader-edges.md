# Implementation Plan: T-048 — Agent-loader edge-case tests

## Task Reference
- **Task ID:** T-048
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-032

## Overview
Cover edge cases not exercised by T-032's happy-path tests: missing dir variations, duplicate refs, hash stability under formatting changes, unicode/ASCII mix, unreadable file.

## Steps

### 1. Create `tests/modules/ai/test_agents_loader_edges.py`

Organize as one `pytest.fixture(tmp_path)` helper + several test classes:

#### `TestMissingDir`
- Dir parent exists but subpath does not → `list_agents() == []`; `load_agent("x")` → `NotFoundError`.
- Dir is a file (not a directory) → `list_agents() == []` with warning log.

#### `TestDuplicateRefs`
- Two files resolve to the same ref (e.g., `foo.yaml` and `foo@1.0.yaml` both matching `"foo"`): documented precedence — versioned file wins if both present; test asserts that.

#### `TestHashStability`
- Load the same file twice → identical hash.
- Load two files with byte-identical contents → identical hash.
- Change a YAML field → hash changes.
- Whitespace-only changes to YAML source → hash changes (we canonicalize from parsed dict, not raw bytes; document this).

#### `TestUnicode`
- Fixture with non-ASCII in `description` round-trips cleanly (load + hash stable).

#### `TestUnreadableFile`
- Use `os.chmod(path, 0o000)` in a fixture (skip on Windows).
- `list_agents()` logs a warning and skips; `load_agent(ref)` raises with a clear error.

#### `TestReservedName`
- Agent YAML with a node named `terminate` → `ValidationError` on load (ties into T-034 reserved-name rule).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_agents_loader_edges.py` | Create | Edge-case test suite. |

## Edge Cases & Risks
- `os.chmod(0o000)` cleanup in teardown — use a try/finally or `@pytest.fixture` with `addfinalizer` to restore perms, else subsequent test tearDown can fail.
- Duplicate-ref precedence is a design decision — document in `agents.py` docstring after this task confirms the behavior.

## Acceptance Verification
- [ ] ≥8 parameterized cases across the classes above.
- [ ] All cases self-contained via `tmp_path`.
- [ ] No test relies on real `AGENTS_DIR`.
- [ ] Reserved-name rule enforced (if T-034 added it).
