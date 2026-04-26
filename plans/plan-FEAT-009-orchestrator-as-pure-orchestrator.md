# Implementation Plan: FEAT-009 — Orchestrator as a pure orchestrator

## Task Reference
- **Feature brief:** [`docs/work-items/FEAT-009-orchestrator-as-pure-orchestrator.md`](../docs/work-items/FEAT-009-orchestrator-as-pure-orchestrator.md)
- **Task list:** [`tasks/FEAT-009-tasks.md`](../tasks/FEAT-009-tasks.md) — T-210 through T-229.
- **Workflow:** standard. Per-task implementation plans (`plan-T-XXX-*.md`) are generated when each task is picked up; this document is the feature-level sequencing + decisions.
- **Complexity:** ~10–14 dev days end-to-end across the 20 tasks. The L-tier tasks (T-214, T-220, T-223) account for roughly half of that.

---

## Overview

FEAT-009 lands in **six sequenced PRs**, each green on its own and reversible until the next merges. The order is dictated by the dependency graph in `tasks/FEAT-009-tasks.md`: the executor seam comes in before the loop swap, the loop swap comes in before any v0.2.0 work, and the v0.2.0 work proves both halves of the feature (the no-LLM-loop and the pluggable-executor surfaces) end-to-end.

The **two irreversible tasks** are T-214 (auto-registers v0.1.0 tools as local executors — touches every tool file) and T-220 (rewrites the runtime loop body and removes the LLM-as-policy code path). Everything else is additive.

---

## PR sequencing

### PR 1 — Foundation (T-210, T-211, T-212)

**Goal:** Land the architectural decision, the pure flow resolver, and the dispatch persistence layer. No runtime behavior change.

- **T-210**: ADR `docs/design/feat-009-pure-orchestrator.md` + supersession banner on the LLM-as-policy AD.
- **T-211**: `src/app/modules/ai/flow_resolver.py` + `flow_predicates.py` + exhaustive transition test (using a fixture flow until T-222 lands the real v0.2.0 YAML).
- **T-212**: `Dispatch` model + Alembic migration + `data-model.md` entry.

**Reversibility:** Pure additions. Migration is forward-safe; downgrade refuses on populated tables (FEAT-008 pattern).

**Done when:** New tests pass; no existing tests touched; the structural test for T-211 (no `anthropic` import) is in place.

---

### PR 2 — Executor seam, no consumers yet (T-213, T-219)

**Goal:** Land the registry, coverage validator, trace kind, and the `RunSupervisor.await_dispatch / deliver_dispatch` primitives. Still no behavior change to running code.

- **T-213**: `executors/{__init__,registry,binding,coverage}.py`; `trace_kind="executor_call"`; `validate_executor_coverage` helper. No registrations yet, so the validator is a no-op.
- **T-219**: `RunSupervisor` extension. Existing `await_signal` / `deliver_signal` are reimplemented on top of the new primitives but remain wire-compatible.

**Reversibility:** Additions only. The supervisor change is internal; the public surface (`await_signal`, `deliver_signal`) is byte-identical.

**Done when:** New unit tests pass; the existing supervisor tests pass without edits.

---

### PR 3 — Local adapter + v0.1.0 auto-registration (T-214, T-218, T-224)

**Goal:** Wire the local executor adapter and register every v0.1.0 tool. Lifespan now builds the registry and validates coverage. v0.1.0 still runs through its current code path because the runtime loop has not yet been swapped — but the registry is now populated and asserted.

- **T-214**: `LocalExecutor` + `executors/bootstrap.py` registering all eight v0.1.0 tools. Tools that today reach into `app.state` for the LLM client get a constructor-injected `LLMClient`.
- **T-218**: `lifespan.py` calls `register_all_executors` and `validate_executor_coverage`.
- **T-224**: Existing v0.1.0 end-to-end test runs *unchanged*. This PR's regression bar.

**Load-bearing decision in this PR:** *DB session ownership for local executors.* The `LocalExecutor` constructor takes `session_factory`, **not** the loop's session. Each dispatch opens its own short-lived session, mirroring the runtime-loop convention. This is the right answer even though no caller exercises it yet — fixing it after T-220 swaps the loop is a much larger surgery.

**Reversibility:** Adapter is additive; lifespan wiring is one block that can be commented out. Tool-file edits (constructor injection) are scoped — review them carefully but they don't change tool semantics.

**Done when:** v0.1.0 e2e is green; coverage validator passes; deliberate-misconfiguration test fails the lifespan as expected.

---

### PR 4 — Remote + human + webhook (T-215, T-216, T-217, T-221)

**Goal:** Complete the executor mode trio and put the restart reconciler in place. Still pre-loop-swap, so this exercises the new code only via direct unit/integration tests, not via a running agent.

- **T-215**: `RemoteExecutor` (HTTP POST + correlation + timeout).
- **T-216**: `POST /hooks/executors/{executor_id}` (HMAC + persist-first + idempotent).
- **T-217**: `HumanExecutor` reframing of `/signals` (preserves existing wire format and idempotency).
- **T-221**: Restart reconciler + `uv run orchestrator reconcile-dispatches` CLI.

**Load-bearing decision in this PR:** *HMAC secret strategy for remote executors.* Two options: reuse the existing engine outbound HMAC helper (one shared secret), or introduce `EXECUTOR_DISPATCH_SECRET` and per-executor secret rotation. Recommendation: start with one shared secret (simpler config, mirrors current engine pattern) and add per-executor rotation only when a real second executor demands it. Document the choice in T-215's PR description.

**Reversibility:** All additive — no existing route is modified. The `/signals` route extension is backward-compatible: signals that don't match a `Dispatch` follow the pre-FEAT-009 behavior.

**Done when:** Failure-mode unit tests pass for each adapter; restart reconciler integration test passes; v0.1.0 e2e still green.

---

### PR 5 — The loop swap (T-220, T-228)

**Goal:** Replace the runtime loop body with `FlowResolver` + dispatch + supervisor wait. Remove the LLM-as-policy code path. Lock the new shape with a structural test.

- **T-220**: Loop body in `service.py` rewritten. `select_next_tool`, the per-call tool-list assembly, and the `terminate` tool injection are deleted from the loop. Stop conditions and `MAX_STEPS_PER_RUN` preserved.
- **T-228**: Static guard that `service.py` and `runtime_helpers.py` import neither `core.llm` nor any executor handler module — the test that prevents drift.

**This is the irreversible PR.** v0.1.0 must continue to pass T-224's e2e *because* every v0.1.0 node now resolves through the local executor adapter from PR 3. If T-224 fails here, the bug is in T-220 — do not weaken T-224 or paper over it in the local adapter.

**Load-bearing decision in this PR:** *Where does the v0.1.0 YAML's `policy.systemPrompts` block go?* v0.1.0 keeps its YAML untouched, so `policy.systemPrompts` stays in the file and the loader keeps reading it — the field is just no longer consulted by the loop. Each v0.1.0 LLM-backed tool reads its prompt from the loaded agent definition via the constructor-injected configuration, not from a runtime-loop call. T-222 will hard-fail this field on v0.2.0+ but tolerate it on v0.1.0.

**Reversibility:** This PR can be reverted, but doing so requires also reverting PR 3's tool constructor-injection edits to recover the original `app.state.llm_client` lookup pattern. After this PR merges, treat the previous loop body as deleted history.

**Done when:** Full suite green including T-224 unchanged; T-228 fails on a deliberately injected `from app.core import llm` in `service.py` (commented-out negative case in the test file).

---

### PR 6 — `lifecycle-agent@0.2.0` end-to-end (T-222, T-223, T-225, T-226, T-227, T-229)

**Goal:** Prove the new shape with a real agent. Land v0.2.0 YAML, its eight executors, the local-only e2e test, the remote-stub e2e test, the failure-mode coverage suite, and the docs sweep.

- **T-222**: `agents/lifecycle-agent@0.2.0.yaml` + agent loader extension for `branch:` and `executors[node].systemPrompt`.
- **T-223**: Eight v0.2.0 executor handlers under `executors/lifecycle_v2/` + bootstrap registrations.
- **T-225**: v0.2.0 cold-start to closure, all-local. Asserts AC-3.
- **T-226**: v0.2.0 with `request_implementation` bound to a respx-stubbed remote URL. Asserts AC-4.
- **T-227**: Failure-mode integration coverage (six scenarios from §9 of the brief).
- **T-229**: ARCHITECTURE.md, CLAUDE.md, data-model.md (refinements), api-spec.md updated with FEAT-009 changelog entries.

**Done when:** All ACs from the brief pass mechanically (AC-1 through AC-8); no doc reference to LLM-as-policy survives without a "(superseded by FEAT-009)" qualifier; FEAT-009 status flipped to `Completed` in `docs/work-items/`.

---

## Load-bearing decisions in one table

| Decision | Made in | Choice | Why |
|---|---|---|---|
| Tenant id source for executor dispatch | T-215 | None — executors are tenant-agnostic at the seam; tenant context comes from the run's already-resolved settings via `EffectorContext`-equivalent | Mirrors how effectors handle it; avoids re-deriving tenant identity in three places |
| HMAC secret strategy | PR 4 / T-215 | Single shared `EXECUTOR_DISPATCH_SECRET` initially | Per-executor rotation is YAGNI until a second executor exists |
| DB session for local executors | T-214 | Constructor-injected `session_factory`; per-dispatch session | Long-lived loop session is wrong shape after T-220; fix once, not later |
| Branch-rule expression syntax | T-211 | Narrow: `result.<field> <op> <literal>` with `op in {==, !=}` | Covers v0.2.0's two real branches; richer syntax invites `eval` mistakes |
| v0.1.0 `policy.systemPrompts` after the loop swap | T-220 + T-222 | Tolerate on v0.1.0 (loader still parses, loop ignores); hard-fail on v0.2.0+ | Preserves v0.1.0 e2e without forking the loader |
| Engine-absent fallback (FEAT-008) | T-220 | Preserved bit-for-bit | Dev mode without flow-engine must keep working |

---

## Risks and mitigations

- **T-220 regresses v0.1.0.** Mitigation: T-224 lives in PR 3 (lands *before* T-220), so the regression bar is already in CI when T-220 ships. If T-224 breaks in PR 5, the failure is the local adapter or the loop swap, not the test.
- **Branch-rule predicates miss a future need.** Mitigation: `FlowResolver` raises `FlowDeclarationError` on unmappable branches at agent-load time — the failure is loud at boot, not silent at runtime. Adding a predicate is one PR.
- **`anthropic` import quarantine creeps back via a transitive import.** Mitigation: T-228's structural test makes the assertion permanent. If a future PR triggers it, the right fix is to push the import behind a lazy local import inside an executor — not to weaken the test.
- **Restart reconciler deletes a still-running remote dispatch.** Mitigation: T-221's reconciler queries the executor's health endpoint when one is configured; only marks `cancelled` when the executor cannot confirm. CLI command supports `--dry-run` so an operator can verify the action plan before any write.
- **PR 6 is large.** Mitigation: T-222 + T-223 can be split into PR 6a (YAML + handlers, no e2e) and PR 6b (e2e + failure coverage + docs) if reviewers prefer. The dependency between them is one-way, so the split is mechanical.

---

## Cross-PR conventions

- Every PR description references FEAT-009 + the specific task IDs landing in that PR + a one-line note on what's reversible vs not.
- Each PR's commit message follows Conventional Commits with a `feat(FEAT-009):` prefix (PRs 1–6) or `docs(FEAT-009):` (PRs 1, 6 documentation portions).
- The `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer on every commit, per CLAUDE.md.
- Doc updates land in the same PR as the code that prompts them — no "docs sweep at the end" except for the closing T-229 narrative pass.

## When this is done

- The orchestrator's runtime loop has four steps: resolve, dispatch, wait, record.
- The LLM does not pick nodes anywhere in the orchestrator process.
- Every artifact-producing operation lives in an executor module — local, remote, or human.
- v0.1.0 still works exactly as before; v0.2.0 works locally and proves remote dispatch via a stub.
- The structural test prevents quietly regressing any of the above.

That's the principal-objective alignment the FEAT was filed to land.
