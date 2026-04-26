# Feature Brief: FEAT-009 — Orchestrator as a pure orchestrator (executors are pluggable, not embedded)

> **Purpose**: Re-anchor the codebase on its founding principle — the orchestrator *orchestrates*: it follows the declared flow, dispatches work to registered executors, and records what happened. It does **not** execute work, and it does **not** decide what comes next via an LLM when the flow declaration already says. Every artifact-producing step must be a *dispatch* to a registered executor (external by design, local only as a convenience), uniformly contracted, with results returned via webhook. Existing in-process tools become the first registered "local executor" so no current capability is lost.
>
> **Relationship to FEAT-006.** FEAT-006 made the lifecycle flow deterministic by declaring transitions in the agent YAML. This FEAT carries that decision into the runtime loop: node selection is a pure function of `(current_node, memory, last_dispatch_result)` against the YAML, not an LLM call. The LLM keeps its job — generating *content* when an executor needs it — but it stops being the loop's policy. (Today's `core/llm.py`-as-policy shape predates FEAT-006 and is now an architectural inconsistency this FEAT closes.)
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-009 |
| **Name** | Orchestrator as a pure orchestrator (executors are pluggable, not embedded) |
| **Target Version** | v0.4.0 |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | Project owner (architectural drift surfaced when reviewing how a "real run" of `lifecycle-agent@0.1.0` actually works end-to-end) |
| **Date Created** | 2026-04-26 |

---

## 2. User Story

**As an** orchestrator operator who wants to wire the loop to *my* executors (claude-code subprocess, GitHub Actions runner, human reviewer, custom CI step), **I want** every node in an agent flow to be a uniform dispatch-and-wait against a registered executor — local or external — **so that** I can plug the orchestrator into any execution surface without forking the codebase, and the orchestrator stays focused on policy decisions, dispatch, and trace.

---

## 3. Goal

The orchestrator's runtime loop reduces to four steps: **resolve next node from the flow declaration → dispatch to the registered executor → wait for the result → record + advance**. No LLM call participates in step 1. Every artifact-producing step in any agent runs through a single executor contract — `dispatch(node, intake, context) → result via webhook` — with local handlers and remote endpoints both registered through the same registry. The orchestrator process itself produces no artifacts and makes no flow decisions that aren't already encoded in the agent YAML or derivable from data.

---

## 4. Feature Scope

### 4.1 Included

- A **deterministic flow resolver** that computes the next node from `(agent_flow_declaration, current_node, memory, last_dispatch_result)` as a pure function. Branches in the flow YAML (e.g. `review_implementation → [corrections, close_work_item]`) are resolved by data — either a memory predicate (`unplanned_tasks_remaining()` for the `generate_plan` self-loop) or a field on the last dispatch result (`result.verdict == "fail"` for the review branch). The resolver is the only thing that picks the next node; it never calls an LLM.
- The runtime loop is reduced to **resolve → dispatch → await → record → advance**. The LLM-as-policy entry point (`core/llm.py` `select_next_tool` and the per-call tool-list assembly in `tools/__init__.py`) is removed from the loop. LLM access is preserved but moved *inside* the executors that need it (e.g. `request_task_generation`, `request_plan`, `request_review`).
- A new `Executor` abstraction with a single uniform contract: receive a dispatch, perform the work, report completion via webhook (or a synchronous in-process equivalent that fakes the webhook for local executors). Executors that need LLM access take a `core/llm` client as a constructor dependency — the LLM is an executor-internal tool, not a runtime-loop primitive.
- An **executor registry** mounted at lifespan, mirroring the FEAT-008 effector registry pattern: every node referenced by a registered agent flow must resolve to either (a) a registered executor, or (b) an explicit `no_executor("policy-only")` exemption (e.g. `terminate`).
- A **local executor adapter** that wraps the existing `modules/ai/tools/lifecycle/*` handlers (`generate_tasks`, `generate_plan`, `close_work_item`, `assign_task`, etc.) and exposes them through the same dispatch contract. No existing tool implementation is deleted; each one is reframed as a *local executor* that happens to run in-process.
- A **remote executor adapter** that POSTs the dispatch to a configured HTTP endpoint and waits for the standard webhook reply (`POST /hooks/executors/<executor-id>` with the result envelope).
- A unified `wait_for_executor` runtime path. The current bespoke `wait_for_implementation` pause becomes a special case of "every step waits for its executor"; the runtime no longer distinguishes "pausing tools" from "synchronous tools" — every dispatch is logically asynchronous.
- A **`lifecycle-agent@0.2.0`** that re-declares every node as a *dispatch verb* (`request_task_generation`, `request_plan`, `request_implementation`, `request_review`, `request_closure`) and binds each to either the local executor (default, preserves today's behavior) or a remote URL (configurable per node). The YAML's `policy.systemPrompts` block (today: per-node prompts for the LLM-as-policy call) is renamed `executors[node].systemPrompt` and attached to the executor that internally calls an LLM — making explicit that the prompt drives content generation, not node selection.
- Trace coverage: every dispatch emits a `trace_kind="executor_call"` entry under `<trace_dir>/executors/<run_id>.jsonl` with `executor_ref`, `dispatch_id`, `mode=local|remote`, `started_at`, `finished_at`, and `outcome`.
- A `validate_executor_coverage()` pre-flight that runs alongside `validate_effector_coverage()` at lifespan startup and refuses to boot if any agent node is unbound.
- Documentation realignment: `ARCHITECTURE.md` (AD-1 explicit; "tools" terminology disambiguated from "executors"), `CLAUDE.md` (patterns + anti-patterns), `data-model.md` (new `executor_registrations` and `executor_dispatches` if they materialize as DB-backed; otherwise in-memory-only registry like effectors).

### 4.2 Excluded

- **LLM-driven flow control.** No node-selection decision is delegated to an LLM. If a flow truly needs a judgment that data can't resolve, that judgment is the *output* of an executor (e.g. `request_review` returns `verdict: pass|fail`) and the deterministic resolver branches on the executor's result. The loop never asks "given the allowed nodes, pick one."
- **Deleting `lifecycle-agent@0.1.0` or any of its in-process tools.** v0.1.0 keeps working unchanged; v0.2.0 is additive. Deprecation can come in a later FEAT once v0.2.0 is proven.
- **A second non-lifecycle agent.** This FEAT only re-grounds the existing agent and the executor seam; new agents come later.
- **Auth / signing for remote executors beyond what already exists.** Remote dispatch reuses the existing HMAC pattern and JWT helpers — no new auth framework.
- **A queue / broker.** Dispatch stays direct HTTP (or in-process for local). A broker is a future FEAT if needed.
- **Multi-tenant executor isolation.** One registry per orchestrator process, same as effectors.
- **Replacing the engine.** The flow engine remains the workflow authority; the executor seam is *downstream* of node selection — engine decides "this node fired", orchestrator dispatches the executor for that node.

---

## 5. Acceptance Criteria

- **AC-1**: `validate_executor_coverage()` runs at lifespan startup; the orchestrator refuses to boot when any node in any registered agent flow has neither an executor registration nor a `no_executor` exemption. Failure message names the offending node + agent.
- **AC-2**: Every existing `lifecycle-agent@0.1.0` end-to-end test continues to pass without modification — the local executor adapter preserves current in-process behavior bit-for-bit.
- **AC-3**: A new `lifecycle-agent@0.2.0` runs cold-start to closure on a throwaway work item using only local executors, and the trace shows one `executor_call` entry per node (no in-process `tool_call` shortcuts).
- **AC-4**: The same `lifecycle-agent@0.2.0` runs cold-start to closure with at least one node (e.g. `request_implementation`) bound to a *remote* executor stub served over HTTP — the orchestrator dispatches, waits, receives the webhook, and advances. Verified via integration test with `respx`.
- **AC-5**: The runtime loop has no code path that produces an artifact (writes a file, edits markdown, runs git, calls an LLM for content generation) **and no code path that calls an LLM for node selection**. Every such operation lives in an executor module outside `modules/ai/service.py` and `modules/ai/runtime_helpers.py`. Enforced by a structural test similar to `tests/test_adapters_are_thin.py`.
- **AC-8**: The `flow_resolver` module is a pure function with no I/O — given `(agent_flow_declaration, current_node, memory_snapshot, last_dispatch_result)` it returns either the next node name or a terminal sentinel. Verified by a unit test that exhaustively walks every transition in `lifecycle-agent@0.2.0` (including both branches of `review_implementation` and the `generate_plan` self-loop) without instantiating any LLM client.
- **AC-6**: `wait_for_implementation`'s bespoke pause logic is removed; every dispatch is asynchronous from the runtime's perspective and uses the same `await supervisor.await_dispatch(dispatch_id)` primitive. Manual signal injection (`POST /api/v1/runs/{id}/signals`) is preserved as a *human executor* registration so existing operator flows keep working.
- **AC-7**: `ARCHITECTURE.md` re-states AD-1 explicitly with a "tools vs executors vs effectors" disambiguation block; `CLAUDE.md` adds a Pattern entry "Executors are the only producers" and an Anti-Pattern "Don't write artifacts from inside the runtime loop"; `data-model.md` and `api-spec.md` updated with `/hooks/executors/<id>` and any new entities.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|-----------------|--------------------|
| `AgentFlowDeclaration` (existing concept, formalized) | The YAML's `flow.transitions` block; the *only* source of truth for which nodes can follow which | Transitions with multiple targets must declare a `branch` rule (memory predicate or dispatch-result field name) so the resolver can pick deterministically without an LLM |
| `FlowResolver` (new — pure function, no DB row) | Computes `next_node` from `(declaration, current_node, memory, last_dispatch_result)` | Pure, deterministic, no I/O, no LLM client; raises if a multi-target transition has no branch rule defined |
| `ExecutorRegistration` (new — in-memory at lifespan, mirrors effector pattern) | Maps `(agent_ref, node_name)` → executor handler (local callable or remote URL + auth) | Coverage validated at boot; no node may have two registrations; `no_executor` exemption requires a justification string |
| `Dispatch` (new — DB-backed) | Records every executor dispatch issued by the runtime | Append-only after terminal state; carries `dispatch_id` (correlation key), `executor_ref`, `mode`, `intake`, `result`, `started_at`, `finished_at`, `outcome` |
| `Step` (existing) | One step now owns at most one `Dispatch`; `Step.status` derives from the dispatch outcome | A step cannot transition to `completed` without a terminal dispatch; matches the current `engine_run_id` correlation pattern |
| `RunSignal` (existing) | Human-executor results arrive through this path; the human-executor registration treats a `RunSignal` arrival as the dispatch result | Signal name maps 1:1 to the dispatch the human is fulfilling |

**New entities required:** `Dispatch` (and its row-level state machine: `pending → dispatched → completed | failed | cancelled`). `ExecutorRegistration` is in-memory only, parallel to `effector_registry`.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/hooks/executors/{executor_id}` | POST | New | HMAC-signed webhook for remote executors to report dispatch results. Persist-first like `/hooks/engine/*` |
| `/api/v1/runs/{id}/signals` | POST | Existing | Reframed as the "human executor" return path; payload shape unchanged |
| `/api/v1/runs/{id}/dispatches` | GET | New (optional, debug) | List dispatches for a run with their state — useful for trace UI later, can be deferred to a follow-on |

**New endpoints required:** `POST /hooks/executors/{executor_id}` is the only mandatory one for v0.4.0.

---

## 8. UI Impact

N/A — orchestrator is headless in v1.

**New screens required:** None.

---

## 9. Edge Cases

- **Local executor raises mid-dispatch.** The local executor adapter catches the exception, synthesizes a `failed` dispatch result, and feeds it back through the same webhook-equivalent path so the runtime sees a uniform failure shape regardless of mode.
- **Remote executor never responds.** Per-dispatch timeout (config: `EXECUTOR_DISPATCH_TIMEOUT_SECONDS`, default 600) terminates the dispatch as `failed` with `reason=timeout`; reuse the same stop-condition priority (`error`).
- **Remote executor responds twice.** The `dispatch_id` unique constraint dedupes; second arrival returns 200 with `meta.alreadyReceived=true`, matching the existing webhook idempotency pattern.
- **Node bound to remote executor while operator is offline.** Configuration check at lifespan: if a remote executor URL is unreachable on a HEAD/health probe, log a warning but boot anyway — the dispatch will fail at runtime with a clear error rather than masking the misconfiguration.
- **Mixed-mode agent (some nodes local, some remote).** Supported by design — registration is per `(agent, node)`; this is the migration path for `lifecycle-agent@0.2.0`.
- **`lifecycle-agent@0.1.0` still runs after v0.4.0 lands.** v0.1.0's existing tools are auto-registered as local executors during lifespan setup; the agent runs unchanged. Removing v0.1.0 is a future, separate decision.
- **Correlation across crashes.** `Dispatch` rows persist; on orchestrator restart, the reconciler queries the executor (where supported) or marks the dispatch `cancelled` with `reason=orchestrator_restart`, mirroring the FEAT-008 outbox/reconcile pattern.

---

## 10. Constraints

- **No breaking change to v0.1.0.** Existing tests, fixtures, smoke flows continue to work.
- **No new infrastructure.** No queue, no broker, no message bus. HTTP + in-process only.
- **Trace must remain replayable.** Adding a new trace kind (`executor_call`) is fine; rewriting historical entries is not.
- **Same security posture as engine webhooks.** Executor webhooks use HMAC, persist-on-arrival, signature-failure → 401 + `signature_ok=false` row.
- **Single-worker still in force.** `RunSupervisor` stays process-local; cross-worker coordination is out of scope.

---

## 11. Motivation and Priority Justification

**Motivation:** A review of `lifecycle-agent@0.1.0` revealed two related drifts from the founding philosophy that *the principal objective of the orchestrator is to orchestrate*:

1. **The orchestrator executes work it should be dispatching.** The agent's eight stages run as in-process tools that write tasks markdown, write plan files, edit work-item documents, and run git operations directly inside the orchestrator process. That violates AD-1 ("orchestrator is policy + dispatch, not execution") and forecloses external-executor plug-in — the very surface the project was built to provide.
2. **The orchestrator asks an LLM to pick the next node, even though FEAT-006 made the flow deterministic.** The current loop calls `select_next_tool` against a per-call tool list every iteration. But the agent YAML already declares the transitions, and the only branches in the lifecycle flow are data-resolvable (`generate_plan` self-loop = "more unplanned tasks?"; `review_implementation` branch = the verdict the executor returned). FEAT-006 took flow control out of the model; the runtime loop never caught up. The LLM-as-policy AD predates FEAT-006 and is now stale.

Both drifts share a root cause: the orchestrator owning behavior that belongs elsewhere (executors below the loop, the flow declaration above it). The current shape works as a demo but cannot accept external executors and confuses "model" with "controller." This drift will compound with every new agent built on top of it.

**Impact if delayed:** Every new feature added to `lifecycle-agent@0.1.0` deepens the assumption that artifact production lives inside the orchestrator. The longer this stays, the harder the eventual realignment becomes — and any external integration (claude-code subprocess, CI job, human-in-the-loop reviewer) has to either fork the codebase or layer on top of the in-process tool surface.

**Dependencies on this feature:** Any future agent that dispatches to an external executor (claude-code subprocess for actual implementation, GitHub Actions for CI, Slack for human approval) is blocked on this seam. FEAT-007 (GitHub merge gating) already pushed against this boundary by introducing per-request effector dispatch as a workaround — FEAT-009 makes that pattern first-class.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | Operator wiring the orchestrator into a real execution surface (see `docs/personas/primary-user.md` if it exists; otherwise this brief introduces the role and the persona doc should be added during task generation) |
| **Stakeholder Scope Item** | Founding philosophy: orchestrator orchestrates; tools and executors plug in. To be cross-referenced against `docs/stakeholder-definition.md` during task generation |
| **Success Metric** | "An external executor can be wired into a lifecycle agent without forking the orchestrator" — concrete, demonstrable via AC-4 |
| **Related Work Items** | FEAT-005 (introduced `lifecycle-agent@0.1.0` and the in-process tool surface this corrects); FEAT-006 (made the lifecycle flow deterministic — this FEAT propagates that decision into the runtime loop); FEAT-008 (engine-as-authority pattern this mirrors for executors); FEAT-007 (per-request effector dispatch — the closest existing pattern); ARCHITECTURE.md AD-1 (the decision this re-anchors) |

---

## 13. Usage Notes for AI Task Generation

When generating tasks from this Feature Brief:

1. **Preserve v0.1.0.** The first investigation task must inventory every callable currently registered as a "tool" under `modules/ai/tools/lifecycle/` and confirm a non-destructive wrapping path. Deletion of any v0.1.0 tool is out of scope.
2. **Mirror FEAT-008's shape.** The executor registry, coverage validator, and dispatch lifecycle should follow the FEAT-008 effector patterns line-for-line where they apply — same exemption mechanism, same lifespan validation, same webhook discipline.
3. **Order tasks by seam, not by feature surface.** Recommended sequence: (1) `FlowResolver` pure-function module + exhaustive transition test (AC-8), (2) `Dispatch` model + migration, (3) `ExecutorRegistry` + coverage validator, (4) local executor adapter wrapping existing tools (LLM client now an executor dependency, not a runtime dependency), (5) `wait_for_executor` runtime primitive replacing the bespoke pause, (6) remote executor adapter + `/hooks/executors/{id}`, (7) `lifecycle-agent@0.2.0` YAML with `branch` rules on multi-target transitions and `executors[node].systemPrompt` reframing, (8) remove the LLM-as-policy code path from the runtime loop, (9) docs + structural test (AC-5).
4. **Acceptance-criteria coverage.** Each of AC-1..AC-7 should map to at least one task's acceptance criteria. AC-2 (v0.1.0 unchanged) is a regression bar that every task must respect.
5. **Edge cases as test tasks.** Section 9 edge cases each become a named integration test in the relevant phase (failure, timeout, double-delivery, cold restart).
6. **Doc updates land in the same PR as the code.** ARCHITECTURE.md, CLAUDE.md, data-model.md, api-spec.md updates per the maintenance discipline table.
7. **Traceability tag.** Every generated task title carries `(FEAT-009)`.
