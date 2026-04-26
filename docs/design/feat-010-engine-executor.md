# FEAT-010 — Engine Executor Adapter

**Status:** Accepted · **Date:** 2026-04-26 · **Sequel to:** [`feat-009-pure-orchestrator.md`](./feat-009-pure-orchestrator.md). Reuses every surface introduced by [`feat-008-engine-as-authority.md`](./feat-008-engine-as-authority.md).

## Context

FEAT-009 stood up the executor seam — a runtime loop that resolves the next node, dispatches to a registered `Executor`, awaits a process-local future, and records the outcome. Three concrete adapters landed (`LocalExecutor`, `RemoteExecutor`, `HumanExecutor`) and the `Dispatch` state machine became the single source of truth for a step's outcome.

FEAT-008 made the flow engine the authoritative state owner for work items and tasks. Signal handlers in `lifecycle/service.py` enqueue a `PendingAuxWrite` row and call `FlowEngineLifecycleClient.transition_item(...)` in the same transaction; the reactor (`lifecycle/reactor.py`) processes the engine's `item.transitioned` webhook by materialising the aux row, consuming the correlation context, firing effectors, and computing W2/W5 derivations.

The two surfaces do not yet meet. None of FEAT-009's three executors *advance engine state*. A `flow.policy: deterministic` agent that needs to drive a work-item transition (W1–W6) or task transition (T1–T12) has no executor to dispatch to — only the LLM-policy `lifecycle-agent@0.1.0` path through `FlowEngineLifecycleClient` directly. FEAT-011 (deterministic lifecycle port) cannot start without that gap closed.

## Decision

Add a fourth executor — `EngineExecutor` — that maps a node dispatch to a flow-engine workflow transition. It produces no artefact data; its single output is *engine state advanced*. It plugs into the FEAT-008 pipeline as a *dispatch-shaped* producer of outbox rows; the FEAT-008 reactor settles it.

The runtime loop, the `FlowResolver`, the `Dispatch` state machine, the executor registry, and the coverage validator are all unchanged. The reactor gains one new step (PR 2). No new persistence surface, no new webhook endpoint, no LLM in the runtime loop.

### The executor's wire-shape

`EngineExecutor.dispatch(ctx)`:

1. Open a session via the constructor-injected `session_factory`.
2. Generate a fresh `correlation_id` (UUID).
3. In **one transaction**:
    - Insert a `PendingAuxWrite` row keyed on `correlation_id`.
    - Call `lifecycle_client.transition_item(item_id, to_status, correlation_id, ...)` — encoding the same `correlation_id` into the engine's `triggeredBy` via the existing `orchestrator-corr:<uuid>` convention from FEAT-008.
4. Commit. Return a `dispatched` envelope carrying `correlation_id`, `transition_key`, and (when surfaced) `engine_run_id`.

The supervisor's per-dispatch future is later resolved by the reactor's wake-dispatch step (PR 2 / T-233) when the engine's `item.transitioned` webhook arrives carrying the matching correlation id.

On engine **4xx**: the transaction rolls back, the executor returns a `failed` envelope with `outcome="error"` and `detail` carrying the engine status + body excerpt. No retry — 4xx is a contract violation per the brief.

On engine **5xx / timeout**: the existing bounded retry inside `FlowEngineLifecycleClient` (3 attempts, 500 ms → 4 s backoff with ~15% jitter) fires. After exhaustion, the transaction rolls back and the executor returns `failed`.

## The reactor pipeline (target shape)

After PR 2 lands, `lifecycle/reactor.py::handle_transition` runs this canonical pipeline on every engine `item.transitioned` webhook:

> **`materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`**

The wake-dispatch step is the FEAT-010 addition. The ordering rationale, fixed by AC-5:

- **Wake after effectors.** The resumed runtime iteration must be able to observe any state derived by the effectors (status cache writes, GitHub Check updates) — running effectors first means the `result` envelope the runtime gets back is *post*-effector, not racing with it.
- **Wake before derivations.** W2/W5 derivations (e.g. "all sibling tasks done → work item ready") may emit further engine transitions of their own. The runtime must advance on the *originating* transition's outcome, not on a derived-transition outcome that would belong to a different dispatch row.

In PR 1 (this PR) the reactor is unchanged. The wake step lands in PR 2; PR 1's `EngineExecutor` is therefore dead code from the runtime's perspective until then. This sequencing is deliberate — it keeps every PR reversible until the next merges, and the executor's unit tests can run against a `respx`-stubbed engine without exercising the reactor.

## Load-bearing decisions

| Decision | Choice | Why |
|---|---|---|
| New executor mode literal | Extend `ExecutorMode` to include `"engine"` | Mirrors how `local`/`remote`/`human` are modeled; one taxonomy, not two |
| `correlation_id` placement on `Dispatch` | Carry in `Dispatch.intake` JSONB; outbox is the durable source of truth | No schema migration; correlation is fundamentally an outbox concern, the dispatch row only needs it for in-process wake lookup |
| Reactor pipeline order at the wake point | `materialize aux → consume correlation → fire effectors → wake dispatch → fire derivations` | Effectors-before-wake so the resumed runtime sees effector-derived state; derivations-after-wake so the runtime advances on the originating transition's outcome |
| `FlowEngineLifecycleClient` import in `executors/engine.py` | `TYPE_CHECKING`-only; constructor injection at runtime | Preserves the FEAT-009 import quarantine — `runtime_deterministic` must not transitively pull the engine HTTP client into `sys.modules`. Verified by `tests/test_engine_executor_import_quarantine.py` |
| Engine-absent dev mode | `register_engine_executor` raises `RuntimeError` if `lifecycle_client is None` | Misconfiguration surfaces at boot, naming the offending binding, not at first dispatch. Engine-absent agents must declare a `no_executor("≥10-char reason")` exemption |
| New persistence surface | None — reuse `pending_aux_writes` exclusively | Brief constraint; the engine executor is a *producer of outbox rows*, not a parallel mechanism |

## Consequences

**What changes (across PR 1, PR 2, PR 3):**

- New module `src/app/modules/ai/executors/engine.py` — the `EngineExecutor` class. (PR 1 / T-231.)
- `ExecutorMode` literal extended; `DispatchMode` enum gains `ENGINE`. (PR 1.)
- `DispatchEnvelope` and `ExecutorCallDto` gain optional `correlation_id`, `transition_key`, `engine_run_id` fields, populated only when `mode="engine"`. (PR 1 / T-234.)
- `register_engine_executor` helper in `executors/bootstrap.py` for one-line wiring. (PR 1 / T-232.)
- Reactor pipeline gains `_wake_dispatch` step at the canonical position. (PR 2 / T-233.)
- New CLI `uv run orchestrator reconcile-dispatches [--since=24h] [--dry-run]` — restart-safety reconciler for engine dispatches. (PR 3 / T-235.)

**What stays:**

- The FEAT-008 outbox + reactor + correlation contract is unchanged. The engine executor is a new *producer* of outbox rows; the reactor remains the single consumer.
- The FEAT-009 executor registry, coverage validator, and `Dispatch` state machine are unchanged. The new executor fits the existing seam.
- The `lifecycle-agent@0.1.0` LLM-policy path through `FlowEngineLifecycleClient` directly is unchanged (regression bar — T-238).
- Per-call LLM tool calling *inside* an executor (a content-generating local executor wrapping an Anthropic call) is orthogonal — the engine executor is purely an engine-state advancer.

**Non-obvious footguns:**

- **Webhook arrives before the dispatch row commits.** Covered by the brief §9. The reactor's wake step (PR 2) no-ops on no-match; the runtime advances on its next iteration via the materialised aux row already written by the dispatch's transaction. Verified by T-236's deliberate-ordering-inversion variant.
- **Multi-target transitions on engine-bound nodes.** Out of scope for FEAT-010. `transition_key` and `to_status` are static per binding — branching is the `FlowResolver`'s job, not the executor's. FEAT-011 may revisit if a real consumer needs `result.engine_to_status`-aware branching.
- **Restart safety.** A crash between engine call and webhook leaves a `Dispatch` in `dispatched` with no in-process future. PR 3's `reconcile-dispatches` CLI queries the engine for the entity's current state, materialises the aux row if the transition occurred, marks the dispatch `failed` either way (the run owner is gone). Idempotent — safe to re-run.

## What would flip this decision

- A second consumer of engine round-trips that does not fit the lifecycle workflows (e.g. a non-lifecycle engine integration). At that point the `EngineExecutor` ought to generalise from `FlowEngineLifecycleClient` to an injected protocol — but until that consumer exists, generalising is speculative.
- The flow engine adding a synchronous-transition mode that returns the terminal status in the same response. The wake leg would then become a no-op; the executor would write the aux row inline. This is a flow-engine roadmap question, not an orchestrator one.
- A multi-worker orchestrator (today AD-2 forbids `--workers > 1`). Cross-worker dispatch coordination would force the wake leg through a durable channel; revisit when that constraint flips.

---

**Forward links.** PR 2 (T-233 / T-236 / T-238) extends the reactor with the wake-dispatch step and proves the entire FEAT works end-to-end. PR 3 (T-235 / T-240) closes the operational gap with the reconciler and finalises the docs.

**Cross-link from `CLAUDE.md`.** Patterns: *"Engine-bound nodes register an `EngineExecutor`, never call the engine inline."* Anti-Patterns: *"Don't add a parallel persistence surface for engine round-trips — reuse `pending_aux_writes` and the FEAT-008 reactor."* Both land with the docs sweep in PR 3 (T-240) once the pipeline order has stabilised.
