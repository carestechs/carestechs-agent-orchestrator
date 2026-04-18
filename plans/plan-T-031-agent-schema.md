# Implementation Plan: T-031 — Add YAML dependency and `AgentDefinition` schema

## Task Reference
- **Task ID:** T-031
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Define the YAML agent contract as Pydantic models (no loader yet — that's T-032). Add `pyyaml` to runtime deps and ship a 3-node linear fixture used across the suite.

## Steps

### 1. Modify `pyproject.toml`
- Append `"pyyaml>=6,<7"` to `[project].dependencies`.
- Run `uv lock` so `uv.lock` regenerates cleanly.

### 2. Create `src/app/modules/ai/agents.py`
- Import `BaseModel`, `ConfigDict`, `to_camel`. Shared `_CAMEL_CONFIG` mirrors `schemas.py`.
- Define `AgentNode(BaseModel)`: `name: str`, `description: str`, `input_schema: dict[str, Any]` (JSON Schema), `timeout_seconds: int = 300`.
- Define `AgentFlow(BaseModel)`: `entry_node: str`, `transitions: dict[str, list[str]]` (node → allowed next nodes).
- Define `BudgetDefaults(BaseModel)`: `max_steps: int | None = None`, `max_tokens: int | None = None`.
- Define `AgentDefinition(BaseModel)`: `ref: str`, `version: str`, `description: str`, `nodes: list[AgentNode]`, `flow: AgentFlow`, `intake_schema: dict[str, Any]`, `terminal_nodes: set[str]` (validator: non-empty + subset of `{n.name for n in nodes}`), `default_budget: BudgetDefaults = BudgetDefaults()`. Include `agent_definition_hash: str | None = None` (populated later by loader; excluded from `model_dump` via `exclude_defaults` or a field validator).
- All models use `ConfigDict(populate_by_name=True, alias_generator=to_camel)`.

### 3. Create `tests/fixtures/agents/sample-linear.yaml`
- 3 nodes: `analyze_brief`, `draft_plan`, `review_plan` (arbitrary names picked to be human-readable in traces).
- Linear flow: `entry_node: analyze_brief`, transitions `analyze_brief → [draft_plan]`, `draft_plan → [review_plan]`, `review_plan → []`.
- `terminal_nodes: [review_plan]`.
- Minimal `intake_schema` requiring `brief: string`.
- `default_budget: {max_steps: 10}`.

### 4. Create `tests/modules/ai/test_agent_schema.py`
- Happy: load fixture via `yaml.safe_load`, validate, assert field values.
- Missing required field → `ValidationError`.
- `terminal_nodes` empty → rejected.
- `terminal_nodes` referencing unknown node → rejected.
- `model_dump(mode="json")` round-trip yields deterministic ordering.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `pyyaml` dep. |
| `uv.lock` | Modify | Regenerated. |
| `src/app/modules/ai/agents.py` | Create | Pydantic schema only (no loader). |
| `tests/fixtures/agents/sample-linear.yaml` | Create | 3-node linear fixture. |
| `tests/modules/ai/test_agent_schema.py` | Create | Validation tests. |

## Edge Cases & Risks
- YAML's flexible types (e.g., numeric strings) may surprise validators. Lock Pydantic to strict coercion or document what's permissive.
- `terminal_nodes` semantics must be clear: reaching any of them ends the run with `done_node`. Document in the model docstring.

## Acceptance Verification
- [ ] `uv sync` succeeds with `pyyaml` installed.
- [ ] `AgentDefinition.model_validate` accepts the fixture.
- [ ] Missing-field / invalid-terminal tests pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
