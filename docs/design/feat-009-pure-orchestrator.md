# FEAT-009 — Orchestrator as a Pure Orchestrator

**Status:** Accepted · **Date:** 2026-04-26 · **Repositions (does not remove):** the "Policy via tool calling" decision (shared ADR repo: `adrs/ai/policy-via-tool-calling.md`). LLM-as-policy is preserved as an **opt-in mode** for agents declaring `flow.policy: llm`. Deterministic flow resolution becomes the default for agents declaring `flow.policy: deterministic`. Per-call LLM tool-calling **inside individual executors** is unchanged in either mode.

> **Revision note (2026-04-26, mid-implementation):** the first draft framed the LLM-policy path as deprecated drift to be removed. That framing was tightened: LLM-policy is a *legitimate* mode for agents whose branches genuinely require model judgment, not a drift. What this FEAT lands is therefore *repositioning*, not removal — deterministic resolution is the default, LLM-policy is opt-in. The structural guard (T-228) polices the deterministic path only.

## Context

Two principles have been load-bearing in this codebase from the start:

1. *The orchestrator orchestrates.* It does not implement the work that an agent step represents. The work is performed by external executors (a code-writing subprocess, a CI runner, a human operator); the orchestrator picks the next node, dispatches, persists the trace, and waits.
2. *Flow control is declarative (FEAT-006).* The agent YAML's `flow.transitions` block is the source of truth for what node may follow what. Branches that aren't pure 1→1 are still data-resolvable (verdicts, memory predicates) — there is no "ask the model what to do next" step in the lifecycle.

The runtime loop currently violates both, in a way that surfaced when reviewing how a "real run" of `lifecycle-agent@0.1.0` actually works end-to-end:

- **Drift 1 — orchestrator executes work it should be dispatching.** The eight v0.1.0 lifecycle tools (`generate_tasks.py`, `generate_plan.py`, `close_work_item.py`, `assign_task.py`, the git helpers, `atomic_write.py`) write task markdown, write plan files, edit work-item documents, and run git operations *inside the orchestrator process*. There is no executor seam — local-or-remote is not a knob, it is an unmade decision baked into every tool. External integrations (claude-code subprocess, CI hooks, human approvals) cannot plug in without forking the codebase.
- **Drift 2 — orchestrator asks an LLM to pick the next node despite FEAT-006.** Each loop iteration assembles a per-call tool list and calls `core.llm.select_next_tool` to choose one. But the lifecycle flow is almost entirely 1→1, and its only two real branches (`generate_plan` self-loop = "more unplanned tasks?"; `review_implementation` branch = the verdict the executor returned) are *data-resolvable*. The LLM is being consulted on a question the YAML already answered. The "Policy via tool calling" AD predates FEAT-006 and is now stale.

Both drifts share a root cause: the orchestrator owning behavior that belongs elsewhere — executors *below* the loop, the flow declaration *above* it.

## Decision

The runtime loop reduces to four steps:

> **resolve → dispatch → wait → record**

Concretely:

- **Resolve.** A pure-function `FlowResolver` (T-211) computes the next node from `(declaration, current_node, memory_snapshot, last_dispatch_result)`. No LLM call. Multi-target transitions in the YAML carry a `branch:` block whose `rule` is either a registered predicate name or a tiny `result.<field> <op> <literal>` expression. Terminal nodes come from the YAML's `terminalNodes`; an executor can also short-circuit termination by returning `result.terminal=true`.
- **Dispatch.** A registered `Executor` performs the work. The `ExecutorRegistry` (T-213) maps `(agent_ref, node_name) → ExecutorBinding`. Three modes: `local` (in-process callable, used by default and to wrap every existing v0.1.0 tool unchanged — T-214), `remote` (HTTP service that POSTs back to `/hooks/executors/<id>` — T-215, T-216), `human` (operator that POSTs to `/api/v1/runs/<id>/signals` — T-217). All three converge on the same downstream code path.
- **Wait.** `RunSupervisor.await_dispatch(dispatch_id)` (T-219) suspends the loop iteration on a process-local future keyed by the `Dispatch` row's PK (T-212). `deliver_dispatch(dispatch_id, envelope)` is called from the webhook route, the signal route, or the local-executor wrapper.
- **Record.** The `Dispatch` row's state machine (`pending → dispatched → completed | failed | cancelled`) is the single source of truth for the step's outcome. Stop conditions and `MAX_STEPS_PER_RUN` are unchanged; the priority bucket (`cancelled > error > budget_exceeded > policy_terminated > done_node`) is preserved.

Three hard rules fall out of this:

1. **The runtime loop produces no artifacts.** No `open(...)`, no `subprocess`, no `git`, no LLM call from `service.py` / `runtime_helpers.py`. Enforced by a structural test (T-228) sibling to `tests/test_adapters_are_thin.py`.
2. **The runtime loop does not pick the next node via LLM.** Node selection is a pure function of the declaration and run state. LLM access lives *inside* executors that need to generate content (the system prompt moves from `policy.systemPrompts` in YAML to `executors[node].systemPrompt`).
3. **Every agent node has an executor or an explicit exemption.** `validate_executor_coverage()` (T-213) runs at lifespan startup and refuses to boot when a node is unbound — same shape as FEAT-008's `validate_effector_coverage()`.

## Disambiguation: tools vs executors vs effectors

Before FEAT-009 the codebase used "tool" for two different things; FEAT-009 separates them.

- **Tool** *(legacy term, kept only for v0.1.0 backward compatibility)*. The in-process callables under `src/app/modules/ai/tools/lifecycle/`. After FEAT-009 they are wrapped by `LocalExecutor` (T-214) and registered in the executor registry; the term itself is retired from new code.
- **Executor** *(FEAT-009)*. Producer of an artifact in response to a node dispatch. Lives in `src/app/modules/ai/executors/` (local) or behind a configured URL (remote) or in the operator's hands (human). The orchestrator's pluggable surface — adding a new external integration is "register a new executor binding."
- **Effector** *(FEAT-008)*. Side effect that fires *because the engine confirmed a transition*. Lives in `src/app/modules/ai/lifecycle/effectors/`. Symmetric to executors across the engine boundary: executors run *upstream* of node completion, effectors run *downstream* of engine confirmation.

Two registries, deliberately separate. Conflating them re-creates the FEAT-006 rc2 anti-pattern that FEAT-008 just inverted.

## Consequences

**What changes:**

- New module: `src/app/modules/ai/flow_resolver.py` + `flow_predicates.py` (T-211).
- New entity + migration: `Dispatch` (T-212).
- New module: `src/app/modules/ai/executors/` — protocol, registry, coverage validator, local/remote/human adapters (T-213 through T-217).
- New webhook route: `POST /hooks/executors/{executor_id}` (T-216).
- Existing route reframing: `POST /api/v1/runs/{id}/signals` becomes the human-executor return path; wire format unchanged (T-217).
- New trace kind: `executor_call` with `dispatch_id`, `executor_ref`, `mode`, `started_at`, `finished_at`, `outcome` (T-213).
- New CLI: `uv run orchestrator reconcile-dispatches [--since=24h] [--dry-run]` (T-221).
- Runtime loop body in `service.py` rewritten — the `select_next_tool` call, per-call tool-list assembly, and the `terminate` tool injection are deleted from the loop (T-220).
- New agent: `lifecycle-agent@0.2.0` with dispatch verbs and `branch:` rules (T-222, T-223).

**What stays:**

- Engine-as-authority (FEAT-008). Workflow state lives in the flow engine; the reactor still writes status caches and aux rows on engine-confirmed transitions; effectors fire from the reactor exactly as today.
- `lifecycle-agent@0.1.0` runs unchanged (regression bar, T-224). Every v0.1.0 tool is auto-registered as a `LocalExecutor`; tools that today reach into `app.state` for the LLM client receive it as a constructor dependency instead.
- Per-call LLM tool calling *inside* an executor that needs to generate content (`request_task_generation`, `request_plan`, `request_review`). The LLM is now an executor concern, not a runtime-loop concern.
- Engine-absent fallback. When `flow_engine_lifecycle_base_url` is not configured, the pre-FEAT-008 inline path remains for solo-dev mode.
- Stop-condition priority and the `MAX_STEPS_PER_RUN` budget.

**Non-obvious footguns:**

- **DB session ownership for local executors.** The `LocalExecutor` (T-214) takes a `session_factory`, *not* the loop's session. Each dispatch opens its own short-lived session; passing the loop's session into a handler regresses the per-iteration-session convention.
- **`policy.systemPrompts` in v0.1.0 YAML.** Tolerated on v0.1.0 (the loader keeps parsing it; the loop just stops consulting it). Hard-fail on v0.2.0+. Decision documented in T-220 / T-222.
- **HMAC secret for executor webhooks.** Single shared `EXECUTOR_DISPATCH_SECRET` to start; per-executor rotation is a future FEAT and should not pre-bake assumptions (T-215).
- **Restart reconciler conservatism.** Orphan dispatches (rows in `dispatched` with no in-process future) are only marked `cancelled` when the executor cannot confirm the dispatch is still in flight. CLI `--dry-run` lets operators verify before any write (T-221).

## What would flip this decision

We would revisit if:

- A real agent emerges whose node selection genuinely requires an LLM judgment that no data on the dispatch result envelope can capture — e.g. open-ended branching across 5+ targets where the choice depends on unstructured analysis. The current narrow expression syntax + predicate registry would no longer suffice. Even then the right move is probably a *new* node bound to an LLM-backed executor whose result drives the branch — keeping the resolver pure — not LLM-in-the-loop.
- Multi-orchestrator coordination demands cross-process dispatch state (today the supervisor is process-local). At that point the executor seam stays; the supervisor primitives grow to back themselves with a queue. The decision here doesn't change.
- Per-call LLM cost or latency makes the executor-internal LLM call the wrong shape (e.g. wanting to share a single conversation across multiple node dispatches). The seam still holds; the executor changes.

We would *not* revisit because the LLM "feels like" the right place for node selection. That is the cliché this decision pushes back against.
