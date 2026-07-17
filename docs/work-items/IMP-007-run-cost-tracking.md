# Improvement Proposal: IMP-007 — Per-run cost tracking

> **Purpose**: The orchestrator has no visibility into LLM spend per run. The agent-platform already measures token counts and cost per capability job and includes them in the callback payload, but the orchestrator's `AgentPlatformExecutor` drops the `usage` field entirely. In-process `LLMContentExecutor` nodes are also untracked. Without cost data on the `Run`, there is no way to budget, alert, or retrospect on per-feature spend.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-007 |
| **Name** | Per-run cost tracking |
| **Type** | Observability / Operational |
| **Status** | Proposed |
| **Priority** | Medium |
| **Proposed By** | Engineering (2026-07-13) |
| **Date Created** | 2026-07-13 |

---

## 2. Target Area

**Component / Module:** `modules/ai/executors/`, `modules/ai/models.py`, `modules/ai/schemas.py`

**Affected Files / Directories:**
- `src/app/modules/ai/executors/platform.py` — read `usage` from callback; write to dispatch row
- `src/app/modules/ai/executors/llm_content.py` — capture token counts from LLM response; write to dispatch row
- `src/app/modules/ai/models.py` — add `input_tokens`, `output_tokens`, `cost_usd` to `Dispatch`
- `src/app/modules/ai/schemas.py` — surface cost fields on `RunDto` and `DispatchDto`
- `docs/data-model.md` — document new `Dispatch` fields
- `docs/api-spec.md` — document cost fields on run and dispatch responses
- `migrations/` — Alembic revision for new `Dispatch` columns

---

## 3. Current State

### How It Works Today

**Platform path (`AgentPlatformExecutor`):**
The agent-platform's `deliver_callback` sends a `usage` object in the callback payload whenever a capability job completes:

```json
{
  "dispatchId": "...",
  "outcome": "success",
  "result": { ... },
  "usage": {
    "inputTokens": 1240,
    "outputTokens": 387,
    "cacheCreationInputTokens": 0,
    "cacheReadInputTokens": 0,
    "costUsd": null
  }
}
```

`AgentPlatformExecutor._handle_webhook` in `platform.py` reads `dispatchId` and `result` but ignores `usage` entirely. The information is lost after the HTTP request is handled.

**In-process path (`LLMContentExecutor`):**
`LLMContentExecutor` calls the LLM provider and receives token counts in the response, but does not write them anywhere. The `Dispatch` row it creates carries no cost information.

**`Dispatch` model:**
The `Dispatch` table has no columns for token counts or cost. Per-run cost can only be approximated by summing `PolicyCall.input_tokens + PolicyCall.output_tokens` for the LLM-policy runtime, and even that is not aggregated on the `Run`.

### Problems

1. **No per-run cost figure**: There is no single field that answers "how much did this run cost?"
2. **Platform usage is discarded**: The platform already measures cost and sends it; the orchestrator throws it away.
3. **In-process LLM cost is invisible**: `LLMContentExecutor` token counts are never persisted.
4. **No budget enforcement at run level**: Without a running total, there is nothing to compare against a `maxCostUsd` budget.
5. **No retrospective visibility in DevHub**: Operators cannot see how much each work item cost to process.

---

## 4. Desired State

### Target Implementation

Each `Dispatch` row gains three nullable columns: `input_tokens`, `output_tokens`, `cost_usd`. Both execution paths write them on completion:

- **Platform path**: `AgentPlatformExecutor._handle_webhook` reads `payload["usage"]` and updates the `Dispatch` row before calling `handle_executor_webhook`.
- **In-process path**: `LLMContentExecutor` writes token counts from the provider response to the `Dispatch` row after the LLM call completes.

The `Run` schema gains a computed `totalCostUsd` field (summed from all `Dispatch.cost_usd` values for that run) and `totalInputTokens` / `totalOutputTokens` aggregates, surfaced on `GET /api/v1/runs/{id}`.

`cost_usd` is nullable at the column level because:
- The in-process path does not compute a dollar figure today (only token counts). The field stays `NULL` until a pricing helper is wired up.
- The platform's `AnthropicLLMProvider` also leaves `cost_usd=None` today; it can be populated later without a schema change.

### Benefits

1. **Operators can see per-run spend** in DevHub at the run detail level.
2. **No information thrown away**: the data the platform already sends is now persisted.
3. **Foundation for budget alerting**: a future `maxCostUsd` field on `Run` or on the agent YAML can compare against `totalCostUsd`.
4. **Retrospective analysis**: aggregate queries over `dispatches.cost_usd` can rank most-expensive nodes or agent versions.

---

## 5. Constraints and Non-Goals

- **No pricing table in v1**: Dollar amounts come from the platform's `usage.costUsd` field or from a provider-supplied cost field. The orchestrator does not implement its own token-price lookup in this improvement.
- **No run-level budget enforcement**: Alerting and hard-stop on `maxCostUsd` is out of scope for this IMP. The columns are the prerequisite; enforcement is a separate feature.
- **Agentic executor (`ClaudeCodeExecutor`) is excluded**: The Claude CLI's JSON output does not currently report actual spend (only the `--max-budget-usd` ceiling). Tracking is deferred until the CLI exposes it.
- **`PolicyCall` table is unchanged**: Token counts on `PolicyCall` cover the LLM-policy runtime. This IMP covers the deterministic runtime's executor dispatch path, which has no `PolicyCall` rows.

---

## 6. Success Criteria

- [ ] `Dispatch` table has `input_tokens INTEGER`, `output_tokens INTEGER`, `cost_usd NUMERIC(10,6)` (all nullable).
- [ ] `AgentPlatformExecutor` writes token fields to the `Dispatch` row from the callback `usage` object when the platform job completes.
- [ ] `LLMContentExecutor` writes `input_tokens` and `output_tokens` to the `Dispatch` row after the LLM call. `cost_usd` remains `NULL` until a pricing helper is added.
- [ ] `GET /api/v1/runs/{id}` response includes `totalInputTokens`, `totalOutputTokens`, and `totalCostUsd` (summed from `Dispatch` rows; `totalCostUsd` is `null` if no `cost_usd` is set on any dispatch).
- [ ] `GET /api/v1/runs/{id}/steps` dispatch entries include `inputTokens`, `outputTokens`, `costUsd`.
- [ ] Alembic migration for the three new columns.
- [ ] `docs/data-model.md` and `docs/api-spec.md` updated with new fields and a changelog entry.
- [ ] Existing tests pass. At least one unit test asserts that the platform executor writes token counts from a mock callback payload.
