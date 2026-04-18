# Implementation Plan: T-034 — Tool-definition builder (agent node → `ToolDefinition`)

## Task Reference
- **Task ID:** T-034
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-031

## Overview
Convert an agent's nodes into a policy tool list. Tool gating (omit a node from `available_nodes` → tool disappears) is the policy's action-space control surface per CLAUDE.md.

## Steps

### 1. Modify `src/app/modules/ai/tools/__init__.py`
- Add constant `TERMINATE_TOOL_NAME = "terminate"`.
- Add `_TERMINATE_TOOL = ToolDefinition(name="terminate", description="End the run cleanly; no further node dispatches.", parameters={"type": "object", "properties": {}})`.
- Public `def build_tools(agent: AgentDefinition, available_nodes: Iterable[str]) -> list[ToolDefinition]`:
  - Materialize `available_set = set(available_nodes)`.
  - Iterate `agent.nodes` in definition order; include `ToolDefinition(name=n.name, description=n.description, parameters=n.input_schema)` only if `n.name in available_set`.
  - Append `_TERMINATE_TOOL` once at the end.
- Return order is deterministic (iteration order of `agent.nodes` + terminate last) so tests can assert exactly.
- Unknown names in `available_nodes` are silently ignored (the set guides gating only).

### 2. Create `tests/modules/ai/test_tools_builder.py`
- Fixture: load `sample-linear.yaml` via `load_agent` or inline fake.
- Case: all nodes available → N+1 tools, `terminate` last.
- Case: partial gate — only `analyze_brief` available → 2 tools (`analyze_brief`, `terminate`).
- Case: empty gate → 1 tool (`terminate`).
- Case: unknown name in `available_nodes` → still filters by definition, no error.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/tools/__init__.py` | Modify | Add `build_tools`, `TERMINATE_TOOL_NAME`. |
| `tests/modules/ai/test_tools_builder.py` | Create | Gating + ordering tests. |

## Edge Cases & Risks
- Two agent nodes sharing a name would produce duplicate tools — `AgentDefinition` should enforce unique names; add a model validator in T-031 if not already present (defer if tight: this task can assume uniqueness and the validator is a follow-up).
- `terminate` must never collide with a real node name — document as a reserved name in `agents.py` and reject at load time in T-032.

## Acceptance Verification
- [ ] `build_tools(fixture, all_names)` returns `len(nodes) + 1` tools with `terminate` last.
- [ ] Empty gate yields only `terminate`.
- [ ] Reserved-name collision is rejected at agent load (cross-check with T-032).
- [ ] `uv run pytest tests/modules/ai/test_tools_builder.py -v` green.
