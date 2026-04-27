# Implementation Plan: FEAT-011 — Deterministic lifecycle agent port (`lifecycle-agent@0.3.0`)

## Task Reference
- **Feature brief:** [`docs/work-items/FEAT-011-lifecycle-agent-deterministic-port.md`](../docs/work-items/FEAT-011-lifecycle-agent-deterministic-port.md)
- **Task list:** [`tasks/FEAT-011-tasks.md`](../tasks/FEAT-011-tasks.md) — T-250 through T-268.
- **Workflow:** standard. Per-task implementation plans (`plan-T-XXX-*.md`) are generated when each task is picked up; this document is the feature-level sequencing + decisions. (Same convention as FEAT-009 / FEAT-010.)
- **Complexity:** ~7–10 dev days end-to-end across the 19 tasks. The three L-tier tasks (T-252 `LLMContentExecutor`, T-253 v0.3.0 YAML, T-260 happy-path + budget e2e) are the bulk; everything else is mid-sized additions on top of FEAT-009's seam + FEAT-010's `EngineExecutor`.

---

## Overview

FEAT-011 lands in **five sequenced PRs**, each green on its own and reversible until the next merges. The order is dictated by the dependency graph in `tasks/FEAT-011-tasks.md`: design doc + predicates first (so the YAML port has a contract to match); the new executor + YAML scaffold second (still inert from the runtime's perspective); the full bootstrap wiring third (where the happy-path e2e proves the seam works for the production workload); the four semantic edge cases fourth (correction budget, rejection paths, pause/resume, restart safety); the closing docs + live-LLM contract test fifth.

The **single load-bearing decision** is the `LifecycleMemory` shape choice in T-250 — every executor written in T-254 reads/writes memory through whichever path that doc names. Pinning the choice in PR 1 prevents reviewers from relitigating it in every later PR.

The **regression bar (AC-7)** runs in every PR: the v0.1.0 LLM-policy e2e suite must pass unchanged. Failure pauses work until cause is identified and fixed.

---

## PR sequencing

### PR 1 — Foundation: design doc + branch predicates (T-250, T-251)

**Goal:** Land the design doc (mapping table + memory shape decision + predicate inventory) and the new branch predicates. The runtime, executor seam, and existing agents are **unchanged**; v0.3.0 YAML does not yet exist.

- **T-250**: Design doc `docs/design/feat-011-lifecycle-deterministic-port.md` with the three load-bearing artefacts: node-to-engine-transition mapping table, `LifecycleMemory` shape decision (single-valued), branch-predicate inventory.
- **T-251**: Branch-predicate registry extension — `review_passed` and `task_rejected`. `flow_resolver.py` is **not modified**.

**Reversibility:** Pure additions. No agent file, no executor file, no schema change.

**Acceptance gate:** Design doc reviewed and merged with a single concrete `LifecycleMemory` shape recommendation; new predicates' unit tests pass; `flow_resolver.py` `git diff` is empty; v0.1.0 e2e suite green.

---

### PR 2 — `LLMContentExecutor` + agent YAML scaffold (T-252, T-253, T-263)

**Goal:** Land the new executor module and the v0.3.0 YAML, but do **not** wire them into bootstrap yet. The runtime can't reach the new agent because no executor binding for it exists at lifespan startup; coverage validator (FEAT-009) would refuse to boot a run against `lifecycle-agent@0.3.0` — which is fine, because no caller starts such a run in this PR.

- **T-252**: `LLMContentExecutor` under `src/app/modules/ai/executors/llm_content.py`. Unit-tested against `StubLLMProvider` (success path, schema-validation failure, retry exhaustion, prompt-rendering).
- **T-253**: `agents/lifecycle-agent@0.3.0.yaml` declaring `flow.policy: deterministic`, eight nodes with declared transitions and `branch:` blocks per T-251 / T-250's predicate inventory. No `policy.systemPrompts` block at agent level.
- **T-263**: AC-8 exhaustive branch-walk unit test. Runs against the new YAML; asserts every transition is reachable and the resolver does not instantiate any LLM client.

**Reversibility:** Pure additions. The new executor module is dead code until PR 3 wires it; the new YAML is on disk but uncallable (no executor coverage). Reverting the PR removes both files.

**Acceptance gate:** `LLMContentExecutor` unit tests green; v0.3.0 YAML parses (agent loader accepts it); branch-walk test enumerates every transition; v0.1.0 e2e suite green.

---

### PR 3 — Bootstrap wiring + happy-path e2e (T-254, T-255, T-260, T-265)

**Goal:** Wire every v0.3.0 node to its executor via `register_lifecycle_v03`; implement the chosen `LifecycleMemory` shape; prove the seam works for the production workload with a happy-path + correction-budget e2e; confirm the coverage validator refuses to boot if any node is left unbound.

- **T-254**: `register_lifecycle_v03` in `executors/bootstrap.py` — engine-bound nodes via `register_engine_executor` (FEAT-010), LLM-content nodes via `LLMContentExecutor`, `request_implementation` via `HumanExecutor`, `correct_implementation` via `LocalExecutor`. System-prompt files copied into `src/app/modules/ai/executors/prompts/lifecycle/`. Wired into `register_all_executors`.
- **T-255**: `LifecycleMemory` shape implementation per T-250's decision. Either typed-schema-under-namespace helpers (shape 1) or `__memory_patch` envelope threading (shape 2).
- **T-260**: AC-1 + AC-2 e2e — happy path reaches `RunStatus.COMPLETED`; correction budget parametrized over attempts 1, 2, 3 (attempt 3 → `RunStatus.FAILED` + `correction_budget_exceeded`).
- **T-265**: AC-6 coverage validator — refuses to boot on missing v0.3.0 binding; boots with `no_executor` exemption.

**Load-bearing decision in this PR (confirmed by user 2026-04-26):** system prompts for LLM-content executors are **copied** from `.ai-framework/prompts/` into `src/app/modules/ai/executors/prompts/lifecycle/` (one `.md` per node) at PR-3 time — not symlinked, not imported. Rationale: v0.1.0 retirement should not silently mutate v0.3.0 behavior. Trade-off: prompt drift between two locations becomes a new maintenance surface — flag in the migration doc (T-267).

**This is the irreversible PR.** Once `register_lifecycle_v03` is wired into `register_all_executors`, lifespan startup binds the v0.3.0 executors automatically when the YAML is on disk; reverting after FEAT-012 starts consuming v0.3.0's dispatch shapes is a much larger surgery. The mitigation: T-264 (the v0.1.0 regression bar) runs in this PR and every later PR.

**Reversibility within PR 3:** The bootstrap helper is one block; reverting it (and the `register_all_executors` call site) restores the pre-PR-3 boot order exactly. The memory-shape implementation is local to `tools/lifecycle/memory.py` (shape 1) or scoped to the new executors (shape 2). The e2e test fixtures are test-only.

**Acceptance gate:** T-260 happy path green; T-260 budget parametrization green for all three attempt counts; T-265 coverage-validator test green; v0.1.0 e2e suite green (T-264 verification).

---

### PR 4 — Semantic edge cases: pause / rejection / restart safety (T-256, T-257, T-258, T-259, T-261, T-262)

**Goal:** Land the four semantic edge cases that v0.3.0 must preserve bit-for-bit from v0.1.0 — pause-for-implementation idempotency, correction budget enforcement (already wired in PR 3, here we add the focused tests), rejection paths, restart safety.

- **T-256**: Pause-for-implementation as `HumanExecutor`. `(run_id, name, task_id)` idempotency preserved. Minimal extension to `HumanExecutor` if needed.
- **T-257**: Correction budget as branch predicate + stop-condition. `LIFECYCLE_MAX_CORRECTIONS` plumbed through bootstrap. `runtime_deterministic.py` is **not modified**.
- **T-258**: Rejection paths — `LocalExecutor` writes `Approval` row inline (FEAT-008 contract preserved); resolver routes via `task_rejected`. No engine call on rejection.
- **T-259**: Restart safety — verify `reconcile-dispatches` (FEAT-010) + `reconcile-aux` (FEAT-008) handle a v0.3.0 run interrupted mid-engine-dispatch. No new reconciler module unless a v0.3.0-specific gap surfaces.
- **T-261**: AC-3 e2e — task rejection routes to rejection branch + writes Approval + zero engine POSTs.
- **T-262**: AC-4 e2e — pause-for-implementation idempotency (signal before, signal after, duplicate signal).

**Load-bearing decision in this PR:** *Does `HumanExecutor` (FEAT-009) already key on `(run_id, name, task_id)`?* If yes, T-256 collapses to verification + tests. If no, the extension is minimal — augment the existing keying tuple, do not fork a new executor mode. T-256's implementation plan on pickup makes the call.

**Reversibility:** Each semantic addition is local (one node's executor binding or one predicate). The integration tests are test-only. Reverting any one task does not regress the others.

**Acceptance gate:** T-261 + T-262 green; T-259 confirms reconciler behavior or names a v0.3.0-specific gap with a scoped fix; v0.1.0 e2e suite green (T-264 verification).

---

### PR 5 — Closing: live-LLM contract test + migration doc + docs sweep (T-264, T-266, T-267, T-268)

**Goal:** Land the live-LLM contract test (off by default), the migration doc, and the closing docs sweep. Flip FEAT-011 status to `Completed`.

- **T-264**: AC-7 regression bar — final pass of v0.1.0 e2e suite recorded in PR body alongside v0.3.0 results.
- **T-266**: AC-9 live-LLM contract test under `tests/contract/test_lifecycle_v03_live.py`, gated by `@pytest.mark.live`. Drives v0.3.0 against real Anthropic; engine stubbed; off by default.
- **T-267**: Migration doc `docs/migration/lifecycle-v01-to-v03.md`.
- **T-268**: CLAUDE.md directory map + Patterns + Anti-Patterns updates; FEAT-005 supersession note; data-model.md / api-spec.md changelog entries; FEAT-011 brief Status → `Completed`.

**Load-bearing decision in this PR:** *Cost ceiling for the live-LLM contract test.* Recommendation: bound the run via `ANTHROPIC_MAX_TOKENS` such that a single full-lifecycle run costs ≤ $1. If that proves infeasible (e.g. plan generation alone blows the budget), break T-266 into per-node contract tests rather than one full lifecycle. Document the choice in the test file and the migration doc.

**Reversibility:** Doc updates are doc updates. The contract test is gated off by default; CI does not run it. Reverting the PR drops the migration doc and the closing docs sweep but leaves the FEAT-011 implementation fully functional.

**Acceptance gate:** All v0.3.0 e2e + unit tests green; v0.1.0 e2e suite green; live-LLM contract test runs successfully when gate is on (manual verification, recorded in PR body); FEAT-011 brief Status flipped to `Completed`; no doc reference to v0.1.0 surviving without a "v0.3.0 supersedes" marker.

---

## Load-bearing decisions in one table

| Decision | Made in | Choice | Why |
|---|---|---|---|
| `LifecycleMemory` shape | T-250 (PR 1) | **Recommend shape (1) — typed schema persisted in `RunMemory.data` under stable namespace; executors use helpers.** Doc may override with rationale. | Safer port: minimizes behavioral drift from v0.1.0; typed schema catches field-name typos at executor-write time; migration churn is one-shot rather than spread across every executor. Shape (2) is cleaner long-term but multiplies risk in the v0.1.0 → v0.3.0 transition. |
| Branch-predicate vs resolver-expression | T-250 + T-251 (PR 1) | Named predicates `review_passed` + `task_rejected` if YAML expression form is awkward; otherwise inline `result.<field> <op> <literal>` | Inline form is preferable when readable; named predicates exist for clarity when the expression sprawls |
| `LLMContentExecutor` mode literal | T-252 (PR 2) | `mode="local"` — the LLM call is in-process; no wake required | Reuses the FEAT-009 sync local-mode dispatch path; no new wake leg in the runtime |
| LLM provider injection on `LLMContentExecutor` | T-252 (PR 2) | Constructor-injected `LLMProvider`; no module-scope import of provider SDKs | Preserves the FEAT-009 / FEAT-010 import-quarantine discipline |
| System-prompt source for LLM-content nodes | T-254 (PR 3) | Copy prompts from `.ai-framework/prompts/` into `src/app/modules/ai/executors/prompts/lifecycle/`; load at registration | v0.1.0 retirement should not silently mutate v0.3.0 behavior; trade-off (prompt drift surface) flagged in migration doc |
| Correction-budget enforcement | T-257 (PR 4) | Branch predicate + terminal-failure node with `final_state.reason=correction_budget_exceeded`; `runtime_deterministic.py` unchanged | Brief Section 10 constraint: must not modify `runtime_deterministic.py`; existing stop-condition pipeline already maps `final_state.reason` to `RunStatus` |
| Rejection-path executor mode | T-258 (PR 4) | `LocalExecutor` writes `Approval` inline; **no** `EngineExecutor` registration on rejection branches | FEAT-008 contract: rejection does not advance engine state; v0.3.0 preserves bit-for-bit. FEAT-012 will fold this into the unified outbox. |
| `HumanExecutor` keying tuple | T-256 (PR 4) | `(run_id, name, task_id)` — extend `HumanExecutor` minimally if needed; do not fork a new executor mode | v0.1.0 idempotency contract preserved bit-for-bit |
| AC-7 regression bar enforcement | every PR | Every PR runs the v0.1.0 e2e suite; failure is stop-and-fix | Brief Section 10: v0.1.0 e2e suite must remain green throughout this FEAT |

---

## Risks and mitigations

- **`LifecycleMemory` shape (1 vs 2) is the single biggest decision in this FEAT.** Mitigation: T-250 must resolve it before T-253 starts; the design doc is the authoritative call. Plan recommends shape (1) on conservatism grounds; the design author may flip to shape (2) with explicit rationale.
- **Mapping-table completeness.** The brief is silent on whether every engine transition (W1–W6, T1–T12) is exercised by a single run. Mitigation: T-250 enumerates the actual reachable set; T-260's transition-order assertion is built against that set, not against `declarations.py` exhaustively.
- **A v0.1.0 LLM-judgment branch can't be expressed in YAML.** Mitigation: brief Section 9 names two outs — (a) widen the dispatch result envelope so the predicate has data to resolve on (default), (b) leave that one node behind a `flow.policy: llm` sub-flow. Document the choice in the migration doc, not in code. Reach for (b) only after confirming (a) is genuinely insufficient.
- **PR 3 regresses v0.1.0.** Mitigation: T-264 (the regression bar) runs in PR 3 — and every later PR. If T-264 fails in PR 3, the bug is almost certainly in T-254 bootstrap wiring (the only file that touches `register_all_executors`); revert + diagnose.
- **`HumanExecutor` (FEAT-009) doesn't already key on `(run_id, name, task_id)`.** Mitigation: T-256's implementation plan on pickup confirms; if extension is needed, augment the existing tuple, do not fork a new executor.
- **Live-LLM contract test (AC-9) cost spirals.** Mitigation: bound runs via `ANTHROPIC_MAX_TOKENS`; if a full lifecycle exceeds reasonable cost, break T-266 into per-node contract tests rather than one full lifecycle. Test is gated off by default; CI cost stays zero.
- **Prompt drift between `.ai-framework/prompts/` and `src/app/modules/ai/executors/prompts/lifecycle/`.** Mitigation: migration doc (T-267) names the canonical source for each prompt; closing CLAUDE.md update notes the dual-location surface as a known maintenance debt to be resolved in a follow-on FEAT.
- **`runtime_deterministic.py` quietly imports `core.llm` via `LLMContentExecutor` chain.** Mitigation: existing FEAT-009 import-quarantine guard (`tests/test_runtime_deterministic_is_pure.py`) catches this; T-263 adds the v0.3.0-specific branch-walk guard. Two structural tests must pass for every PR in this FEAT.

---

## Cross-PR conventions

- Every PR description references FEAT-011 + the specific task IDs landing in that PR + a one-line note on what's reversible vs not.
- Each PR's commit message follows Conventional Commits with a `feat(FEAT-011):` prefix (PRs 2, 3, 4) or `docs(FEAT-011):` (PR 1 design doc, PR 5 migration + docs sweep).
- The `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer on every commit, per CLAUDE.md.
- Doc updates land in the same PR as the code that prompts them — design doc in PR 1; v0.3.0 YAML in PR 2; bootstrap-prompt files in PR 3; migration doc + closing CLAUDE.md update in PR 5.
- Every PR runs the v0.1.0 e2e suite (T-264 regression bar) and records the result in the PR body. Failure is stop-and-fix, not a deferred ticket.

---

## When this is done

- A `flow.policy: deterministic` agent (`lifecycle-agent@0.3.0`) drives the production lifecycle end-to-end, dispatching engine transitions through `EngineExecutor` and producing artefacts (briefs, plans, reviews) through `LLMContentExecutor` — with no LLM call in the runtime loop itself.
- The eight v0.1.0 nodes have a corresponding executor binding in v0.3.0; the coverage validator refuses to boot if any binding is missing.
- Every v0.1.0 operational behavior (correction budget trip, rejection paths, pause/resume idempotency, restart safety, aux-row materialization) reproduces under v0.3.0 deterministic flow.
- `lifecycle-agent@0.1.0` and its LLM-policy runtime continue to work unchanged; v0.1.0 e2e suite is green throughout the FEAT.
- The branch-walk structural test asserts every v0.3.0 transition resolves without instantiating any LLM client.
- A live-LLM contract test (off by default) demonstrates the LLM-content executor path against the real Anthropic provider.
- Migration doc + CLAUDE.md update + FEAT-005 supersession note are merged; FEAT-011 status is `Completed`.

That's the principal-objective alignment FEAT-011 was filed to land — the production lifecycle on the pure-orchestrator architecture, no LLM tax on flow decisions, FEAT-012's aux-write outbox unification unblocked.
