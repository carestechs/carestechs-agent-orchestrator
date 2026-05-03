# Improvement Proposal: IMP-003 — Swappable reviewer binding + stub-pass mode

> **Purpose**: Make the `review_implementation` executor binding configurable at bootstrap so deployments can swap the in-process LLM reviewer for (a) a stub-pass binding for smoke / CI, (b) a remote binding pointing at an external review service when one ships, (c) a human binding waiting on a review-decision signal. The smoke unblock (stub-pass) is the immediate driver; the architectural shape it sets up is the load-bearing piece.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-003 |
| **Name** | Swappable reviewer binding + stub-pass mode |
| **Type** | Maintainability · Testability · Developer Experience |
| **Status** | Proposed |
| **Priority** | High (blocks end-to-end smoke against `close_work_item`; sets up the external-reviewer migration) |
| **Proposed By** | Live `lifecycle-agent@0.3.0` smoke run on 2026-05-02 |
| **Date Created** | 2026-05-02 |

---

## 2. Target Area

**Component / Module:** `modules/ai/executors/bootstrap.py` — the `register_lifecycle_v03` reviewer-node wiring + a new env-driven binding selector.

**Affected Files / Directories:**
- `src/app/config.py` — new `LIFECYCLE_REVIEWER` setting.
- `src/app/modules/ai/executors/bootstrap.py` — extract the reviewer-binding registration into a selector function.
- `src/app/modules/ai/executors/stub_reviewer.py` (new) — `StubPassReviewerExecutor` (a thin LocalExecutor adapter that synthesizes `verdict=pass`).
- `tests/modules/ai/executors/test_stub_reviewer.py` (new).
- `docs/data-model.md` (Run / RunMemory — new mention of the reviewer-binding env var if it affects runs).
- `CLAUDE.md` — document the env flag in the runtime section.

---

## 3. Current State

### How It Works Today

`register_lifecycle_v03` (`bootstrap.py:726`) hardcodes `LLMContentExecutor` as the binding for `review_implementation`. That binding wraps a single Anthropic Messages call against a system prompt + a user prompt loaded by `_load_review_context`. In smoke environments the LLM has no real diff/PR to judge against (the operator's signal payload contains synthetic data), so it returns `verdict=fail` reliably and the run loops until `terminate_correction_budget` trips. The smoke proves the budget mechanism but never reaches `close_work_item`.

There is no way to swap the reviewer at deployment time without editing the bootstrap helper.

### Problems

1. **Smoke runs can't reach `close_work_item`.** The terminal-success path of the lifecycle is untested end-to-end. Every smoke run today either hits the corrections-budget path or hangs waiting for an evidence-rich signal payload that doesn't exist in CI.
2. **Future external-reviewer migration has nowhere to land.** When the dedicated review service ships (FEAT-???), it needs to replace the in-process LLM at the same node. Today the only way to do that is a code edit in `bootstrap.py` — there's no deployment-time switch, no test seam, and no binding-selector contract documented anywhere.
3. **The current reviewer's failure mode is also a CI failure mode.** A flaky Anthropic 429, a model deprecation, a prompt regression — all of them turn smoke runs red even when the orchestrator code is correct. CI should be able to bypass the LLM entirely.

### Evidence

- 2026-05-02 live run: every iteration of `review_implementation` returned `verdict=fail` with feedback like "task spec, implementation plan, and git diff for T-001 are all missing from this request." After BUG-010 surfaced the plan + task spec the failure shifted to "no actual diff was provided" — fundamentally unfixable from inside the orchestrator without an external diff fetcher (FEAT-013, separate work).
- `bootstrap.py:726` is a hardcoded `LLMContentExecutor` registration — grep shows no env-driven branch.

---

## 4. Desired State

### Target Implementation

Introduce a `LIFECYCLE_REVIEWER` env var with three valid values:

| Value | Binding registered for `review_implementation` |
|-------|------------------------------------------------|
| `llm-content` | Today's `LLMContentExecutor` (default). Production with the orchestrator-as-reviewer assumption. |
| `stub-pass` | New `StubPassReviewerExecutor` — a `LocalExecutor` whose handler synthesizes `verdict=pass` and writes the same `lifecycle.v1.reviewHistory` entry the LLM path would, with `feedback="stub-pass: smoke / CI shortcut"`. |
| `remote` *(future, deferred)* | `RemoteExecutor` pointing at `LIFECYCLE_REVIEWER_URL` for the external review service. Slot reserved; not implemented in this IMP. |

`register_lifecycle_v03` factors the reviewer registration into a small selector helper that takes the setting and returns a binding. The rest of the bootstrap flow is unchanged.

### Benefits

1. **Smoke runs reach `close_work_item`** under `LIFECYCLE_REVIEWER=stub-pass` — proves the success path end-to-end, complements the existing budget-exhaustion path.
2. **Architectural seam for the future external reviewer is now visible** — the contract is "reviewer is a binding option, not a hardcoded executor." When the real external service lands it slots in as the `remote` value with a URL setting; no further bootstrap restructuring needed.
3. **CI decoupled from Anthropic availability** — smoke runs no longer depend on a live LLM provider.
4. **Testable** — `StubPassReviewerExecutor` has a deterministic handler, so reviewer-path integration tests no longer need to seed a `StubLLMProvider` script.

---

## 5. Trigger and Motivation

**Trigger:** Live smoke run on 2026-05-02 reached `terminate_correction_budget` (proving the budget loop works) but not `close_work_item` (the success terminal). The reviewer LLM returns `verdict=fail` for every smoke task because the operator's signal payload doesn't carry a real PR diff. Without an external diff effector (FEAT-013) the in-process LLM reviewer can't pass smoke runs by construction.

**Impact if deferred:** Every CI / smoke run continues to hit the corrections-budget path; the success terminal remains untested in automation. The first deploy of an external reviewer (when it ships) requires a code edit in `bootstrap.py` rather than a config change.

**Dependencies on this improvement:**
- FEAT-013 (GitHub diff effector) is independent — even after diff fetch lands, smoke environments without real PRs need a non-LLM reviewer.
- Future external-reviewer FEAT (TBD) plugs into the same selector via the `remote` slot reserved here.

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `Settings` (config) | New optional `LIFECYCLE_REVIEWER: Literal["llm-content","stub-pass"] = "llm-content"`. `remote` reserved as a future literal. | `docs/api-spec.md` — env vars section |
| `register_lifecycle_v03` | Reviewer-node registration moves into a selector function `_register_reviewer(registry, setting, ...)` | `CLAUDE.md` — runtime loop notes |
| `StubPassReviewerExecutor` | New `LocalExecutor` subclass / factory; writes the canonical `reviewHistory` entry with `verdict=pass`, `feedback="stub-pass: smoke / CI shortcut"` | — |
| `LIFECYCLE_REVIEWER` env var | Documented in `CLAUDE.md` runtime section + `.env.example` | `CLAUDE.md` |

No data-model change. No migration. The runtime loop is untouched — this is a bootstrap-layer swap.

---

## 7. Risk Assessment

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Stub-pass accidentally enabled in production → every implementation rubber-stamped | Low (env var defaults to `llm-content`) | Critical (compliance / quality) | Lifespan boot logs the active reviewer binding at INFO; production runbook checklist asserts `LIFECYCLE_REVIEWER=llm-content` (or `remote` once it ships). Separate doctor command output flags the value. Future: refuse to boot in `stub-pass` if `ENV=production`. |
| `reviewHistory` entries from stub differ from LLM entries → analytics confusion | Low | Low | Stub writes the same shape; the `feedback` field's `"stub-pass: ..."` prefix is the discriminator. Documented. |
| Selector grows over time → new options become a regex / if-chain | Low | Low | Keep the selector a small dispatch table; refuse unknown values at boot. |
| External reviewer arrives in a shape the `remote` value doesn't fit (e.g. async batch instead of synchronous HTTP) | Medium | Medium | Reserve the `remote` slot; if the real external reviewer needs a different mode (e.g. `human` for an async PR-review queue), add a fourth value. The selector pattern absorbs that without shape change. |

### Rollback Strategy

Single revert. No data shape to undo. Production runs default to `llm-content` even if the new code is present, so a hot-reverse to "old behaviour" is "set or unset the env var."

---

## 8. Constraints

- **Stub must write `reviewHistory` in the canonical shape** (BUG-010's `lifecycle.v1.reviewHistory` + camelCase field names + per-task `attempt` counter). Drift between stub and LLM here would re-fragment the memory readers we just consolidated.
- **Selector lives in bootstrap, not in the runtime.** Runtime stays mode-agnostic; only the binding is selected.
- **No new executor type.** Stub is a `LocalExecutor` with a custom handler — the existing four executor classes cover the design space.
- **Default value is `llm-content`.** No behaviour change for existing deployments that don't set the var.
- **Future-aware but not future-prescriptive.** The `remote` slot is reserved by the literal type but explicitly NOT implemented in this IMP — premature implementation locks in a contract before the external reviewer exists.

---

## 9. Success Criteria

- A smoke run with `LIFECYCLE_REVIEWER=stub-pass` reaches `close_work_item` and ends `status=completed`.
- A smoke run with `LIFECYCLE_REVIEWER=llm-content` (or unset) behaves identically to today's runs — no regression.
- Setting `LIFECYCLE_REVIEWER` to an unknown value refuses boot with a clear `ConfigError`.
- A unit test exercises the stub directly and asserts `reviewHistory` shape matches what `_patch_review` would produce.
- An integration test runs a deterministic flow with `LIFECYCLE_REVIEWER=stub-pass` and confirms the reviewer-pass path advances to `approve_review` (T10).

---

## 10. Current Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| `_patch_review` (LLM path) | Unit | BUG-010 added. |
| Reviewer prompt context loader | Unit | BUG-010 added. |
| End-to-end review-pass path | None | Today's smoke can't pass review without real PR evidence; this IMP unblocks that suite. |
| Bootstrap helper variants | Sparse | `register_lifecycle_v03` has structural tests (coverage validation) but no test for "swap a single node's binding." |

---

## 11. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | Live smoke run on 2026-05-02 (operator session); BUG-010 PR followup discussion |
| **Stakeholder Alignment** | "External review service is the long-term shape; current LLM is bootstrap" — operator-stated direction, 2026-05-02 |
| **Architecture Reference** | `docs/ARCHITECTURE.md` — executor seam (FEAT-009/010); `CLAUDE.md` — runtime loop |
| **Related Work Items** | FEAT-013 (GitHub diff effector — independent; together they make the LLM reviewer competent for real PRs), future FEAT (external review service — slots into the `remote` value reserved here), BUG-010 (canonical `reviewHistory` shape this stub must match) |
| **Blocked Features** | Reviewer-pass smoke coverage; future external-reviewer rollout |
