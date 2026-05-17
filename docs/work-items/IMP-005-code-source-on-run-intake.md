# Improvement Proposal: IMP-005 — Code source (repo + branch) on run intake

> **Purpose**: Give every executor a stable handle to the codebase the run is operating against. Today the lifecycle agent knows *what* to build (the work-item brief) but not *where* — there is no field anywhere on `Run`, `LifecycleMemory`, or `DispatchContext` that names a GitHub repo or branch. Executors that read existing code (`generate_plan`, `review_implementation`) infer it implicitly, and any future executor that needs to create a branch, apply a patch, or run tests has no anchor at all. Extend the run-intake schema with a required `codeSource` block (`repo` + `baseBranch`, optional `workBranch`), persist it on the existing `Run.intake` JSONB column, and surface it to every executor via the existing `DispatchContext.intake`.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-005 |
| **Name** | Code source (repo + branch) on run intake |
| **Type** | Feature gap · Contract completeness |
| **Status** | Proposed |
| **Priority** | Medium |
| **Proposed By** | Operator review during IMP-004 planning, 2026-05-17 — noted that adding executor checkpoints exposes the absence of a code-source anchor in the intake contract |
| **Date Created** | 2026-05-17 |

---

## 2. Target Area

**Component / Module:** `modules/ai` — intake schema + executor seam.

**Affected Files / Directories:**
- `src/app/modules/ai/schemas.py` (new `CodeSourceDto`; extend the run-start intake DTO)
- `src/app/modules/ai/service.py` (validate `codeSource` on `start_run`; legacy-intake shim if any)
- `src/app/modules/ai/executors/base.py` (no shape change — `DispatchContext.intake` already carries it)
- `agents/lifecycle-agent@0.3.0.yaml`, `agents/lifecycle-agent@0.4.0-manual.yaml` (extend each agent's `intakeSchema` to require `codeSource`)
- `agents/lifecycle-agent@0.2.0.yaml` (intake schema extended too — the demo agent must round-trip the field even if its executors don't read it)
- `docs/api-spec.md` (intake contract: `POST /api/v1/runs` body documents `codeSource`)
- `docs/data-model.md` (intake payload shape note — no SQL column change)
- `tests/modules/ai/test_service_start_run.py` (validation tests: missing `codeSource`, missing `baseBranch`, malformed `repo`)
- `tests/integration/test_lifecycle_v03.py` and `tests/integration/test_lifecycle_v04_manual.py` (fixtures supply `codeSource`)

**Not affected (out of scope):**
- No new DB column. `Run.intake` is already `JSONB`.
- No new executor type. The producer that *uses* `workBranch` (a future `create_branch` executor or similar) is separate work.
- No orchestrator-level default for repo. Per IMP-005 scope, code source is always a per-run input.

---

## 3. Current State

### How It Works Today

`POST /api/v1/runs` accepts an `intake` block that today carries `workItem` (FEAT-014) and any agent-specific fields. The body is validated against the per-agent `intakeSchema` in the YAML and persisted to `Run.intake` (JSONB). `DispatchContext.intake` exposes it to every executor handler as `Mapping[str, Any]`.

There is **no field anywhere — in `intakeSchema`, in `LifecycleMemory`, in `DispatchContext`, on the `Run` model — that names a code repository or branch.** The `generate_plan` and `review_implementation` LLM executors today operate purely on the work-item brief text plus the in-memory plan/tasks state. They never read source code.

### Problems

1. **Contract gap.** Every agent that does software-lifecycle work *implicitly* targets some repo + branch. Not making that explicit means:
   - The lifecycle agent's outputs (plans, reviews) can't reference real file paths with any guarantee they exist on the operator's branch.
   - There is no way to write an executor that creates a working branch, applies a patch, opens a PR, or runs tests — because none of those have a target.
   - Audit trails can't answer "which branch was this plan written against?"

2. **Implicit scope.** Operators today must assume the orchestrator and the work-item brief share an unstated repo context. Two operators running parallel runs against different repos for the same brief have no way to disambiguate.

3. **Future executors are blocked.** The natural next executors (apply-patch, run-tests, open-PR, post-review-comments) all need a `(repo, branch)` tuple. Without an intake field, each would either invent its own intake key or read from env — both regressions against the FEAT-014 "intake is the upload surface" pattern.

### Evidence

- `src/app/modules/ai/models.py:81` — `Run.intake: JSONB` exists; nothing named "repo" / "branch" / "git" lives elsewhere on the model.
- `src/app/modules/ai/executors/base.py:46` — `DispatchContext.intake: Mapping[str, Any]` is the existing surface.
- `agents/lifecycle-agent@0.3.0.yaml` and `agents/lifecycle-agent@0.4.0-manual.yaml` — `intakeSchema` only requires `workItem`; no `codeSource` key.
- `grep -ri 'repo\|branch\|github' src/app/modules/ai/executors/` — no executor reads either today.

---

## 4. Desired State

### Target Implementation

Extend the run-intake contract with a required `codeSource` block:

```jsonc
// POST /api/v1/runs
{
  "agentRef": "lifecycle-agent@0.4.0-manual",
  "intake": {
    "workItem": { "id": "IMP-042", "kind": "IMP", "content": "..." },
    "codeSource": {
      "repo": "carestechs/carestechs-agent-orchestrator",
      "baseBranch": "main",
      "workBranch": "feat/imp-042"   // optional
    }
  }
}
```

**Validation rules** (Pydantic v2 `CodeSourceDto`):

- `repo`: required, non-empty, must match `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` (GitHub `owner/name` shape — no host prefix, no `.git` suffix; the host is implicit GitHub for v1).
- `baseBranch`: required, non-empty, must satisfy `git check-ref-format --branch` rules (validator: no leading `/`, no `..`, no whitespace, no control chars; full git ref grammar is overkill — reject the common foot-guns and accept the rest).
- `workBranch`: optional, same shape rules as `baseBranch`. If absent on intake, the field is *also* writable to memory by a future producer executor — see "Branch lifecycle ownership" below.

**Persistence:** validated at `service.start_run`, stored verbatim under `Run.intake.codeSource`. No new column.

**Executor surface:** every executor handler reads it via `ctx.intake["codeSource"]`. A thin typed accessor — `from app.modules.ai.executors.code_source import read_code_source(ctx) -> CodeSourceDto` — keeps handler code from passing untyped mappings around.

### Branch lifecycle ownership

The locked-in semantics for `workBranch`:

- **Operator-supplied wins.** If the intake carries `workBranch`, every executor reads it and no producer ever overwrites it.
- **Otherwise an executor fills it.** A future `create_branch` executor (or whatever the first concrete producer is — out of scope for this IMP) writes the branch name into a top-level memory sidecar — `RunMemory.data["codeSource"]["workBranch"]` — using the same `_patch_*` builder pattern that backed IMP-004's `assignments` sidecar.
- **Read order at any consumer:** memory-sidecar value (if present) → intake value (if present) → raise. The accessor `read_code_source(ctx, memory=...)` encodes this precedence in one place so no executor reimplements it.

This preserves the "intake = immutable inputs, memory = run-derived" split established by FEAT-014.

### Updated flow

```
operator → POST /api/v1/runs {intake.codeSource}
              ↓
         Run.intake (JSONB)
              ↓
         DispatchContext.intake["codeSource"]   ← every executor sees this
              ↓
         (future) create_branch executor → memory.codeSource.workBranch
              ↓
         (future) apply_patch / run_tests / open_pr executors
```

### Benefits

1. **Closes the contract gap** — `(repo, baseBranch)` is now an explicit input, not an unstated assumption.
2. **Unblocks the next wave of executors.** Patch-apply, test-run, PR-open, review-comment executors all have the anchor they need.
3. **Zero migration.** Reuses `Run.intake` JSONB and `DispatchContext.intake`.
4. **Forward-compatible with multi-repo work items.** If a brief ever spans two repos, the field grows to a `codeSources: [...]` array; v1 ships the singular form.
5. **Audit trail.** Every run's trace now ties to a concrete branch — answering "which code did this plan target?" by reading `Run.intake.codeSource.workBranch || baseBranch`.

---

## 5. Trigger and Motivation

**Trigger:** During IMP-004 planning on 2026-05-17, surfaced the question "where do executors get the code context they need?" Confirmed that no such field exists. Both decisions on the table — orchestrator-level config vs. per-run intake — were considered; per-run intake won because (a) the orchestrator has no current notion of a single-repo identity, (b) per-run keeps the surface symmetric with `workItem`, and (c) multi-repo runs become a future array-of-`codeSource`s rather than a config migration.

**Impact if deferred:**
- Every future code-touching executor is blocked or invents its own intake key.
- Plans and reviews continue to ship without a verifiable branch handle in their trace.
- The first executor that *does* need this will silently regrow the FEAT-014 anti-pattern (reading from a filesystem path or env var instead of intake).

**Dependencies on this improvement:**
- Future `create_branch` executor (writes `workBranch` to memory).
- Future patch-apply / test-run / PR-open / review-comment executors.
- Possible future engine workflow extension that carries branch identity into engine state.

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `POST /api/v1/runs` intake DTO | New required `codeSource` field on `intake` | `docs/api-spec.md` — Runs |
| `CodeSourceDto` (new Pydantic model) | `repo`, `baseBranch`, optional `workBranch` with format validators | `src/app/modules/ai/schemas.py` |
| `Run.intake` (JSONB column) | New persisted key `codeSource` — no schema change | `src/app/modules/ai/models.py` (no edit) |
| `DispatchContext.intake` | No shape change; new well-known key surfaces through the existing mapping | `src/app/modules/ai/executors/base.py` (no edit) |
| `read_code_source` accessor (new) | Reads memory sidecar → intake → raises; returns typed `CodeSourceDto` | `src/app/modules/ai/executors/code_source.py` (new module) |
| `intakeSchema` in each agent YAML | Adds `codeSource` to `required` for v0.3.0 and v0.4.0-manual; v0.2.0 keeps it optional (demo agent) | `agents/lifecycle-agent@*.yaml` |
| `LifecycleMemory` | No model change; the optional sidecar lives on top-level `RunMemory.data["codeSource"]` — same pattern as `assignments` (IMP-004) and `plans` (BUG-013) | `src/app/modules/ai/lifecycle/memory.py` (no edit) |
| Trace surface | No new trace kind — intake is already echoed at run-start | unchanged |

No SQL migration, no new endpoint, no new background job.

---

## 7. Risk Assessment

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Existing operators with scripted `POST /api/v1/runs` calls break when `codeSource` becomes required | High | Medium | Ship with a `LIFECYCLE_CODE_SOURCE_REQUIRED` setting defaulting to `false` for one minor release, flip to `true` in the next. Document the transition in CLAUDE.md and the api-spec changelog. |
| Operator supplies a malformed `repo` (`https://github.com/...` or trailing `.git`) | High | Low | Pydantic validator with a clear error message naming the expected `owner/name` shape. Reject at the route. |
| `workBranch` operator-supplied vs. memory-written race | Low | Medium | Read order is fixed: memory → intake → raise. Operator-supplied path never reads memory; producer path only writes memory if intake omitted the field. No race because the read precedence is deterministic. |
| Multi-repo work item appears before v1 ships | Low | Low | Singular `codeSource` is forward-compatible — a future `codeSources: []` array can deprecate the singular form. No schema migration on the singular field meanwhile. |
| Executor accidentally writes to `Run.intake` (mutating an "immutable input") | Medium | Medium | Add a structural test asserting no executor calls `Run.intake.__setitem__` or otherwise touches the field. Pattern lives in tests/test_executors_dont_read_briefs.py — extend with a sibling guard. |
| GitHub host assumption locks out GitLab/self-hosted later | Medium | Low | The validator accepts `owner/name`; the *interpretation* as GitHub is per-executor. A future executor for GitLab can read the same field and resolve it differently. If multi-host becomes real, add `host: "github" | "gitlab"` as an optional discriminator — additive, non-breaking. |
| Memory sidecar bloats `RunMemory.data` over many runs | Low | Low | `codeSource` sidecar is ≤200 bytes; no consequence. |

### Rollback Strategy

Three files revert: `schemas.py` (drop `CodeSourceDto`), the affected YAMLs (remove `codeSource` from `intakeSchema`), and `api-spec.md`. Any in-flight run with `codeSource` already persisted on `Run.intake` is harmless — the field becomes orphan data, no executor reads it post-rollback.

---

## 8. Constraints

- **No new DB column.** `Run.intake` is already JSONB; the field must live there.
- **No orchestrator-level default for repo.** Code source is always a per-run input (locked decision — see §5).
- **Operator-supplied `workBranch` is authoritative.** A producer executor MUST NOT overwrite an intake-supplied `workBranch`. Read precedence is fixed: memory sidecar → intake → raise.
- **Validation is shape-only.** The orchestrator never `git fetch`es or otherwise verifies the repo/branch exists at intake time. That's an executor's job (e.g. a future `verify_repo_access` step). Shape validation prevents the easy mistakes; live verification is out of scope.
- **Pattern fidelity.** The optional memory sidecar uses the same `RunMemory.data["codeSource"]` top-level placement as `assignments` (IMP-004) and `plans` (BUG-013) — no nesting under `lifecycle.v1`.
- **Backwards-compat window.** One minor release with `LIFECYCLE_CODE_SOURCE_REQUIRED=false` (warns), then flip to `true`. Existing scripted callers get a deprecation window.
- **No coupling to GitHub APIs in this IMP.** The field exists; calls to the GitHub API live in future executors.

---

## 9. Success Criteria

- `POST /api/v1/runs` with a well-formed `intake.codeSource` succeeds and `GET /api/v1/runs/{id}` returns the field on `intake`.
- `POST /api/v1/runs` with missing `codeSource` (under the strict setting) returns 400 RFC-7807 with `code=intake-validation-failed` and a field-path pointing at `codeSource`.
- `POST /api/v1/runs` with `repo="https://github.com/foo/bar.git"` returns 400 with a message naming the expected `owner/name` shape.
- A unit test on a sample executor handler demonstrates `read_code_source(ctx)` returns the typed DTO and `read_code_source(ctx, memory=mem_with_workbranch)` returns the memory-overridden value.
- `agents/lifecycle-agent@0.3.0.yaml` and `agents/lifecycle-agent@0.4.0-manual.yaml` require `codeSource` in their `intakeSchema`; the executor coverage validator boots cleanly with no behavioral change to existing nodes (they just have a richer intake).
- Existing FEAT-015 / T-302 and FEAT-011 integration tests are updated to supply `codeSource` in their run-start fixtures and continue to pass.
- `docs/api-spec.md` documents `CodeSourceDto` and its validation rules; changelog entry references IMP-005.
- A new structural test guards that no executor writes to `Run.intake`.

---

## 10. Current Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| Intake validation | Unit | FEAT-014 introduced `WorkItemIntakeDto` with shape validation; the new `CodeSourceDto` follows the same pattern and gets equivalent unit coverage. |
| `DispatchContext.intake` access | Indirect | Existing executor tests touch `ctx.intake["workItem"]`; the new accessor needs its own unit tests for the memory-sidecar precedence. |
| Run-start round-trip | Integration | FEAT-014 covers intake → DB → `GET /runs/{id}`; extend to assert `codeSource` round-trips. |
| Backwards-compat warning | Unit | New — when `LIFECYCLE_CODE_SOURCE_REQUIRED=false`, an intake without `codeSource` logs a deprecation warning and accepts the run. |

Gap: no test today exercises any field analogous to `codeSource`. This IMP introduces the pattern.

---

## 11. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | Planning discussion during IMP-004 implementation, 2026-05-17 — recognized the absence of a code-source anchor in executor contracts |
| **Stakeholder Alignment** | Self-delivery discipline (AD-6) — the orchestrator's own runs against this repo need a stable code-source handle once executors do real software-lifecycle work |
| **Architecture Reference** | `docs/ARCHITECTURE.md` — Executor seam (FEAT-009); `CLAUDE.md` — "Work-item bodies live in the DB" + "intake is the upload surface" patterns from FEAT-014 |
| **Related Work Items** | FEAT-014 (intake-as-upload-surface; this IMP follows the same pattern), IMP-004 (top-level memory sidecar precedent), BUG-013 (per-task sidecars precedent) |
| **Blocked Features** | `create_branch` executor; apply-patch executor; run-tests executor; open-PR executor; review-comment executor; engine workflow extension carrying branch identity |
