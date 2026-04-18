# Implementation Plan: T-010 — Pydantic DTOs matching api-spec

## Task Reference
- **Task ID:** T-010
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Every control-plane route's validation layer requires correct request/response shapes so OpenAPI is accurate. Addresses AC-6 and AC-2.

## Overview
Implement Pydantic v2 DTOs in `modules/ai/schemas.py` for every shared DTO and request body in `docs/api-spec.md`. All fields use snake_case Python with camelCase JSON aliases. Enums are imported from `enums.py` (shared with models).

## Implementation Steps

### Step 1: Shared response DTOs
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify

Implement with `model_config = ConfigDict(populate_by_name=True)` and `alias_generator=to_camel` from pydantic:

- `RunSummaryDto` — id, agent_ref, status, stop_reason, started_at, ended_at
- `RunDetailDto` — extends RunSummaryDto with agent_definition_hash, intake, trace_uri, step_count, last_step (optional nested)
- `LastStepSummary` — id, step_number, node_name, status (nested in RunDetailDto)
- `StepDto` — all fields from api-spec Shared DTOs
- `PolicyCallDto` — all fields from api-spec
- `WebhookEventDto` — all fields from api-spec
- `AgentDto` — ref, definition_hash, path, intake_schema, available_nodes

### Step 2: Request DTOs
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify

- `CreateRunRequest` — agent_ref (required), intake (dict), budget (optional nested)
- `BudgetConfig` — max_steps (optional int), max_tokens (optional int)
- `CancelRunRequest` — reason (optional str)
- `WebhookEventRequest` — event_type (WebhookEventType enum), engine_run_id, engine_event_id, step_correlation_id (optional uuid), occurred_at, payload (dict)

### Step 3: Webhook acknowledgement DTO
**File:** `src/app/modules/ai/schemas.py`
**Action:** Modify

- `WebhookAckDto` — received (bool), event_id (uuid)

### Step 4: Tests
**File:** `tests/modules/ai/test_schemas.py`
**Action:** Create

- Serialization round-trip: `dto.model_dump(by_alias=True)` produces camelCase.
- `Dto.model_validate(camelCase_dict)` accepts camelCase input.
- Webhook event request rejects unknown `eventType`.
- Enum fields validate against the shared enums.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | All DTOs |
| `tests/modules/ai/test_schemas.py` | Create | Round-trip + validation tests |

## Edge Cases & Risks

- **`alias_generator` + `populate_by_name`.** Must set both so Python code can use snake_case but JSON uses camelCase.
- **`datetime` serialization.** Pydantic v2 serializes `datetime` as ISO 8601 by default — correct for `timestamptz`.
- **`WebhookEventType` validation.** Use the StrEnum directly as the field type; Pydantic v2 validates against enum members.

## Acceptance Verification

- [ ] Each DTO's fields match `docs/api-spec.md` → Shared DTOs exactly.
- [ ] `model_dump(by_alias=True)` produces camelCase JSON.
- [ ] `model_validate(camelCase_dict)` accepts camelCase input.
- [ ] Unknown `eventType` raises `ValidationError`.
- [ ] pyright + ruff clean.
