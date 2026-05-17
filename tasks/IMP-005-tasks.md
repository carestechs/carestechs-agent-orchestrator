# IMP-005 — Task Breakdown

> **Source brief:** `docs/work-items/IMP-005-code-source-on-run-intake.md`
> **Numbering:** Continues from IMP-004 (last task T-310).
> **Total tasks:** 6
> **Critical path:** T-311 → T-313 → T-315 → T-316 (4-step chain)

This IMP adds a `codeSource` block to the run-intake contract — `{ repo, baseBranch, workBranch? }` — persisted on the existing `Run.intake` JSONB and surfaced to every executor via `DispatchContext.intake`. Zero DB migration. Shape-only validation; no live `git fetch`. The optional `workBranch` follows operator-supplied-wins → memory-sidecar fallback semantics, encoded centrally in one accessor.

---

## Phase 0: Preparation

(No precondition coverage tasks. The pattern is symmetric with `WorkItemIntakeDto` from FEAT-014 — that test layout is the structural baseline this IMP extends.)

---

## Phase 1: Parallel Implementation

### T-311: Add `CodeSourceDto` schema with format validators

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add `CodeSourceDto` to `src/app/modules/ai/schemas.py` as a Pydantic v2 model with `model_config = _CAMEL_CONFIG` (existing module convention) plus `extra="forbid"`. Fields:
- `repo: str` — required; `field_validator` rejects empty, whitespace, or anything not matching `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` (GitHub `owner/name` shape — no host prefix, no `.git` suffix).
- `base_branch: str` (alias `baseBranch`) — required; validator rejects empty/whitespace, leading `/`, `..`, ASCII control chars, and whitespace inside the ref.
- `work_branch: str | None = None` (alias `workBranch`) — optional; same shape rules as `base_branch` when present.

Then extend `IntakeDto` (or the equivalent run-start intake model, wherever `workItem` is declared) with `code_source: CodeSourceDto | None = None`. Required-ness is enforced at the `service.start_run` layer in T-313, not on the DTO — that lets us keep one DTO across the deprecation window.

**Rationale:**
The validators reject the common foot-guns (`https://...`, trailing `.git`, branch names with whitespace) at the route boundary with one clear error message. The full git ref grammar is overkill — accept the common shape and let downstream `git` calls catch the rest.

**Acceptance Criteria:**
- [ ] `CodeSourceDto(repo="org/name", baseBranch="main")` validates; `work_branch is None`.
- [ ] `CodeSourceDto(repo="https://github.com/org/name", baseBranch="main")` raises `ValidationError` with a message naming the expected `owner/name` shape.
- [ ] `CodeSourceDto(repo="org/name.git", baseBranch="main")` raises (the `.git` suffix matches the regex if not careful — write the test to lock the rejection).
- [ ] `CodeSourceDto(repo="org/name", baseBranch=" main ")` raises (whitespace).
- [ ] `CodeSourceDto(repo="org/name", baseBranch="main", workBranch="feat/x")` validates; camelCase alias round-trips.
- [ ] `CodeSourceDto(repo="org/name", baseBranch="main", workBranch="../escape")` raises.
- [ ] `model_dump(by_alias=True)` produces camelCase keys.
- [ ] Extra fields rejected (`extra="forbid"`).
- [ ] `pyright` clean.

**Files to Modify/Create:**
- `src/app/modules/ai/schemas.py` — add `CodeSourceDto`; extend the run-start intake DTO with the optional field.

**Technical Notes:**
- Branch validator: a focused validator covering `not value.strip()`, `value.startswith("/")`, `".." in value`, `any(ch.isspace() for ch in value)`, `any(ord(ch) < 0x20 for ch in value)`. Keep the regex out of branch validation — git ref grammar is too quirky for one regex.
- The DTO is independent of the YAML `intakeSchema`. The YAML edit lands in T-314.

---

### T-312: Add `read_code_source` accessor with memory-sidecar precedence

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-311

**Description:**
Create `src/app/modules/ai/executors/code_source.py` exporting `read_code_source(ctx: DispatchContext, memory: Mapping[str, Any] | None = None) -> CodeSourceDto`. Precedence:

1. If `memory` is provided and `memory.get("codeSource", {}).get("workBranch")` is non-empty, that value overrides the intake's `workBranch`.
2. Read base values from `ctx.intake["codeSource"]` and validate via `CodeSourceDto.model_validate(...)`.
3. If a memory-supplied `workBranch` was found in step 1, override the DTO field via `model_copy(update={"work_branch": ...})`.
4. If `ctx.intake` carries no `codeSource` key, raise `ValueError("codeSource missing from intake")`.

Pure function, no I/O, no logging.

**Rationale:**
Centralizing the read precedence in one place keeps every future executor honest. Without this accessor, each consumer would reimplement the merge and we'd grow drift the first time a producer executor lands.

**Acceptance Criteria:**
- [ ] `read_code_source(ctx)` returns the intake's `CodeSourceDto` when no `memory` is passed.
- [ ] `read_code_source(ctx, memory={"codeSource": {"workBranch": "feat/x"}})` returns a DTO with `work_branch="feat/x"` even if the intake's `workBranch` is None.
- [ ] Operator-supplied intake `workBranch` is **not** overwritten when memory carries no `workBranch` (read order is checked, not blind merge).
- [ ] Memory passing `{"codeSource": {"workBranch": None}}` does not override an intake-supplied `workBranch`.
- [ ] Missing intake `codeSource` raises `ValueError`.
- [ ] Pure function — no side effects, no mutation of `ctx` or `memory`.
- [ ] `pyright` clean.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/code_source.py` — new module, ≤30 lines.
- Unit tests live in T-315.

**Technical Notes:**
- The accessor is the only sanctioned read path. Any executor calling `ctx.intake["codeSource"]` directly bypasses the memory precedence and is a review blocker.
- `model_copy(update=...)` returns a fresh DTO — preserves the "no mutation" guarantee.

---

### T-313: Wire intake validation + `LIFECYCLE_CODE_SOURCE_REQUIRED` setting into `start_run`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-311

**Description:**
Add `lifecycle_code_source_required: bool = False` to `src/app/config.py` (env var `LIFECYCLE_CODE_SOURCE_REQUIRED`). In `src/app/modules/ai/service.py::start_run`, after the existing intake parsing:

- If `intake.code_source is None` and the setting is `True`, raise `ValidationError(code="intake-validation-failed", detail="codeSource is required")`.
- If `intake.code_source is None` and the setting is `False`, emit one structured `logger.warning` with `run_id` (about to be assigned), `agent_ref`, and the message `"intake.codeSource missing — falling back to deprecation window; flip LIFECYCLE_CODE_SOURCE_REQUIRED=true to enforce"`.
- If `intake.code_source` is present, no extra logic — it's already validated by the DTO.

The persisted `Run.intake` JSONB carries `codeSource` verbatim when present; absent under the false setting, the field simply isn't there.

**Rationale:**
One-minor-release deprecation window keeps scripted callers from breaking the day this lands. Logging on the soft-path turns silent drift into visible drift.

**Acceptance Criteria:**
- [ ] `LIFECYCLE_CODE_SOURCE_REQUIRED=false` (default): run starts succeed with or without `codeSource`; the no-`codeSource` path emits a deprecation warning.
- [ ] `LIFECYCLE_CODE_SOURCE_REQUIRED=true`: missing `codeSource` returns 400 RFC-7807 with `code=intake-validation-failed`; the body's `detail` mentions `codeSource`.
- [ ] When `codeSource` is present, the value is persisted to `Run.intake` and round-trips through `GET /api/v1/runs/{id}`.
- [ ] Bad-shape `codeSource` (e.g. malformed `repo`) returns 400 with a `ValidationError`-derived Problem Details body regardless of the setting (that path is the DTO validator, not the service flag).
- [ ] Setting reachable via `app.config.get_settings()` — no direct `os.environ` reads in service code.

**Files to Modify/Create:**
- `src/app/config.py` — new setting.
- `src/app/modules/ai/service.py` — branching in `start_run`.

**Technical Notes:**
- The warning fires before the run id is materialized — log on a synthetic key (`agent_ref` + truncated brief id) rather than `run_id`. Don't block to assign an id just for the log line.
- Existing `ValidationError` mapping in `core/exceptions.py` already maps to 400 RFC-7807 — no new exception type needed.

---

### T-314: Extend agent YAML `intakeSchema` for v0.3.0 and v0.4.0-manual

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None (parallelizable with T-311/T-312/T-313)

**Description:**
Edit `agents/lifecycle-agent@0.3.0.yaml` and `agents/lifecycle-agent@0.4.0-manual.yaml` to extend the existing `intakeSchema` with a `codeSource` object property:

```yaml
codeSource:
  type: object
  required: [repo, baseBranch]
  additionalProperties: false
  properties:
    repo:        {type: string, pattern: "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"}
    baseBranch:  {type: string, minLength: 1}
    workBranch:  {type: string, minLength: 1}
```

Add `codeSource` to the top-level `required:` array. Leave `agents/lifecycle-agent@0.2.0.yaml` and `agents/lifecycle-agent@0.1.0.yaml` untouched — the legacy demo agents don't need this field for their out-of-scope flows.

Update the top-of-file header comment on each edited YAML to mention `codeSource` as a required intake input alongside `workItem`.

**Rationale:**
The YAML `intakeSchema` is the agent-level contract; the Pydantic DTO is the orchestrator-level contract. Both must align. The schema-level constraint is permissive (no whitespace/control-char checks — that's the DTO's job); jsonschema validates at the agent loader and Pydantic re-validates at the service. Double-validation is by design — the agent loader is allowed to be looser as long as the DTO is the tighter floor.

**Acceptance Criteria:**
- [ ] `lifecycle-agent@0.3.0.yaml` and `lifecycle-agent@0.4.0-manual.yaml` `intakeSchema` blocks include `codeSource` with the shape above.
- [ ] Both YAMLs have `codeSource` in `required:`.
- [ ] `lifecycle-agent@0.2.0.yaml` and `lifecycle-agent@0.1.0.yaml` are byte-unchanged (`git diff agents/lifecycle-agent@0.{1,2}.0.yaml`).
- [ ] Header comment on edited YAMLs lists `codeSource` as a required intake field.
- [ ] `uv run uvicorn app.main:app` boots cleanly.

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.3.0.yaml` — additive `intakeSchema` edit + header comment.
- `agents/lifecycle-agent@0.4.0-manual.yaml` — same.

**Technical Notes:**
- The YAML `required: [codeSource]` makes the agent loader strict regardless of the orchestrator-level deprecation flag. This means: during the deprecation window, the orchestrator setting controls whether the *DTO* enforcement fires, but the YAML enforcement is always on. That's intentional — operators can't script around the YAML contract; the flag is only for the orchestrator-level migration.
- Actually — re-read: if the YAML is always-strict, the orchestrator flag becomes redundant. **Decision for the plan:** the YAML's `required:` lists `codeSource` only when the orchestrator setting flips to `true`. In the deprecation window, the YAML keeps `codeSource` *defined* but not in `required:`. Lock this in T-314's plan.

---

## Phase 2: Migration

(No data migration. The orchestrator-level deprecation flag is the only migration tool. Existing in-flight runs persist without `codeSource`; new runs grow the field. Once `LIFECYCLE_CODE_SOURCE_REQUIRED=true` flips on, the YAML's `required:` array is updated in tandem — a one-line follow-on PR after the deprecation window closes.)

---

## Phase 3: Verification

### T-315: Unit + integration tests (DTO, accessor, service validation, fixture updates, structural guard)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-311, T-312, T-313, T-314

**Description:**
Add four test surfaces:

1. **DTO tests** — `tests/modules/ai/test_schemas_code_source.py` (new): covers every AC bullet in T-311 (happy path, URL rejection, `.git` rejection, whitespace rejection, escape sequence rejection, camelCase round-trip, extra-fields rejection).
2. **Accessor tests** — `tests/modules/ai/executors/test_code_source_accessor.py` (new): covers every AC bullet in T-312 (precedence ordering, no-mutation, missing intake raises).
3. **Service validation tests** — extend `tests/modules/ai/test_service_start_run.py` (or equivalent) with: required-true rejects missing, required-false logs warning + accepts, malformed `repo` rejected regardless of setting, valid `codeSource` round-trips to `Run.intake`.
4. **Integration fixture updates** — `tests/integration/test_lifecycle_v04_manual.py` and `tests/integration/test_lifecycle_v03.py` (or the canonical integration tests for those agents): add `codeSource` to every `POST /api/v1/runs` fixture body. Tests must continue to pass — this is a non-behavioral fixture update.
5. **Structural guard** — extend `tests/test_executors_dont_read_briefs.py` (or add a sibling) with an AST-walk test asserting no executor module assigns to `ctx.intake` or `run.intake` (mutation guard).

**Rationale:**
DTO + accessor unit tests + service-layer integration tests + a structural guard cover every angle the IMP can break in. Fixture updates keep existing FEAT-015 / FEAT-011 coverage from regressing on the new required field.

**Acceptance Criteria:**
- [ ] All new tests pass.
- [ ] `uv run pytest tests/modules/ai/ tests/integration/` passes end-to-end.
- [ ] The structural guard catches a deliberate `ctx.intake["codeSource"] = ...` mutation (verify locally by inserting then reverting).
- [ ] No `# type: ignore` introduced.
- [ ] Integration fixture updates touch no production code.

**Files to Modify/Create:**
- `tests/modules/ai/test_schemas_code_source.py` — new.
- `tests/modules/ai/executors/test_code_source_accessor.py` — new.
- `tests/modules/ai/test_service_start_run.py` — extend.
- `tests/integration/test_lifecycle_v04_manual.py` — fixture update.
- `tests/integration/test_lifecycle_v03.py` — fixture update.
- `tests/test_executors_dont_read_briefs.py` — extend (or add sibling test).

**Technical Notes:**
- The structural guard parses each executor module with `ast` and walks for `Subscript` assignments to attribute chains ending in `.intake`. Reuse the visitor pattern from the existing brief-read guard if it walks AST already; otherwise write a small one (~20 lines).

---

### T-316: Docs — `docs/api-spec.md`, `docs/data-model.md` note, `CLAUDE.md` patterns

**Type:** Docs
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-313, T-314

**Description:**
Three doc edits:

1. **`docs/api-spec.md`** — extend the `POST /api/v1/runs` body schema with `intake.codeSource`; document `CodeSourceDto` (fields, validation rules, examples). Add a changelog entry dated 2026-05-17 referencing IMP-005 and noting the deprecation window via `LIFECYCLE_CODE_SOURCE_REQUIRED`.
2. **`docs/data-model.md`** — add a one-line note under `Run.intake` describing `codeSource` as a well-known intake key (no SQL change). Add changelog entry.
3. **`CLAUDE.md`** — add a new pattern bullet in "Patterns to Follow": **"Code source lives on intake, read through `read_code_source`."** Two-three sentences: where the field lives, why operator-supplied `workBranch` wins, the read precedence rule.

Also add the env var `LIFECYCLE_CODE_SOURCE_REQUIRED` to the Quick Reference / config docs section if there's one.

**Rationale:**
Doc-first discipline (per CLAUDE.md Documentation Maintenance table). Without the CLAUDE.md bullet, the next agent author will read `ctx.intake["codeSource"]` directly and bypass the accessor.

**Acceptance Criteria:**
- [ ] `docs/api-spec.md` documents `CodeSourceDto` with field shapes + validation rules + an example payload. Changelog entry present.
- [ ] `docs/data-model.md` notes `codeSource` as a well-known `Run.intake` key. Changelog entry present.
- [ ] `CLAUDE.md` Patterns section has a new bullet titled "Code source lives on intake".
- [ ] `LIFECYCLE_CODE_SOURCE_REQUIRED` mentioned in the relevant config doc surface.
- [ ] No edits to `docs/ARCHITECTURE.md` (no new component).
- [ ] No edits to `docs/ui-specification.md` (no UI in v1).

**Files to Modify/Create:**
- `docs/api-spec.md` — body schema + changelog.
- `docs/data-model.md` — note + changelog.
- `CLAUDE.md` — new pattern bullet.

---

## Summary

| Phase | Tasks | Notes |
|-------|-------|-------|
| 0 — Preparation | — | FEAT-014's `WorkItemIntakeDto` test layout is the baseline. |
| 1 — Implementation | T-311, T-312, T-313, T-314 | Schema → accessor → service wiring → YAML extension. T-311/T-314 are independent; T-312/T-313 wait on T-311. |
| 2 — Migration | — | Deprecation flag is the only migration tool; flip when ready. |
| 3 — Verification | T-315, T-316 | Combined test surface + docs. |

**Critical path:** T-311 → T-313 → T-315 → T-316 (4 steps). T-312 + T-314 parallelize with T-313.

**Risk assessment:** Low. The change is one DTO + one accessor + one service branch + two YAML edits, all following established FEAT-014 patterns. The non-obvious decision (operator-supplied `workBranch` precedence) is locked in one accessor.

**Recommended review points:**
- After T-311: review the branch-name validators — git ref rules are subtle; check the edge cases match the AC list.
- After T-313: verify the deprecation warning fires exactly once per run (not per loop iteration).
- After T-315: smoke-run `uv run pytest tests/integration/` to confirm no FEAT-015 / FEAT-011 regression.

**Rollback strategy:** Revert three files (`schemas.py`, the two YAMLs) + the new `code_source.py` accessor module + new tests. Any in-flight runs with `codeSource` on `Run.intake` are orphan data — harmless. Setting `LIFECYCLE_CODE_SOURCE_REQUIRED=false` is the fast rollback if the strict flip causes problems in production.
