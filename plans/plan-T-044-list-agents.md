# Implementation Plan: T-044 — `list_agents` via the loader

## Task Reference
- **Task ID:** T-044
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-032

## Overview
Thin passthrough from the AGENTS_DIR loader. Invalid YAML surfaces as a 500 (not a silent skip) per the scope lock's "observability is non-negotiable" principle.

## Steps

### 1. Modify `src/app/modules/ai/service.py`
Replace `list_agents()` body:
- Call `agents.list_agents()`.
- For each `AgentDefinition` map to `AgentDto(ref=..., definition_hash=agent.agent_definition_hash, path=str(agent_path_if_available), intake_schema=agent.intake_schema, available_nodes=[n.name for n in agent.nodes])`.
- `path` requires the loader to return path info. Extend T-032's loader to return `(AgentDefinition, Path)` pairs, OR attach `_source_path` as a private field on the model and surface it here. Prefer a small `AgentRecord(definition: AgentDefinition, path: Path)` dataclass for clarity.

### 2. Modify `src/app/modules/ai/agents.py`
- Change `list_agents()` to return `list[AgentRecord]` and `load_agent()` to return `AgentRecord` for symmetry. Update T-032's tests to match the new shape (minor adjustment).

### 3. Create `tests/modules/ai/test_service_agents.py`
- Happy: 2 YAMLs in `tmp_path` → 2 DTOs in sorted order.
- Empty dir → `[]`.
- Invalid YAML → raises (service layer does not swallow; let the global 500 handler catch).
- DTO round-trip camelCase check.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/agents.py` | Modify | Introduce `AgentRecord(definition, path)`. |
| `src/app/modules/ai/service.py` | Modify | Real `list_agents`. |
| `tests/modules/ai/test_agents_loader.py` | Modify | Update to `AgentRecord` shape. |
| `tests/modules/ai/test_service_agents.py` | Create | Service-layer tests. |

## Edge Cases & Risks
- Secrets in YAML (e.g., API keys baked into nodes): the `AgentDto.intake_schema` leaks the schema but not runtime values — safe. Document the guidance in CLAUDE.md (T-061).
- A malformed YAML crashing the endpoint is by-design: we want operators to notice broken agents, not silently miss them.

## Acceptance Verification
- [ ] Empty dir → `[]`, not a 5xx.
- [ ] Valid YAMLs return `AgentDto` list with correct fields.
- [ ] Invalid YAML returns 500 Problem Details with the exception context logged.
