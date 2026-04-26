# FEAT-009 — Orchestrator as a pure orchestrator (executors are pluggable, not embedded)

> **Source:** `docs/work-items/FEAT-009-orchestrator-as-pure-orchestrator.md`
> **Status:** Not Started
> **Target version:** v0.4.0

FEAT-009 closes two related drifts: (1) the orchestrator executes work it should be dispatching, and (2) the orchestrator asks an LLM to pick the next node even though FEAT-006 made the flow deterministic. The runtime loop reduces to **resolve → dispatch → wait → record**, with a flow-declaration-driven resolver replacing the LLM-as-policy path and a uniform executor seam absorbing every artifact-producing step. v0.1.0 keeps working unchanged via auto-registered local executors; v0.2.0 is the new shape and proves remote dispatch end-to-end.

---

## Foundation

### T-210: ADR — pure-orchestrator decision, supersede LLM-as-policy AD

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Write `docs/design/feat-009-pure-orchestrator.md` as the authoritative architectural decision behind FEAT-009. State the two drifts being corrected (executes-instead-of-dispatching and LLM-as-policy-despite-FEAT-006), the new four-step loop, and the disambiguation between *tools*, *executors*, and *effectors*. Add a "Superseded by FEAT-009" banner to whichever existing ADR carries the LLM-as-policy decision.

**Rationale:**
AC-7. The "Policy via tool calling" line in CLAUDE.md and any AD that established LLM-as-policy must be unambiguously deprecated before code changes land — otherwise reviewers will defend the old shape.

**Acceptance Criteria:**
- [ ] New ADR explicitly names the symmetric principle: orchestrator does not execute, *and* does not decide what FEAT-006's flow declaration already decides.
- [ ] Lists what changes (loop becomes 4 steps, executor seam, FlowResolver) and what stays (engine authority from FEAT-008, effector registry from FEAT-008).
- [ ] Disambiguation block: tools (legacy term), executors (FEAT-009 producers, registered per `(agent, node)`), effectors (FEAT-008 consequences of engine-confirmed transitions).
- [ ] Old LLM-as-policy AD (track it down in `docs/design/` or in the shared ADR repo if applicable) carries a forward-link banner.
- [ ] Cross-linked from `CLAUDE.md`'s architecture section.

**Files to Modify/Create:**
- `docs/design/feat-009-pure-orchestrator.md` — new.
- The current LLM-as-policy ADR file — banner.
- `CLAUDE.md` — link in the architecture section.

---

### T-211: FlowResolver — pure-function next-node selection

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Introduce `src/app/modules/ai/flow_resolver.py`: a pure function `resolve_next(declaration, current_node, memory_snapshot, last_dispatch_result) -> NextNode | TerminalSentinel`. For 1→1 transitions, return the only target. For multi-target transitions, evaluate the `branch` rule declared on the YAML edge — either a memory-predicate name (`unplanned_tasks_remaining`) or a dispatch-result field expression (`result.verdict == "fail"`). No I/O, no DB, no LLM client. Raises `FlowDeclarationError` if a multi-target transition has no `branch` rule.

**Rationale:**
AC-8 and the second drift this FEAT closes. Carrying FEAT-006's deterministic-flow promise into the runtime loop requires a pure resolver that stands on its own and can be exhaustively tested.

**Acceptance Criteria:**
- [ ] `resolve_next` signature documented; no asyncio, no `await`.
- [ ] Branch-rule registry (small dict) maps predicate names → callables that take `(memory, last_dispatch_result)` and return `bool`. Predicates needed for v0.2.0: `unplanned_tasks_remaining`, `correction_attempts_under_bound`.
- [ ] Dispatch-result field expressions parsed safely (no `eval`); supported shape is `result.<field> <op> <literal>`, op in `==, !=`.
- [ ] `FlowDeclarationError` raised eagerly when the resolver is asked about an unmappable transition.
- [ ] Unit test exhaustively walks every transition in `lifecycle-agent@0.2.0` (delivered in T-222) — for now, walk a fixture flow that mirrors v0.2.0's branches; the v0.2.0 fixture switches to the real YAML once T-222 lands.
- [ ] Test imports do not pull in `core/llm` (verified by an explicit `assert "anthropic" not in sys.modules` after import — same import-quarantine pattern as `tests/test_adapters_are_thin.py`).

**Files to Modify/Create:**
- `src/app/modules/ai/flow_resolver.py` — new.
- `src/app/modules/ai/flow_predicates.py` — new (registry + the v0.2.0 predicates).
- `tests/modules/ai/test_flow_resolver.py` — new.

**Technical Notes:**
The resolver is the load-bearing artifact for the FEAT-006 alignment. Keep it ruthlessly small. Anything that needs DB access belongs in the *predicates* (which can be passed snapshots prepared by the loop), not in the resolver.

---

### T-212: `Dispatch` model + Alembic migration + state machine

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Add the `Dispatch` SQLAlchemy model and the `dispatches` table. State machine `pending → dispatched → completed | failed | cancelled` enforced via a CHECK constraint and the model's transition methods. `dispatch_id` is the correlation key (`uuid` PK), with `step_id`, `executor_ref`, `mode`, `intake` (JSONB), `result` (JSONB nullable), `outcome` (text nullable), `started_at`, `finished_at`. Append-only after terminal state.

**Rationale:**
AC-3, AC-4, AC-6. Every executor seam (local, remote, human) writes here, and the row is the persistence backbone for restart-recovery (T-221).

**Acceptance Criteria:**
- [ ] Alembic migration — new revision file under `src/app/migrations/versions/`. Refuses to downgrade if any non-terminal `dispatches` rows exist (mirrors the FEAT-008 destructive-pre-flight pattern).
- [ ] `Dispatch` model in `src/app/modules/ai/models.py` with composite uniqueness on `(step_id)` (one dispatch per step) and unique on `dispatch_id`.
- [ ] Pydantic DTO for trace serialization in `src/app/modules/ai/schemas.py`.
- [ ] State-machine transitions (`mark_dispatched`, `mark_completed`, `mark_failed`, `mark_cancelled`) raise `IllegalDispatchTransition` on invalid moves; covered by unit tests.
- [ ] `data-model.md` updated with the new entity (changelog entry).

**Files to Modify/Create:**
- `src/app/migrations/versions/<ts>_add_dispatches_feat_009.py` — new.
- `src/app/modules/ai/models.py` — `Dispatch` model.
- `src/app/modules/ai/schemas.py` — `DispatchEnvelope`.
- `tests/modules/ai/test_dispatch_model.py` — new.
- `docs/data-model.md` — entity + changelog entry.

---

## Backend — executor seam

### T-213: `Executor` protocol + `ExecutorRegistry` + coverage validator + trace kind

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Introduce `src/app/modules/ai/executors/`: `Executor` Protocol, `ExecutorRegistry` with `register((agent_ref, node_name), binding)` / `resolve(...)` / iteration helpers, `ExecutorBinding` (mode + handler/url + timeout + optional `system_prompt`), `no_executor("reason")` exemption, and `validate_executor_coverage(registry, agents)` lifespan helper. Add `trace_kind="executor_call"` to the trace writer. No concrete executors yet.

**Rationale:**
AC-1. Mirrors FEAT-008's `EffectorRegistry` pattern line-for-line so reviewers can pattern-match. Coverage validation must live with the registry so a misconfigured agent fails the boot, not the third hour of a run.

**Acceptance Criteria:**
- [ ] `executors/__init__.py`, `registry.py`, `binding.py`, `coverage.py` modules exist.
- [ ] `Executor` Protocol: `name: ClassVar[str]`, `mode: Literal["local","remote","human"]`, `async def dispatch(intake, ctx) -> DispatchEnvelope`.
- [ ] `validate_executor_coverage` raises `ExecutorCoverageError` naming every `(agent_ref, node_name)` that has neither a registration nor a `no_executor` exemption.
- [ ] `trace_kind="executor_call"` added with required fields: `dispatch_id`, `executor_ref`, `mode`, `started_at`, `finished_at`, `outcome`.
- [ ] Unit tests: register/resolve/duplicate-rejection/coverage-pass/coverage-fail/exemption-with-justification.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/__init__.py`
- `src/app/modules/ai/executors/registry.py`
- `src/app/modules/ai/executors/binding.py`
- `src/app/modules/ai/executors/coverage.py`
- `src/app/modules/ai/trace.py` — new trace kind.
- `tests/modules/ai/executors/test_registry.py`
- `tests/modules/ai/executors/test_coverage.py`

---

### T-214: Local executor adapter + auto-register v0.1.0 lifecycle tools

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-212, T-213

**Description:**
Build `LocalExecutor` that wraps an in-process callable, invokes it, catches exceptions to synthesize a `failed` dispatch envelope, and writes the `Dispatch` row through the shared persistence path. Then auto-register every existing `lifecycle-agent@0.1.0` tool (`generate_tasks`, `generate_plan`, `assign_task`, `wait_for_implementation`, `review_implementation`, `corrections`, `close_work_item`, `load_work_item`) as a local executor bound to its node — so v0.1.0 keeps working with zero YAML edits. Any LLM access in those handlers becomes a constructor-injected `core/llm` client, not a runtime-loop import.

**Rationale:**
AC-2 (v0.1.0 unchanged) + AC-3 (local-mode dispatch path proven). This is the single largest task because it's also the one that retires the in-process tool surface as the runtime's primary integration point.

**Acceptance Criteria:**
- [ ] `LocalExecutor` in `executors/local.py` — synchronous Python invocation wrapped in the async dispatch contract; exceptions → `failed` envelope with `reason="exception"` and the traceback in `result.detail`.
- [ ] `executors/bootstrap.py` (lifespan-time wiring) registers each v0.1.0 tool against `(lifecycle-agent@0.1.0, <node-name>)`.
- [ ] Tool handlers that today import `core/llm` directly receive an `LLMClient` instance via constructor (factory built by `bootstrap.py`).
- [ ] No v0.1.0 tool file is deleted; signatures may be wrapped but not removed.
- [ ] All existing `tests/modules/ai/tools/lifecycle/test_*.py` pass unchanged.
- [ ] New unit tests: dispatch success, dispatch raises → `failed`, dispatch returns wrong shape → `failed` with `reason="contract_violation"`.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/local.py` — new.
- `src/app/modules/ai/executors/bootstrap.py` — new.
- `src/app/modules/ai/tools/lifecycle/*.py` — minimal edits to accept an `LLMClient` constructor dependency where applicable.
- `tests/modules/ai/executors/test_local.py`

**Technical Notes:**
Watch for tools that today reach into `app.state` for the LLM client — that pattern must move to constructor injection so the runtime loop is no longer the LLM owner. If a tool currently writes to disk through `atomic_write.py`, leave that helper in place — it's an executor-internal concern now, not an orchestrator concern.

---

### T-215: Remote executor adapter — HTTP dispatch + correlation + timeout

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-212, T-213

**Description:**
`RemoteExecutor` POSTs `{ dispatch_id, intake, callback_url }` to a configured URL, expects `202`, persists the `Dispatch` row as `dispatched`, and returns immediately to the runtime loop (which then awaits via the supervisor primitive from T-219). Per-dispatch timeout (`EXECUTOR_DISPATCH_TIMEOUT_SECONDS`, default 600) fires `mark_failed(reason="timeout")` if no webhook reply arrives. Reuses HTTP client conventions from `engine_client.py`.

**Rationale:**
AC-4. Without a working remote adapter the executor seam is a thought experiment.

**Acceptance Criteria:**
- [ ] `executors/remote.py` with `RemoteExecutor(url, auth_header, timeout_s)`.
- [ ] Dispatch envelope POSTed with `Content-Type: application/json` + signed via the same HMAC helper used by engine outbound (or a new `EXECUTOR_DISPATCH_SECRET` with the same shape if we want per-executor isolation — pick the simpler one and document).
- [ ] Timeout fires `mark_failed` *and* writes a trace entry; the supervisor unblocks the awaiting loop iter with the `failed` outcome.
- [ ] Setting `EXECUTOR_DISPATCH_TIMEOUT_SECONDS` is added to `config.py` + `.env.example`.
- [ ] Unit tests via `respx`: 202 path, 5xx → `failed reason=remote_error`, network error → `failed reason=connection`, timeout fires.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/remote.py` — new.
- `src/app/config.py` — new setting.
- `.env.example` — documentation block.
- `tests/modules/ai/executors/test_remote.py`

**Technical Notes:**
Reuse the bounded-retry policy from FEAT-003 (`AnthropicLLMProvider`) — 3 attempts on 5xx/connection/timeout, no retry on 4xx. Keep the retry inside the *initial* dispatch POST only; do not retry once the executor has accepted (the dispatch is then in flight and the timeout owns recovery).

---

### T-216: `POST /hooks/executors/{executor_id}` — persist-first webhook

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-212, T-215, T-219

**Description:**
New route: HMAC verify → persist `webhook_events` row (with `signature_ok` regardless) → look up `Dispatch` by `dispatch_id` from the body → `mark_completed` or `mark_failed` per `outcome` → call `supervisor.deliver_dispatch(dispatch_id, outcome, result)`. Idempotent on `dispatch_id` (second arrival returns `200` with `meta.alreadyReceived=true`).

**Rationale:**
AC-4. Mirrors the existing `/hooks/engine/*` discipline exactly — same persist-first ordering, same signature-failure handling, same idempotency story.

**Acceptance Criteria:**
- [ ] Route in `src/app/modules/ai/router.py`; payload schema in `schemas.py`.
- [ ] HMAC failure → 401 + `signature_ok=false` row + no dispatch state change.
- [ ] Unknown `dispatch_id` → 404 RFC 7807 problem details.
- [ ] Already-terminal dispatch + same outcome → 200 with `meta.alreadyReceived=true`.
- [ ] Already-terminal dispatch + different outcome → 409 conflict (dispatch results are immutable after terminal).
- [ ] Integration test: full round-trip with `respx` for the orchestrator → executor leg and direct `httpx.AsyncClient` POST for the executor → orchestrator leg.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — new endpoint.
- `src/app/modules/ai/schemas.py` — `ExecutorWebhookPayload`.
- `src/app/modules/ai/service.py` — `handle_executor_webhook` service function.
- `tests/integration/test_executor_webhook.py`
- `docs/api-spec.md` — add endpoint + changelog entry.

---

### T-217: Human executor — `/signals` as dispatch-result wiring

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-212, T-219

**Description:**
Reframe `POST /api/v1/runs/{id}/signals` as the human-executor return path. When a signal arrives for a node bound to a human executor, look up the in-flight `Dispatch` for that `(run_id, task_id)` and `mark_completed` with `result={signal_name, payload}`. Existing operator workflows preserved bit-for-bit; the wire format does not change.

**Rationale:**
AC-6. Keeps the manual-signal flow alive while uniting it under the dispatch contract.

**Acceptance Criteria:**
- [ ] `service.handle_signal` resolves the matching `Dispatch`; if none, the existing pre-FEAT-009 behavior is preserved (RunSignal row only) so non-human-executor signal use cases stay valid.
- [ ] `HumanExecutor` class in `executors/human.py` whose `dispatch` writes the `dispatched` row and returns control immediately (the actual completion comes via the signal endpoint).
- [ ] Existing signal idempotency contract preserved: duplicate signal name → 202 + `meta.alreadyReceived=true`.
- [ ] `wait_for_implementation` v0.1.0 tool is rebound to `HumanExecutor` (one-line edit in `executors/bootstrap.py`).

**Files to Modify/Create:**
- `src/app/modules/ai/executors/human.py` — new.
- `src/app/modules/ai/service.py` — `handle_signal` extended.
- `src/app/modules/ai/executors/bootstrap.py` — rebinding for v0.1.0 wait node.
- `tests/integration/test_human_executor.py`

---

## Runtime loop

### T-218: Lifespan wiring — registry build + `validate_executor_coverage` + `app.state.executor_registry`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-213, T-214

**Description:**
At lifespan startup, build the `ExecutorRegistry`, call `executors.bootstrap.register_all_executors`, run `validate_executor_coverage` against every loaded agent's flow declaration, and refuse to boot on coverage failure. Mount the registry at `app.state.executor_registry`.

**Rationale:**
AC-1. The validator only matters if it actually runs at boot.

**Acceptance Criteria:**
- [ ] `lifespan.py` calls `register_all_executors` after `register_all_effectors`.
- [ ] `validate_executor_coverage(registry, agents)` runs and aborts startup on failure with a message that names the unbound `(agent_ref, node_name)`.
- [ ] Test that asserts a deliberate misconfiguration fails the lifespan.

**Files to Modify/Create:**
- `src/app/lifespan.py`
- `tests/test_lifespan.py` (or extend the existing one)

---

### T-219: `RunSupervisor.await_dispatch` / `deliver_dispatch` primitives

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-212

**Description:**
Add the supervisor primitives that the runtime loop awaits when an executor is in-flight: `await_dispatch(dispatch_id) -> DispatchEnvelope` (used inside the loop), `deliver_dispatch(dispatch_id, envelope)` (called by the webhook + signal routes + the local-executor wrapper). Per-dispatch `asyncio.Future` keyed in a process-local map, timeout-protected via the dispatch row's deadline.

**Rationale:**
AC-6. The current bespoke `await_signal` / `deliver_signal` becomes a thin wrapper over these primitives so all three dispatch modes wake the loop the same way.

**Acceptance Criteria:**
- [ ] `RunSupervisor` exposes the new primitives; existing `await_signal` / `deliver_signal` are reimplemented on top of them.
- [ ] Future map cleaned up on terminal delivery; `cancel_dispatch(run_id)` invoked when a run is cancelled mid-dispatch.
- [ ] Unit tests: deliver-before-await (set immediately), await-before-deliver (blocks then resumes), double-deliver (second is no-op), cancel-while-awaiting (raises `DispatchCancelled`).

**Files to Modify/Create:**
- `src/app/modules/ai/runtime_helpers.py` — `RunSupervisor` extension.
- `tests/modules/ai/test_run_supervisor_dispatch.py`

---

### T-220: Runtime loop — `FlowResolver` + dispatch + remove LLM-as-policy

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-211, T-214, T-218, T-219

**Description:**
Rewrite the per-iteration body of the runtime loop in `modules/ai/service.py` to: open session → load run state → call `FlowResolver.resolve_next` → resolve executor binding → write `Dispatch (pending → dispatched)` → call `executor.dispatch` → `await supervisor.await_dispatch` → record outcome → evaluate stop conditions → advance. Delete the LLM-as-policy code path from the loop (the `select_next_tool` invocation, the per-call tool-list assembly, the `terminate` tool special case — terminal nodes come from the YAML's `terminalNodes` list now).

**Rationale:**
AC-5, AC-6. This is the load-bearing change that makes the orchestrator a pure orchestrator. The four-step loop becomes literally four steps.

**Acceptance Criteria:**
- [ ] `service.py` loop body has no import of `core/llm` and no call to `select_next_tool`.
- [ ] Stop-condition priority preserved: `cancelled > error > budget_exceeded > policy_terminated > done_node` (where `done_node` now means "resolver returned `TerminalSentinel`").
- [ ] `MAX_STEPS_PER_RUN` enforcement preserved.
- [ ] Trace continues to emit one entry per loop iteration; `policy_call` kind is no longer written from the loop (it can still be emitted by executors that internally call an LLM).
- [ ] Existing runtime-loop unit tests adapted; the composition-integrity test from CLAUDE.md ("remove the LLM → deterministic pipeline still runs") becomes trivially true and should still be asserted.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py` — loop body rewrite.
- `src/app/modules/ai/runtime_helpers.py` — drop policy-related helpers.
- `src/app/modules/ai/tools/__init__.py` — drop `terminate` tool injection (terminal handling moves to FlowResolver).
- Existing tests across `tests/modules/ai/test_service*.py` — adjust fixtures to the new contract.

**Technical Notes:**
This task is the irreversible commit. Land T-211 through T-219 cleanly, run the full suite green, *then* do T-220 in one focused PR so the regression surface is small.

---

### T-221: Restart reconciler — orphan dispatch handling

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-212, T-219

**Description:**
On orchestrator startup, scan `dispatches` for rows in `dispatched` state with no in-process `RunSupervisor` future (i.e. orphaned by a crash or restart). For remote dispatches with a configured executor health endpoint, query the executor for the `dispatch_id` status; otherwise mark `cancelled` with `reason="orchestrator_restart"`. Add `uv run orchestrator reconcile-dispatches [--since=24h] [--dry-run]` CLI command for operator-driven reruns. Mirrors the FEAT-008 outbox reconciler shape.

**Rationale:**
Edge case "Correlation across crashes" + AC-6 (system-level). Without this, a crash mid-dispatch leaves a run permanently stuck.

**Acceptance Criteria:**
- [ ] Lifespan-time scan with a configurable lookback (`EXECUTOR_RESTART_LOOKBACK_HOURS`, default 24).
- [ ] CLI command `reconcile-dispatches` with idempotent semantics.
- [ ] Dry-run mode prints the action plan, performs no writes.
- [ ] Trace emits one `executor_call` finalization entry per reconciled dispatch.
- [ ] Integration test: simulate a crash by writing a `dispatched` row with no future, restart-equivalent reconcile, assert `cancelled` outcome.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/reconcile.py` — new.
- `src/app/cli.py` — new command.
- `src/app/lifespan.py` — call reconciler at boot.
- `tests/integration/test_dispatch_reconciler.py`

---

## `lifecycle-agent@0.2.0`

### T-222: `lifecycle-agent@0.2.0` YAML — dispatch verbs + branch rules + executor system prompts

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-211, T-213

**Description:**
Author `agents/lifecycle-agent@0.2.0.yaml`. Nodes are dispatch verbs: `request_work_item_load`, `request_task_generation`, `request_assignment`, `request_plan`, `request_implementation`, `request_review`, `request_correction`, `request_closure`. Multi-target transitions carry `branch:` blocks consumable by `FlowResolver` (e.g. `review_implementation: branch: { rule: "result.verdict == 'pass'", true: request_closure, false: request_correction }`). The old `policy.systemPrompts` block is gone; the per-node system prompt moves to an `executors[node].systemPrompt` field on each LLM-backed executor's binding.

**Rationale:**
AC-3, AC-8. The new YAML is the artifact that proves the new shape is expressible cleanly.

**Acceptance Criteria:**
- [ ] YAML loads via the existing agent loader (extend it to parse `branch:` and `executors[node].systemPrompt` if needed; keep v0.1.0 schema valid).
- [ ] `FlowResolver` exhaustive-walk test from T-211 now binds to the real v0.2.0 YAML and passes.
- [ ] `validate_executor_coverage` passes against v0.2.0 once T-223 lands.
- [ ] No `policy.systemPrompts` anywhere in v0.2.0.

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.2.0.yaml` — new.
- `src/app/modules/ai/agent_loader.py` — branch + per-node system-prompt parsing.
- `tests/modules/ai/test_agent_loader.py` — branch parsing cases.

---

### T-223: v0.2.0 executors — LLM-backed (task gen, plan, review) + pure (assign, correction, closure, load)

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-214, T-222

**Description:**
Build the executor handlers behind v0.2.0's nodes. The four LLM-backed handlers (`request_task_generation`, `request_plan`, `request_review`) accept an `LLMClient` constructor dep and use the system prompt declared in the YAML. The pure handlers (`request_work_item_load`, `request_assignment`, `request_correction`, `request_closure`) are deterministic memory-mutating callables (load file, pick assignee, increment counter, flip status).

**Rationale:**
AC-3. v0.2.0 is the proof-of-life for the new shape; without these handlers the YAML is decoration.

**Acceptance Criteria:**
- [ ] All eight handlers in `src/app/modules/ai/executors/lifecycle_v2/` (one module per handler).
- [ ] Auto-registered via `executors/bootstrap.py` against the v0.2.0 agent ref.
- [ ] `request_review` returns `result.verdict in {"pass", "fail"}` so the `FlowResolver` branch rule from T-222 has the field it expects.
- [ ] `request_correction` increments `memory.correction_attempts[task_id]`; on bound exceeded, returns `result.terminal=true` and the resolver maps that to a `TerminalSentinel` with `stop_reason="correction_budget_exceeded"`.
- [ ] Unit tests per handler with the LLM stubbed at the `LLMClient` seam.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/lifecycle_v2/__init__.py`
- One file per handler under that directory.
- `src/app/modules/ai/executors/bootstrap.py` — v0.2.0 registrations.
- `tests/modules/ai/executors/lifecycle_v2/test_*.py`

---

## Integration & failure-mode tests

### T-224: Integration — `lifecycle-agent@0.1.0` end-to-end unchanged

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-220

**Description:**
Run the existing v0.1.0 end-to-end test (cold start → terminal node) after the runtime-loop swap. No edits to the test, no edits to the v0.1.0 YAML, no edits to the v0.1.0 tool source. Asserts AC-2 directly.

**Acceptance Criteria:**
- [ ] Existing test files under `tests/integration/test_lifecycle_agent_v01_*.py` (or equivalents) pass with zero modifications.
- [ ] Trace shape preserved (no missing or unexpected entries).

---

### T-225: Integration — `lifecycle-agent@0.2.0` cold-start to closure, local-only

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-223

**Description:**
End-to-end test: `POST /api/v1/runs` with v0.2.0 against a throwaway work-item file, every node bound to a local executor, run completes with `stop_reason=done_node`. Trace contains exactly one `executor_call` per node and zero `policy_call` entries from the loop. Asserts AC-3.

**Acceptance Criteria:**
- [ ] Test in `tests/integration/test_lifecycle_agent_v02_local.py`.
- [ ] Asserts no `policy_call` trace entries originate from `service.py`.
- [ ] Asserts `executor_call` count equals declared node count traversed.

---

### T-226: Integration — `lifecycle-agent@0.2.0` with one remote executor (respx stub)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-225, T-215, T-216

**Description:**
Same v0.2.0 flow as T-225 but `request_implementation` is bound to a remote executor at a `respx`-mocked URL. The mock receives the dispatch POST, asynchronously POSTs back to `/hooks/executors/{id}` with a successful outcome, the loop wakes and continues to closure. Asserts AC-4.

**Acceptance Criteria:**
- [ ] Test in `tests/integration/test_lifecycle_agent_v02_remote.py`.
- [ ] HMAC signature on the inbound webhook is computed via the production helper.
- [ ] Run reaches `done_node`; the remote `executor_call` trace entry shows `mode=remote`.

---

### T-227: Integration — failure-mode coverage

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-220, T-215, T-216, T-221

**Description:**
Six tests, one file each (or one file with six cases), exercising every edge case from the brief's §9: (1) local executor raises, (2) remote 5xx, (3) remote timeout, (4) duplicate webhook delivery, (5) bad HMAC, (6) cancellation mid-dispatch, (7) restart-with-orphan-dispatch reconciliation.

**Acceptance Criteria:**
- [ ] Each scenario produces the expected `Dispatch` terminal state and stop-condition outcome.
- [ ] Trace entries are present and well-formed for every failure path.
- [ ] Run-level outcome matches the priority bucket (`error` for failures, `cancelled` for user cancel).

**Files to Modify/Create:**
- `tests/integration/test_executor_failure_modes.py` — new.

---

### T-228: Structural test — runtime loop has no artifact production and no LLM-as-policy

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-220

**Description:**
Static guard test, sibling to `tests/test_adapters_are_thin.py`. Asserts: (1) `modules/ai/service.py` and `modules/ai/runtime_helpers.py` do not import `core.llm`, `app.modules.ai.tools.lifecycle.atomic_write`, or any executor handler module; (2) those files contain no `open(...)`, `subprocess`, or `git` references; (3) the import-time module list does not pull `anthropic` or `openai`. Encodes AC-5 as a permanent property.

**Acceptance Criteria:**
- [ ] Test exists, fails on a deliberate violation (commented-out negative case), passes on the real code.

**Files to Modify/Create:**
- `tests/test_runtime_loop_is_pure.py` — new.

---

## Polish & docs

### T-229: Docs realignment — ARCHITECTURE.md, CLAUDE.md, data-model.md, api-spec.md

**Type:** Documentation
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-220, T-216, T-222

**Description:**
Walk the project docs and propagate the FEAT-009 shape:

- `ARCHITECTURE.md` — restate AD-1 explicitly; add the tools/executors/effectors disambiguation block from T-210; show the new four-step loop; supersede the LLM-as-policy AD reference; changelog entry.
- `CLAUDE.md` — Patterns: add "Executors are the only producers" and "Flow control comes from the YAML, not the LLM." Anti-Patterns: add "Don't write artifacts from inside the runtime loop" and "Don't ask an LLM what node comes next." Update the Quick-Reference directory map under `modules/ai/` with `executors/` and `flow_resolver.py`. Update the "LLM Providers" section to clarify LLMs are now an executor concern.
- `data-model.md` — `Dispatch` entity body (already added in T-212; refine if needed); changelog entry.
- `api-spec.md` — `POST /hooks/executors/{executor_id}` body + responses; reframe `/signals` as "human executor return path"; changelog entry.

**Acceptance Criteria:**
- [ ] All four docs updated; each carries a 2026-04-26 (or current) changelog entry referencing FEAT-009.
- [ ] No leftover references to LLM-as-policy without a "(superseded by FEAT-009)" qualifier.
- [ ] `CLAUDE.md` Pre-Work Checklist remains valid (no broken file paths).

**Files to Modify/Create:**
- `docs/ARCHITECTURE.md`
- `CLAUDE.md`
- `docs/data-model.md`
- `docs/api-spec.md`

---

## Summary

**Total task count: 20** (T-210 through T-229).

By type:
- Backend: 11 (T-211, T-213, T-214, T-215, T-216, T-217, T-218, T-219, T-220, T-221, T-222, T-223 — note T-222 includes a small loader edit so straddles)
- Database: 1 (T-212)
- Testing: 5 (T-224, T-225, T-226, T-227, T-228)
- Documentation: 2 (T-210, T-229)

Complexity distribution:
- S: T-210, T-217, T-218, T-224, T-228
- M: T-211, T-212, T-213, T-215, T-216, T-219, T-221, T-222, T-225, T-226, T-227, T-229
- L: T-214, T-220, T-223
- XL: none — the L-tier tasks (T-214, T-220, T-223) are the load-bearing ones; nothing single-PR is heavier than that.

**Critical path** (longest dependency chain — also the recommended landing order):
T-212 → T-213 → T-214 → T-218 → T-220 → T-225 → T-226

That is: dispatch model → registry → local adapter → lifespan wiring → runtime-loop swap → v0.2.0 local-only proof → v0.2.0 remote proof.

T-211 (FlowResolver) and T-210 (ADR) can land in parallel with the early steps. T-229 (docs sweep) is the natural closing PR after T-228 (structural guard) flips green.

**Risks / open questions**

- **Branch-rule expressiveness.** The T-211 expression syntax (`result.<field> <op> <literal>`) covers v0.2.0's two real branches but is intentionally narrow. If a future agent needs richer conditions (numeric comparisons, `in` checks), revisit before broadening — `eval` is not the answer.
- **`anthropic` import quarantine.** Existing `tests/test_adapters_are_thin.py` quarantines `anthropic` from the runtime; T-228 broadens that to also quarantine it from `service.py` / `runtime_helpers.py`. If the test starts triggering on a transitive import, the right fix is to push that import behind a lazy local import inside an executor — not to weaken the test.
- **`agents/lifecycle-agent@0.1.0.yaml` `policy.systemPrompts` block.** v0.1.0's YAML still has it, and the loader has to keep parsing it for v0.1.0 compatibility. Decision needed during T-222: tolerate the field on v0.1.0 and forbid it on v0.2.0+, or silently ignore everywhere. Recommendation: tolerate on v0.1.0, hard-fail on v0.2.0+.
- **Local executor and DB session ownership.** A local executor that needs DB access today has it implicitly via the runtime loop's session. After FEAT-009, the session is loop-owned and short-lived — local executors will need their own session via `session_factory`. The `LocalExecutor` constructor in T-214 should accept `session_factory` and pass it through, *not* hand the loop's session to the handler.
- **Engine-absent fallback (FEAT-008).** The current code preserves a pre-FEAT-008 inline path when no `lifecycle_engine_client` is configured. T-220's loop rewrite must keep that fallback intact — the executor seam is orthogonal to the engine-as-authority decision and should not regress dev-mode operation.
