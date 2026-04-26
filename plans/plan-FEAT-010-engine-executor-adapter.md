# Implementation Plan: FEAT-010 — Engine executor adapter

## Task Reference
- **Feature brief:** [`docs/work-items/FEAT-010-engine-executor-adapter.md`](../docs/work-items/FEAT-010-engine-executor-adapter.md)
- **Task list:** [`tasks/FEAT-010-tasks.md`](../tasks/FEAT-010-tasks.md) — T-230 through T-240.
- **Workflow:** standard. Per-task implementation plans (`plan-T-XXX-*.md`) are generated when each task is picked up; this document is the feature-level sequencing + decisions. (Same convention as FEAT-009 — per-task plans are not bundled with the FEAT plan.)
- **Complexity:** ~4–6 dev days end-to-end across the 11 tasks. The single L-tier task (T-231) is the bulk; everything else is small additions on top of FEAT-008's outbox + FEAT-009's seam.

---

## Overview

FEAT-010 lands in **three sequenced PRs**, each green on its own and reversible until the next merges. The order is dictated by the dependency graph in `tasks/FEAT-010-tasks.md`: the executor itself comes in before any reactor change (so we can unit-test it without touching the lifecycle pipeline), the reactor wake comes in with its end-to-end proof (so the wake hook is exercised the moment it ships), and the reconciler closes the operational gap last (so restart safety lands when the rest is stable).

The **single load-bearing task** is T-233 (extends the FEAT-008 reactor pipeline with a wake-dispatch step at a specific position relative to effectors and derivations). Everything else is additive.

---

## PR sequencing

### PR 1 — Foundation: design doc + executor + bootstrap (T-230, T-231, T-232, T-234, T-237, T-239)

**Goal:** Land the design doc, the `EngineExecutor`, the bootstrap helper, the trace-shape extension, and the structural import-quarantine test. The reactor is **unchanged**; an engine dispatch's HTTP call still happens but the dispatch's future is never woken — the reconciler (PR 3) would mark such an orphan as `failed` on the next restart, which is fine because no agent in this PR registers an engine executor (the throwaway test agent for AC-1 lands in PR 2).

- **T-230**: Design doc `docs/design/feat-010-engine-executor.md`.
- **T-231**: `EngineExecutor` + `ExecutorMode` literal extension. Unit-tested against a respx-stubbed engine.
- **T-232**: `register_engine_executor` bootstrap helper.
- **T-234**: Trace shape extension for `mode=engine` — landed before the dispatch path is exercised so T-236 can assert on it directly when PR 2 ships.
- **T-237**: Structural import-quarantine test — passes immediately because nothing imports `executors/engine.py` from the runtime yet.
- **T-239**: Coverage-validator test — confirms the FEAT-009 validator handles engine-bound bindings without modification.

**Reversibility:** Pure additions. No reactor edit, no runtime edit, no schema change.

**Acceptance gate:** `EngineExecutor` unit tests green; structural test green; coverage-validator test green; no existing tests touched. The whole PR is dead code from the runtime's perspective until PR 2 lands.

---

### PR 2 — The wake: reactor pipeline extension + end-to-end proof (T-233, T-236, T-238)

**Goal:** Extend the FEAT-008 reactor pipeline with the wake-dispatch step at the canonical position, and prove the entire FEAT works end-to-end with the throwaway test agent.

- **T-233**: Reactor pipeline gains `_wake_dispatch` at the position `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`. `RunSupervisor` threaded into `handle_transition`. Wake is no-op on no-match / already-terminal — the race covered in §9 of the brief is handled by design.
- **T-236**: Throwaway `test-agent@0.1.0` with one engine-bound node reaches terminal end-to-end against a respx-stubbed engine. Includes the deliberate-ordering-inversion variant.
- **T-238**: Existing `lifecycle-agent@0.1.0` LLM-policy + engine integration suite passes unchanged — the regression bar.

**Load-bearing decision in this PR:** *Where does `correlation_id` live on `Dispatch`?* The design doc (T-230) recommends carrying it in `Dispatch.intake` JSONB — no new column, no schema migration, the outbox row is the durable source of truth and the dispatch row carries the correlation only for in-process lookups. Land this decision in T-230's PR (PR 1) so reviewers in T-231 and T-233 can pattern-match instead of relitigating.

**This is the irreversible PR.** Once the reactor wakes engine dispatches, downstream agents (FEAT-011) start depending on the wake hook; reverting it after FEAT-011 lands is a much larger surgery. The mitigation: T-238 lives in this PR (not deferred), so the v0.1.0 baseline is asserted *before* any FEAT-011 work depends on the new pipeline shape.

**Reversibility:** Reactor change is one block (`_wake_dispatch` + the call site in `handle_transition`); supervisor threading is local to the route handler. Reverting both restores the pre-FEAT-010 reactor exactly. The throwaway test agent fixture is test-only and can be deleted on revert.

**Acceptance gate:** T-236 passes (engine dispatch reaches terminal; race variant handled); T-238 passes (v0.1.0 unchanged); reactor pipeline ordering test (subtask of T-233) passes; full suite green.

---

### PR 3 — Operational: reconciler + closing docs sweep (T-235, T-240)

**Goal:** Close the restart-safety gap and finalize the docs.

- **T-235**: `reconcile_orphan_dispatches` extended for `mode="engine"` — queries the engine for the entity's current state, materializes the aux row if the transition occurred, marks the dispatch `failed` either way (run owner is gone). New `uv run orchestrator reconcile-dispatches` CLI command (companion to FEAT-008's `reconcile-aux`). Integration tests for both the transition-occurred and transition-unconfirmed branches.
- **T-240**: Docs realignment — `CLAUDE.md`, `ARCHITECTURE.md`, `api-spec.md`, `data-model.md` (note only). Reactor pipeline ordering line in `CLAUDE.md` matches the implementation.

**Load-bearing decision in this PR:** *Does `FlowEngineLifecycleClient.get_item_state` exist?* The implementation plan for T-235 (generated at task pickup) should verify this against the current client. If absent, add a thin read-only wrapper backed by the existing engine read API — adding a new engine-side endpoint is out of scope per the brief constraint "must not modify carestechs-flow-engine to simplify agent behavior." If even the read API is absent, fall back to the conservative branch only (mark `failed` with `detail="orchestrator_restart_engine_unconfirmed"`) and file a follow-on bug.

**Reversibility:** Reconciler extension is additive — non-engine modes preserve FEAT-009's conservative-cancel behavior bit-for-bit. CLI command is additive. Doc updates are doc updates.

**Acceptance gate:** Reconciler integration tests green (both branches); CLI dry-run produces the expected action plan; no doc reference to engine-bound producer logic surviving without a path through `EngineExecutor`; FEAT-010 status flipped to `Completed` in `docs/work-items/`.

---

## Load-bearing decisions in one table

| Decision | Made in | Choice | Why |
|---|---|---|---|
| New executor mode literal | T-231 | Extend `ExecutorMode` to include `"engine"` | Mirrors how `local`/`remote`/`human` are modeled; no parallel taxonomy |
| `correlation_id` placement on `Dispatch` | T-230 + T-231 | Carry in `Dispatch.intake` JSONB; outbox is durable source of truth | No schema migration; correlation is fundamentally an outbox concern, dispatch only needs it for in-process wake lookup |
| Reactor pipeline order at the wake point | T-230 + T-233 | `materialize aux → consume correlation → fire effectors → wake dispatch → fire derivations` | Effectors before wake so resumed runtime sees effector-derived state; derivations after wake so the runtime advances on the originating transition's outcome, not a derived one |
| Reconciler reach into engine | T-235 | Use a thin `get_item_state` wrapper if needed; do not add new engine-side endpoints | Brief constraint: do not modify the flow engine to simplify agent behavior |
| Module-scope import of `FlowEngineLifecycleClient` in `executors/engine.py` | T-231 + T-237 | Forbidden — only `TYPE_CHECKING` import; client supplied via constructor | Preserves FEAT-009 import quarantine for the deterministic runtime |
| Engine-absent dev mode | T-232 | `register_engine_executor` raises clear error if `lifecycle_client` is `None` | Misconfiguration surfaces at boot, not at first dispatch; engine-absent agents must use `no_executor` exemption |

---

## Risks and mitigations

- **T-233 races: webhook arrives before dispatch row commits.** Mitigation: T-233's wake step no-ops on no-match; T-236 includes a deliberate ordering-inversion variant that asserts the runtime advances anyway via the materialized aux row on the next iteration. The race is *expected* — the design accommodates it rather than papering over it.
- **`FlowEngineLifecycleClient.get_item_state` may not exist.** Mitigation: T-235's PR description must verify this on pickup. If absent, the fallback is to scope the reconciler to the conservative-cancel branch only — every orphan engine dispatch is marked `failed` with `detail="orchestrator_restart_engine_unconfirmed"` and the outbox row remains for a future retry. File a follow-on bug to add the read API later.
- **`engine_run_id` field in trace may not be available from the lifecycle client.** Mitigation: T-234 makes the field optional in engine-mode trace entries; if the client doesn't surface a run id, the trace omits it and the operator falls back to joining via correlation id (which is always present).
- **PR 2 regresses v0.1.0.** Mitigation: T-238 lives in PR 2 (not deferred), so the v0.1.0 baseline is asserted in the same PR that ships the reactor change. If T-238 breaks in PR 2, the bug is in T-233's pipeline ordering or supervisor threading, not in the v0.1.0 surface.
- **Import quarantine creeps via `executors/__init__.py`.** Mitigation: T-237 asserts `runtime_deterministic.py` does not pull `engine_client` or `httpx` transitively. If a future PR triggers the test, the right fix is a lazy local import inside `EngineExecutor.dispatch`, not weakening the test.

---

## Cross-PR conventions

- Every PR description references FEAT-010 + the specific task IDs landing in that PR + a one-line note on what's reversible vs not.
- Each PR's commit message follows Conventional Commits with a `feat(FEAT-010):` prefix (PRs 1–3) or `docs(FEAT-010):` (PR 1 design doc, PR 3 docs sweep).
- The `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer on every commit, per CLAUDE.md.
- Doc updates land in the same PR as the code that prompts them — the trace-shape note (T-234) lands in PR 1; the reactor pipeline note (T-240) lands with the reconciler in PR 3 because the pipeline order itself only stabilizes after T-233 + T-235 are both in.

---

## When this is done

- A `flow.policy: deterministic` agent can declare an engine-bound node, register an `EngineExecutor` against it, and the runtime drives the engine's authoritative work-item or task state machine without any new persistence surface or new webhook endpoint.
- The reactor pipeline is the canonical: `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`.
- A crash mid-dispatch is recovered by the reconciler at lifespan startup; no orphan dispatch leaks, no aux-row write is silently dropped.
- `lifecycle-agent@0.1.0` (LLM-policy) continues to drive the engine via the FEAT-008 inline path, unchanged. v0.2.0 (FEAT-009 demo) has no engine-bound nodes and is unaffected.
- The structural test prevents quietly regressing the import-quarantine property for the deterministic runtime.
- FEAT-011 (deterministic lifecycle port) can start: the seam it depends on is in place, exercised end-to-end, and operationally backed by a reconciler.

That's the principal-objective alignment FEAT-010 was filed to land.
