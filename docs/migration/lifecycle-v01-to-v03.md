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
  - `load_work_item`, `generate_tasks`, `generate_plan`, `review_implementation` → `CompositeLLMEngineExecutor` (LLM call → engine transition in one tx).
  - `assign_task`, `close_work_item` → `EngineExecutor` (engine-only).
  - `request_implementation` → `HumanExecutor` (signal-driven).
  - `correct_implementation` → `LocalExecutor` (writes `Approval` row inline; FEAT-008 contract).
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
5. **Engine transition order.** Trace shows engine POSTs in the order: `work_item.W1` → `task.T2_T4` → `task.T5` → `task.T6_T7` → (resume) → `task.T10` → `work_item.W6`. T1xN fanout is collapsed in PR 3 — see "T1 fanout" below.

## Edge cases (FEAT-011 brief Section 9)

- **Branch expression can't capture an LLM judgment.** Today only `review_implementation` makes a non-trivial judgment, and the YAML branches on `review_passed` (which reads the verdict from memory's `review_history`). If a future stage needs richer judgment, two paths: (a) widen the dispatch result envelope so a predicate can decide on it, or (b) flip that one node behind a `flow.policy: llm` sub-flow. Default is (a).
- **Mid-run upgrade.** Out of scope for FEAT-011. A run started on v0.1.0 must finish on v0.1.0; flipping the agent ref mid-flight is a configuration error. The `lifecycle.v1` namespace is forward-compatible with reads, but the bookkeeping namespace (`__feat009`) is v0.3.0-only.
- **`LifecycleMemory` schema drift.** The v0.3.0 namespace is `lifecycle.v1`. A future schema break ships as `lifecycle.v2`; reader helpers will route on the namespace. Field-name changes within v1 are additive.
- **Engine 404 on transition.** Inherits BUG-002 handling — the lifecycle reactor's stale-cache 404 recovery still runs because the engine client is shared.
- **LLM output schema-validation failure.** `LLMContentExecutor` retries up to `max_retries` (default 1) before returning a failed envelope with `detail="result_schema_validation_failed"`. The runtime then surfaces a failed run (`RunStatus.FAILED`, `stop_reason=ERROR`). Operators see the validation error in the trace.

## Known limitations from PR 3

- **T1xN fanout collapsed.** v0.3.0's `generate_tasks` composite issues a single engine transition (`task.T2_T4`, target status `approved`) regardless of task count. Real T1×N fanout (one engine call per task) is a follow-on FEAT — track via the `generate_tasks` mapping table entry in `docs/design/feat-011-lifecycle-deterministic-port.md`.
- **Post-resume T9 not modeled.** `request_implementation` registers as a plain `HumanExecutor`; the engine T9 transition that would advance the task to `impl_review` after the operator signal is not currently fired by v0.3.0. The next node (`review_implementation`) fires its own T10 transition that picks up the same effect, but operators inspecting the engine timeline will see the gap.
- **`correct_implementation` writes Approval under `decided_by_role='admin'`.** The `ActorRole` enum currently disallows `'agent'`; v0.3.0's self-approve flow mints rejections under admin auspices. A follow-on adds an `agent` role.
- **Prompt drift surface.** System prompts under `src/app/modules/ai/executors/prompts/lifecycle/` are *copies* of `.ai-framework/prompts/` files (decision documented in design doc — copy, not symlink, so v0.1.0 retirement does not silently mutate v0.3.0). The two locations can diverge; treat the executor copy as canonical for v0.3.0.

## Pointer to FEAT-012

FEAT-012 (queued; not yet started) folds rejection-path aux writes into the unified outbox + reactor pipeline that FEAT-008 / FEAT-010 own. Once it lands, `correct_implementation` will enqueue a `PendingAuxWrite` instead of writing the `Approval` row inline, and the reactor will materialize the row when the engine confirms the rejection-bearing transition.

## Changelog

- 2026-04-30 — Initial migration doc shipped with FEAT-011.
