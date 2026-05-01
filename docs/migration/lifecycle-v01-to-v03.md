# Migration: `lifecycle-agent@0.1.0` → `@0.3.0`

> **Status:** v0.3.0 ships under FEAT-011 (2026-04-30). v0.1.0 remains on disk and operational; deletion is a future FEAT once v0.3.0 has accumulated production hours.

## TL;DR

```bash
# Same command surface, different agent ref.
uv run orchestrator run lifecycle-agent@0.3.0 \
  --intake workItemPath=docs/work-items/FEAT-099.md \
  --follow
```

External contracts are unchanged: same `/api/v1/runs`, same signal endpoint, same engine webhook secret, same trace stream. The pivot is internal — flow control becomes deterministic and the LLM is demoted to an executor-internal concern.

## What changes operationally

- **Nothing externally.** Run intake, signal payloads, webhook signatures, trace JSONL shape — all unchanged.
- **Agent ref differs.** Callers pass `lifecycle-agent@0.3.0` instead of `@0.1.0`. The CLI, the API, and the lifespan binding accept both.
- **Cost profile may shift.** v0.3.0 runs the LLM only inside content-producing executors (`load_work_item`, `generate_tasks`, `generate_plan`, `review_implementation`). v0.1.0 ran the LLM at every flow-control decision. Per-run token cost is typically lower under v0.3.0; verify with a side-by-side trace before flipping defaults.

## What changes internally

- **Policy is deterministic.** `flow.policy: deterministic` in `agents/lifecycle-agent@0.3.0.yaml`. Branch decisions resolve via the predicate registry (`correction_attempts_under_bound`, `review_passed`, `unplanned_tasks_remaining`); no LLM call inside the runtime loop. Verified by `tests/test_runtime_deterministic_is_pure.py`.
- **Executor seam is the production surface.** Every node binds to a concrete executor in `register_lifecycle_v03` (`src/app/modules/ai/executors/bootstrap.py`):
  - `load_work_item` → `LLMContentExecutor` (synthesises the brief; persists into `LifecycleMemory.work_item`; no engine call).
  - `register_work_item` → `EngineCreateExecutor` (BUG-003; calls W1 `create_item`, writes `engineItemId` into `Run.intake`, inserts the local `work_items` row).
  - `generate_tasks` → `LLMContentExecutor` (writes the task list into `LifecycleMemory.tasks`; no engine call).
  - `propose_tasks` → `ProposeTasksExecutor` (BUG-004; T1xN `create_item` against the task workflow + T2+T4 per task, then W2 `open → in_progress` on the work item).
  - `assign_task` → `EngineExecutor` with a `target_id_resolver` that reads the *current task's* engine id from memory (T5: `assigning → planning`).
  - `generate_plan` → `CompositeLLMEngineExecutor` with `target_id_resolver` (LLM plan + T6: `planning → plan_review`; self-loops on `unplanned_tasks_remaining`).
  - `approve_plan` → `EngineExecutor` with `target_id_resolver` (T7: `plan_review → implementing`).
  - `request_implementation` → `HumanExecutor` (signal-driven pause).
  - `submit_implementation` → `SubmitImplementationExecutor` (BUG-004; idempotent T9: `implementing → impl_review` once per task; subsequent visits during the rejection-resubmit loop are no-ops).
  - `review_implementation` → `LLMContentExecutor` (verdict only; the engine T10 fires only on the pass branch via `approve_review`).
  - `approve_review` → `EngineExecutor` with `target_id_resolver` (T10: `impl_review → done`).
  - `correct_implementation` → `LocalExecutor` (writes `Approval` row inline; FEAT-008 contract; no engine call).
  - `close_work_item` → `SequenceEngineExecutor` (W4 `in_progress → ready` then W6 `ready → closed`; default resolver reads work-item id from `Run.intake.engineItemId`).
  - `terminate_correction_budget` → `LocalExecutor` (terminal failure → `RunStatus.FAILED`).
  - `start` → `no_executor` exemption (synthetic resolver-marker node).
- **System prompts moved.** `policy.systemPrompts` is gone from the agent YAML. Each LLM-content executor binding holds its own system prompt loaded from `src/app/modules/ai/executors/prompts/lifecycle/<node>.md`. The prompts were copied from `.ai-framework/prompts/` at PR-3 time — see "Prompt drift" below.
- **Memory shape namespaced.** v0.3.0 persists `LifecycleMemory` under the `lifecycle.v1` namespace inside `RunMemory.data`. Helpers: `read_lifecycle_memory(run_memory)` and `write_lifecycle_memory(model)` in `src/app/modules/ai/tools/lifecycle/memory.py`. Backward-compat: `read_lifecycle_memory` falls back to v0.1.0's top-level shape so a resumed run keeps reading.
- **Correction budget is a predicate + terminal node.** `LIFECYCLE_MAX_CORRECTIONS` (default 2) is plumbed through `register_lifecycle_v03`. The `correction_attempts_under_bound` predicate routes the false branch to `terminate_correction_budget`; the runtime's stop-condition pipeline maps the terminal envelope to `RunStatus.FAILED` with `final_state.reason=correction_budget_exceeded`.

## What to verify before flipping the default agent ref

1. **Smoke test in stub mode.** `LLM_PROVIDER=stub uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=...` — composition integrity (AD-3).
2. **Cost comparison.** Pick one work item; run it through both v0.1.0 and v0.3.0 with the same Anthropic model. Diff token counts from the trace's `policy_call` / `executor_call` entries.
3. **Side-by-side trace inspection.** v0.3.0's trace shows `mode=local` for LLM-content nodes, `mode=engine` for engine-bound nodes (composite or pure), `mode=human` for the pause node, and `mode=local` for `correct_implementation` / `terminate_correction_budget`. The flow has a synthetic `start` step; subsequent steps follow the YAML transitions.
4. **Aux-row materialization.** Confirm `task_assignments`, `task_plans`, `task_implementations`, `approvals` rows show up under v0.3.0 the same way they do under v0.1.0. Rejection paths (`correct_implementation`) write `Approval(stage='impl', decision='reject')` inline — no engine transition. Verified by `tests/integration/test_lifecycle_v03_rejection.py`.
5. **Engine transition order.** Trace shows engine calls in the order: `register_work_item` → W1 `create_item` (synchronous, no webhook) → `propose_tasks` (T1×N `create_item` per LLM-produced task → T2 `proposed→approved` → T4 `approved→assigning` per task → W2 `open→in_progress`; all synchronous) → T5 `assigning→planning` → T6 `planning→plan_review` → T7 `plan_review→implementing` → (operator signal) → T9 `implementing→impl_review` (skipped on rejection re-loops) → T10 `impl_review→done` (pass branch only) → W4 `in_progress→ready` → W6 `ready→closed`.

## Edge cases (FEAT-011 brief Section 9)

- **Branch expression can't capture an LLM judgment.** Today only `review_implementation` makes a non-trivial judgment, and the YAML branches on `review_passed` (which reads the verdict from memory's `review_history`). If a future stage needs richer judgment, two paths: (a) widen the dispatch result envelope so a predicate can decide on it, or (b) flip that one node behind a `flow.policy: llm` sub-flow. Default is (a).
- **Mid-run upgrade.** Out of scope for FEAT-011. A run started on v0.1.0 must finish on v0.1.0; flipping the agent ref mid-flight is a configuration error. The `lifecycle.v1` namespace is forward-compatible with reads, but the bookkeeping namespace (`__feat009`) is v0.3.0-only.
- **`LifecycleMemory` schema drift.** The v0.3.0 namespace is `lifecycle.v1`. A future schema break ships as `lifecycle.v2`; reader helpers will route on the namespace. Field-name changes within v1 are additive.
- **Engine 404 on transition.** Inherits BUG-002 handling — the lifecycle reactor's stale-cache 404 recovery still runs because the engine client is shared.
- **LLM output schema-validation failure.** `LLMContentExecutor` retries up to `max_retries` (default 1) before returning a failed envelope with `detail="result_schema_validation_failed"`. The runtime then surfaces a failed run (`RunStatus.FAILED`, `stop_reason=ERROR`). Operators see the validation error in the trace.

## Known limitations from PR 3

- ~~**T1xN fanout collapsed.**~~ Closed by BUG-004: `propose_tasks` now fires one `create_item` + T2 + T4 per LLM-produced task.
- ~~**Post-resume T9 not modeled.**~~ Closed by BUG-004: `submit_implementation` fires T9 idempotently.
- **`correct_implementation` writes Approval under `decided_by_role='admin'`.** The `ActorRole` enum currently disallows `'agent'`; v0.3.0's self-approve flow mints rejections under admin auspices. A follow-on adds an `agent` role.
- **Aux-row materialisation for `propose_tasks` not yet via outbox.** The T2+T4 self-approvals are fired inline; the corresponding `Approval(stage=proposed)` rows are not persisted (FEAT-008's outbox-then-reactor materialisation is preserved for the rejection path only). FEAT-012 is the umbrella for folding all aux writes through the outbox; this expands its surface.
- **Multi-task implementation/review loop not modelled.** The current YAML works for the single-task happy path that v0.3.0's e2e tests exercise. Multi-task scenarios (where each task needs its own assign → plan → implement → review cycle) need a back-edge from `approve_review` to `assign_task` plus a `more_tasks_remaining` predicate. Tracked separately.
- **Prompt drift surface.** System prompts under `src/app/modules/ai/executors/prompts/lifecycle/` are *copies* of `.ai-framework/prompts/` files (decision documented in design doc — copy, not symlink, so v0.1.0 retirement does not silently mutate v0.3.0). The two locations can diverge; treat the executor copy as canonical for v0.3.0.

## Pointer to FEAT-012

FEAT-012 (queued; not yet started) folds rejection-path aux writes into the unified outbox + reactor pipeline that FEAT-008 / FEAT-010 own. Once it lands, `correct_implementation` will enqueue a `PendingAuxWrite` instead of writing the `Approval` row inline, and the reactor will materialize the row when the engine confirms the rejection-bearing transition.

## Changelog

- 2026-04-30 — Initial migration doc shipped with FEAT-011.
- 2026-05-01 — BUG-003: `load_work_item` Composite split into `load_work_item` (LLM-content only) + `register_work_item` (new `EngineCreateExecutor`). Engine W1 is `create_item`, not `transition_item` — the Composite's "transition existing item" contract no longer leaks into the creation step. The agent YAML grows one node and one transition; no operational change for callers.
- 2026-05-01 — BUG-004: full task-lifecycle rewire. New executors (`SequenceEngineExecutor`, `ProposeTasksExecutor`, `SubmitImplementationExecutor`); `target_id_resolver` extension on `EngineExecutor` and `CompositeLLMEngineExecutor`; new YAML nodes (`propose_tasks`, `approve_plan`, `submit_implementation`, `approve_review`); `LifecycleTask.engine_item_id` + `submitted` fields. Engine state-machine fidelity restored — every task-lifecycle transition now addresses a real engine task entity with a valid `to_status`. Closes the "T1xN fanout collapsed" and "post-resume T9 not modeled" simplifications from the prior changelog entry.
- 2026-05-01 — BUG-005: registered engine webhook subscriptions (`ensure_engine_subscriptions`); fixed `load_work_item` to read brief contents via a new `prompt_context_loader` extension on `LLMContentExecutor`. Followup: GET-then-POST dedupe so re-boots don't accumulate duplicate subscriptions; `PUBLIC_BASE_URL` must point at the orchestrator's container DNS name, not `localhost`.
- 2026-05-01 — BUG-006: added a diagnostic WARNING log on lifecycle webhook signature verification failure that surfaces the received header value (truncated) so signature-scheme mismatches between the engine and orchestrator can be diagnosed from logs.
- 2026-05-01 — BUG-007: reordered `ProposeTasksExecutor` so the local `tasks` row is committed before T2/T4 fire, fixing the race where the engine's webhook arrived before the row existed and the reactor's lookup-by-`engine_item_id` skipped the event.
- 2026-05-01 — BUG-008: rewrote `generate_tasks.md` and `generate_plan.md` system prompts (200–300 lines each) to short schema-aligned executor-facing prompts. The originals were the unmodified ai-framework authoring templates asking for Markdown output, conflicting with the JSON tool schemas the executor wraps them around. `load_work_item.md` and `review_implementation.md` were already correctly shaped and left untouched.
- 2026-05-01 — Correlation-id contract switched from comment-encoding to a structured request field. The orchestrator previously prefixed `transition_item`'s `comment` with `orchestrator-corr:<uuid>` on the assumption the engine echoed the comment back via `triggered_by`. It does not — `triggered_by` is the engine's actor id; the comment-encoding hack never worked. The orchestrator now sends `correlationId` as a top-level structured field on the request body; the engine echoes it back as `correlationId` on the resulting webhook (in `data.correlationId` or top-level — the reactor accepts either via `webhook_correlation_id(...)`). `extract_correlation_id` was deleted. Affected paths: `transition_item` (request shape), `LifecycleWebhookEvent` / `LifecycleWebhookData` (now carry `correlation_id: UUID | None`), reactor's `_consume_correlation_by_id` (renamed; takes a UUID directly).
