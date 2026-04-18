# Implementation Plan: T-032 — Agent loader (`load_agent`, `list_agents`) with hashing

## Task Reference
- **Task ID:** T-032
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-031

## Overview
Add filesystem loading to `agents.py`: walk `Settings.agents_dir`, match `{ref}@{version}.yaml`, parse + validate + hash. Single entry point for all agent data.

## Steps

### 1. Modify `src/app/modules/ai/agents.py`
- Add `import hashlib`, `yaml`, `pathlib.Path`.
- Private helper `_canonicalize(raw: dict) -> bytes`: sort keys recursively, `json.dumps(..., sort_keys=True).encode()` — canonical byte form the hash consumes.
- Private helper `_parse_file(path: Path) -> AgentDefinition`: open file, `yaml.safe_load`, validate via `AgentDefinition.model_validate`, compute hash from canonicalized bytes, set on the model (use a `model_copy(update={"agent_definition_hash": h})` or a dedicated field setter method).
- Public `load_agent(ref: str) -> AgentDefinition`: accept `ref` as `"name"` or `"name@version"`. Look up `{agents_dir}/{ref}.yaml` OR `{agents_dir}/{name}@{version}.yaml`. Missing → `NotFoundError(f"agent not found: {ref}")`. Invalid YAML/schema → raises `ValidationError`.
- Public `list_agents() -> list[AgentDefinition]`: glob `*.yaml` in `agents_dir`, skip unreadable files with a `WARNING` log, return sorted by `(ref, version)`. Missing dir → `[]`.
- All IO is sync (loader runs during request handling; YAML files are tiny and this avoids async/sync coupling).

### 2. Create `tests/modules/ai/test_agents_loader.py`
- Use `tmp_path` + `monkeypatch.setattr` on `Settings.agents_dir` (or pass an override via a module-level `_agents_dir()` helper that's patchable).
- Case: load known ref → returns AgentDefinition with stable hash.
- Case: unknown ref → `NotFoundError`.
- Case: invalid YAML → `ValidationError`.
- Case: missing dir → `list_agents() == []`, `load_agent(...)` raises `NotFoundError`.
- Case: same file loaded twice → identical hash (determinism).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/agents.py` | Modify | Add `load_agent`, `list_agents`, hashing helpers. |
| `tests/modules/ai/test_agents_loader.py` | Create | Loader happy + edge tests. |

## Edge Cases & Risks
- File named with `@` on case-insensitive filesystems (macOS default) — ensure globbing is case-sensitive or document the constraint.
- Hashes should be stable across Python versions — `json.dumps(sort_keys=True)` is; avoid `yaml.dump` round-trips for the hash input.
- If `agents_dir` is an absolute path vs relative, resolve via `Path.resolve()` once so tests behave identically.

## Acceptance Verification
- [ ] `load_agent("sample-linear@1.0")` returns validated model + matching hash.
- [ ] `list_agents()` sorted; empty list on missing dir.
- [ ] Unknown ref → `NotFoundError`.
- [ ] Invalid YAML → `ValidationError`.
- [ ] `uv run pytest tests/modules/ai/test_agents_loader.py -v` green.
