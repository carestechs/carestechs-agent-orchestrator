# FEAT-014 — Work-item upload (deprecate `workItemPath` filesystem read)

> **Source:** `docs/work-items/FEAT-014-work-item-upload-not-filesystem-read.md`
> **Status:** Not Started
> **Target version:** v0.9.0

FEAT-014 replaces the orchestrator's filesystem read of work-item briefs (`intake.workItemPath = "docs/work-items/FEAT-XXX.md"`) with an upload-then-dedupe-by-id flow: `intake.workItem = {id, kind, content?}`. The body lands in a new `work_items.body_md` column on first sight, is keyed on the existing `external_ref` UNIQUE, and is content-addressed via `body_sha256` so that re-uploads are idempotent and conflicting bodies are rejected with **409**. The CLI reads the file client-side and POSTs the body; the executor seam swaps from disk read to DB read. `workItemPath` stays one minor as a deprecated alias with a WARNING log.

The numbering picks up at **T-282** (FEAT-013 ended at T-281).

---

## Foundation

### T-282: Schema — `work_items.body_md` + `work_items.body_sha256` (forward-only migration)

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add two nullable columns to `work_items` and a forward-only Alembic migration:

- `body_md TEXT NULL` — the raw markdown body as uploaded.
- `body_sha256 TEXT NULL` — hex sha256 of `body_md` bytes-as-received (no normalization). 64 chars, validated via a CHECK constraint `body_sha256 ~ '^[0-9a-f]{64}$'`.

Both columns are NULL-able so pre-FEAT-014 rows (registered with `source_path` only) keep working through the deprecation window. No new index — lookups stay on the existing `external_ref` UNIQUE; `body_sha256` is read-only-by-id, never queried directly.

**Rationale:**
AC-1, AC-3, AC-7 — every other task in this FEAT reads from or writes to these two columns. Lands first so the registration service has a place to write.

**Acceptance Criteria:**
- [ ] `WorkItem` SQLAlchemy model gains `body_md: Mapped[str | None]` and `body_sha256: Mapped[str | None]`; type-checked under strict pyright.
- [ ] Alembic migration applies cleanly on a fresh DB and on a DB already at the current head; downgrade drops both columns (documented as destructive).
- [ ] CHECK constraint on `body_sha256` rejects non-hex / wrong-length values at insert time.
- [ ] `docs/data-model.md` entry for `WorkItem` lists both new columns and the immutability rule.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — extend `WorkItem`.
- `src/app/migrations/versions/2026_05_XX_feat_014_work_item_body.py` — new revision.

**Technical Notes:**
Use `Text` (not `String`) — body length is unbounded by SQL but capped at the route boundary (T-285). CHECK constraint per CLAUDE.md naming: `ck_work_items_body_sha256_format`.

---

### T-283: Typed intake — `RunIntakeWorkItem` Pydantic model + `RunIntake` integration

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add a typed sub-object to the run intake so registration is a real DTO at the boundary, not `dict[str, Any]` access in service code:

```python
class RunIntakeWorkItem(BaseModel):
    id: str           # pattern: ^[A-Z]+-\d+(-[a-z0-9-]+)?$
    kind: WorkItemType  # enum from existing models
    content: str | None = None
```

Wire this into the existing `Run.intake` schema. Add an `intake.workItem` accessor on the run-start DTO that the route uses. **Do not** remove `workItemPath` / `workItemId` / `workItemEngineId` yet — backward-compat in T-288.

**Rationale:**
AC-1..AC-4, AC-7, AC-9 — every downstream task (service, route, executor swap) imports this model. Lands in parallel with T-282 because it has no DB dependency.

**Acceptance Criteria:**
- [ ] `RunIntakeWorkItem` defined in `src/app/modules/ai/schemas.py` with the regex on `id` and a pydantic `field_validator` for length (≤ 64 bytes).
- [ ] `RunStartRequestDto` (or current run-start DTO name) accepts `intake.workItem` as an optional `RunIntakeWorkItem`.
- [ ] camelCase JSON alias on every field (per CLAUDE.md naming table).
- [ ] Unit tests cover: valid shape, malformed `id`, invalid `kind`, oversized `content` rejected at the model boundary.

**Files to Modify/Create:**
- `src/app/modules/ai/schemas.py` — add the model.
- `tests/modules/ai/test_run_intake_schema.py` — new unit tests.

**Technical Notes:**
`content` size limit at the model level can be soft; the hard limit lives at the route (`INTAKE_WORK_ITEM_MAX_BYTES`, T-285).

---

## Backend — service + route

### T-284: Work-item registration service — INSERT-or-reuse-or-409

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-282, T-283

**Description:**
Introduce `register_work_item(session, dto: RunIntakeWorkItem) -> WorkItem` in `src/app/modules/ai/lifecycle/service.py` (or a new `work_item_registry.py` if the file is getting fat). One pure async function with this state machine:

- Compute `incoming_sha = sha256(dto.content.encode("utf-8"))` if content is present.
- `SELECT WorkItem WHERE external_ref = dto.id` (FOR UPDATE not required — UNIQUE handles concurrency).
- Branches:
  - **No row + no content** → raise `WorkItemNotRegisteredError` (typed → 400 at route).
  - **No row + content** → INSERT new row: `external_ref=id`, `type=kind`, `title = _derive_title(content)` (first H1, fallback `id`), `body_md=content`, `body_sha256=incoming_sha`, `opened_by="upload"`, `status=OPEN`. Catch `UniqueViolation` and re-read (concurrent INSERT race).
  - **Row + no content** → return row.
  - **Row + content where `incoming_sha == row.body_sha256`** → return row idempotently.
  - **Row + content where `incoming_sha != row.body_sha256`** → raise `WorkItemContentConflictError` (typed → 409 at route).
  - **Row + content where `row.body_sha256 IS NULL`** (pre-FEAT-014 backfill) → UPDATE the row with `body_md` / `body_sha256` and return it.
  - **`row.type != dto.kind`** → raise `WorkItemKindConflictError` (typed → 409).

Errors are new subclasses under `app/core/exceptions.py` with kebab-case codes `work-item-not-registered`, `work-item-content-conflict`, `work-item-kind-conflict`.

**Rationale:**
AC-1, AC-2, AC-3, AC-4 directly. This is the single chokepoint that enforces "briefs are immutable" — keep the logic in one function so the test surface is small.

**Acceptance Criteria:**
- [ ] All six branches above have a unit test against a real Postgres fixture.
- [ ] `_derive_title` covers: H1 present, H1 missing, multi-line H1, leading whitespace.
- [ ] Concurrent-INSERT race covered by a test that fires two coroutines against the same `external_ref` with identical content — one succeeds with INSERT, the other re-reads and returns the same row, no exception bubbles.
- [ ] sha256 is computed over `dto.content.encode("utf-8")` (no normalization); confirmed by a CRLF-vs-LF test that produces *different* sha values (this is the documented edge case in the brief).
- [ ] No filesystem read anywhere in `register_work_item`.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/service.py` (or new `work_item_registry.py`) — `register_work_item`, `_derive_title`.
- `src/app/core/exceptions.py` — three new typed exceptions.
- `tests/modules/ai/test_work_item_registry.py` — new.

**Technical Notes:**
Use `hashlib.sha256(...).hexdigest()` — stdlib only. Per CLAUDE.md, the function returns a real DTO/model, never `dict | Any`.

---

### T-285: Route wiring + RFC 7807 errors + payload size cap

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-283, T-284

**Description:**
Wire `register_work_item` into `POST /api/v1/runs` in `src/app/modules/ai/router.py`:

- After request validation, if `intake.workItem` is present, call `register_work_item(session, intake.workItem)` in the same transaction that creates the `Run`.
- On the three typed exceptions from T-284, the global handler emits Problem Details with:
  - `work-item-not-registered` → 400, `type = ".../problems/work-item-not-registered"`.
  - `work-item-content-conflict` → 409, includes `meta.storedSha256` and `meta.uploadedSha256` so the client can diff.
  - `work-item-kind-conflict` → 409, includes `meta.storedKind` and `meta.uploadedKind`.
- Add a server-side body cap: reject `intake.workItem.content` longer than `INTAKE_WORK_ITEM_MAX_BYTES` (config; default 1 MB) with **413 `payload-too-large`** *before* the registration call so a malicious upload doesn't sha256-hash a huge string.
- Persist the linkage: store `engine_item_id` / `workItemId` on the run intake as today (executor layer still reads them); add `Run.intake.workItem.id` mirroring for traceability.

**Rationale:**
AC-1..AC-4 become observable through the public API surface here. The payload cap is the "very large briefs" edge case from the brief.

**Acceptance Criteria:**
- [ ] Integration tests cover the four branches (AC-1..AC-4) end-to-end through the FastAPI client.
- [ ] 413 case has a dedicated test with `INTAKE_WORK_ITEM_MAX_BYTES` set to a small value via env override.
- [ ] Problem Details responses include the diff metadata in 409 bodies; verified by JSON-schema-shaped assertions in tests.
- [ ] The registration call and the `Run` INSERT share one transaction — a 409 raised after the work-item lookup does NOT leave a half-created run.
- [ ] `docs/api-spec.md` updated with the new error codes and intake shape.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — wire registration + size cap.
- `src/app/core/exceptions.py` — map new exception classes in the global handler.
- `src/app/config.py` — add `intake_work_item_max_bytes: int = 1_048_576`.
- `tests/integration/test_runs_route_work_item_upload.py` — new.
- `docs/api-spec.md` — error catalog + intake schema update.

**Technical Notes:**
Per CLAUDE.md ("Pre-persist webhook events"), the registration call is *not* a webhook — but the same persist-before-side-effect discipline applies: write the `WorkItem` row inside the request transaction and only then return 202. The runtime loop never sees a missing row.

---

### T-286: Executor swap — `_handle_request_work_item_load` reads from DB

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-282, T-285

**Description:**
Flip `_handle_request_work_item_load` in `src/app/modules/ai/executors/bootstrap.py` from disk read to DB read:

- Today: reads `ctx.intake.get("workItemPath")` and stuffs the path into memory.
- After: reads `ctx.intake.get("workItem", {}).get("id")` (or falls back to `engineItemId` lookup for runs that started under legacy intake), `SELECT WorkItem WHERE external_ref = id`, returns `body_md` into the memory patch under the same `work_item_content` key downstream nodes expect today.

Add a small helper `_load_work_item_body(session_factory, *, external_ref) -> str` that the executor and any future caller can reuse. **No file IO** in this function — verified by extending the import-quarantine test in T-292.

**Rationale:**
AC-5 — the moment this lands, the orchestrator stops needing filesystem access to brief paths. AC-6 follows by extension.

**Acceptance Criteria:**
- [ ] Executor unit test stages a `WorkItem` row with `body_md = "..."` and verifies the memory patch contains the body, not a path.
- [ ] A second test confirms a `FileNotFoundError` is *never* raised when the brief is registered but the original disk path does not exist on the orchestrator host.
- [ ] Backward-compat: if `intake.workItem` is absent but `intake.workItemId` resolves to a row whose `body_md IS NULL`, the executor falls back to the legacy `source_path` read with a WARNING log (handled in T-288).
- [ ] `_load_work_item_body` is async, uses a short-lived session via `session_factory`, and is covered by a session-discipline test.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — modify `_handle_request_work_item_load`; add `_load_work_item_body`.
- `tests/modules/ai/test_executor_load_work_item.py` — new.

**Technical Notes:**
The executor never imports `pathlib` after this swap. Adding a structural assertion to T-292's quarantine test is cheap and worth doing.

---

### T-287: Audit downstream executors — `propose_tasks` / `generate_tasks` / others read DB content

**Type:** Backend
**Workflow:** investigation-first
**Complexity:** M
**Dependencies:** T-286

**Description:**
Investigation step: grep the codebase for every site that consumes brief content — confirmed candidates from the FEAT-014 brief are `bootstrap.py::generate_tasks` (line ~408, `raw_path = ctx.intake.get("workItemPath")`) and `propose_tasks.py` (current consumer of the loaded body). For each site:

1. Replace any `Path(workItemPath).read_text()` with a read from `WorkItem.body_md` keyed on the memory patch from T-286.
2. Adjust the prompt-template variable substitution: `{workItemPath}` → `{workItemId}` (or keep `{workItemPath}` as a deprecated empty-string for prompt-text stability; document the choice).
3. Confirm none of these sites need the disk path for any reason other than reading the file (logging, audit, etc.).

Output of the investigation lands as a short section in the plan file (`plans/plan-T-287-*.md`) listing every site touched and why.

**Rationale:**
AC-5 is end-to-end — the registration path is moot if a later executor still opens the file. This task closes the gap.

**Acceptance Criteria:**
- [ ] `grep -rn "workItemPath" src/app/` returns only the deprecated-compat shim from T-288 and the executor that derives the path from memory (no `Path.read_text()` of work-item briefs anywhere).
- [ ] `grep -rn "Path(.*work.*item" src/app/` returns no live read paths.
- [ ] Existing unit tests for `propose_tasks` / `generate_tasks` still pass without modification (the body source is transparent to them).
- [ ] Investigation findings documented in the plan file (per workflow).

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — `generate_tasks` adapter site.
- `src/app/modules/ai/executors/propose_tasks.py` — if it reads disk.
- `plans/plan-T-287-executor-audit.md` — captures the investigation.

**Technical Notes:**
This is `investigation-first` per the framework — write down what you found *before* changing code. The grep results are part of the deliverable.

---

### T-288: Deprecation shim — legacy `workItemPath` continues working with a WARNING

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-285, T-286

**Description:**
Preserve one minor's worth of backward-compat for runs started with the old intake shape:

- If `intake.workItem` is absent but `intake.workItemPath` is present, the route logs `WARNING code="intake-work-item-path-deprecated"` once per run and continues.
- The legacy code path reads the markdown from disk (existing behavior) **and** registers a `WorkItem` row via `register_work_item` so that future intake-shape requests can dedupe on it. This bridges old and new without forking the executor.
- If neither is present, the run starts with no `WorkItem` link (non-lifecycle agents — unchanged behavior).

**Rationale:**
AC-7 — existing callers in CI, dev scripts, and the v0.1.0 LLM-policy agent path must keep working through one release.

**Acceptance Criteria:**
- [ ] A run started with `intake.workItemPath="docs/work-items/FEAT-005-lifecycle-agent.md"` (a known repo file) completes end-to-end against `lifecycle-agent@0.3.0`.
- [ ] The deprecation log entry is emitted exactly once per run; verified with `caplog`.
- [ ] After such a run completes, `SELECT body_md FROM work_items WHERE external_ref='FEAT-005'` returns the body (auto-registered from disk).
- [ ] A run started with *both* `workItem` and `workItemPath` uses `workItem` (precedence: new wins; legacy is ignored without warning).

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — deprecation branch.
- `src/app/modules/ai/executors/bootstrap.py` — fallback in `_handle_request_work_item_load`.
- `tests/integration/test_runs_route_legacy_work_item_path.py` — new.

**Technical Notes:**
The deprecation code is scheduled for removal in the FEAT after this one. Tag with `# DEPRECATED FEAT-014; remove after one minor` so the grep is obvious.

---

## CLI

### T-289: CLI flag — `orchestrator run --work-item <path>` reads file client-side

**Type:** CLI
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-285

**Description:**
Add a `--work-item PATH` option to `orchestrator run` in `src/app/cli.py`. Behavior:

1. Read the file at PATH (UTF-8). Exit code 2 with a clear message if the file is missing or unreadable (no HTTP call).
2. Derive `id` and `kind` from the filename (`FEAT-XXX-slug.md` → `id="FEAT-XXX"`, `kind="FEAT"`). If filename doesn't match the canonical pattern, surface an error suggesting `--intake workItem.id=...` instead.
3. Construct `intake.workItem = {id, kind, content: <file bytes>}` and merge with any other `--intake` flags.
4. POST to `POST /api/v1/runs` as today.

The existing `--intake workItemPath=...` flag continues to work and produces the legacy shape (T-288 catches it server-side).

**Rationale:**
AC-6 — operators run against a remote orchestrator with no shared filesystem. AC-7 — the legacy flag still works.

**Acceptance Criteria:**
- [ ] `orchestrator run lifecycle-agent@0.3.0 --work-item docs/work-items/FEAT-005-lifecycle-agent.md` succeeds against a local server.
- [ ] `--work-item ./missing.md` exits with code 2 and a clear message; no HTTP call made (verified by `respx` capture).
- [ ] Filename parse covers: `FEAT-XXX.md`, `FEAT-XXX-some-slug.md`, `BUG-XXX-other.md`, `IMP-XXX.md`. Rejects: `random.md`, `FEAT.md`, `feat-001.md`.
- [ ] `--work-item` and `--intake workItemPath=...` are mutually exclusive (CLI-level error if both supplied).

**Files to Modify/Create:**
- `src/app/cli.py` — new `--work-item` option on `run`.
- `tests/modules/ai/test_cli_run_work_item.py` — new.

**Technical Notes:**
Per CLAUDE.md "Don't bypass the HTTP boundary from the CLI" — the file read happens client-side; the server still sees a regular POST with content in the body.

---

### T-290: CLI helper — `orchestrator import-work-items <dir>` operator backfill

**Type:** CLI
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-285, T-289

**Description:**
One-shot operator command at `orchestrator import-work-items DIR [--dry-run]`. For each `FEAT-*.md` / `BUG-*.md` / `IMP-*.md` file under DIR:

1. Read body, derive `id` and `kind` from filename.
2. POST to `POST /api/v1/work-items` — *new lightweight endpoint that wraps `register_work_item` without starting a run*. This is the **one** new endpoint FEAT-014 adds beyond the run-start hook, and only because import-without-running needs it. Returns 201 on insert, 200 on idempotent reuse, 409 on conflict.
3. Aggregate results into a `WorkItemImportReport` (counts: inserted / reused / conflicted / malformed) and print a CLI summary.
4. `--dry-run` lists what would be inserted without calling the endpoint.

**Rationale:**
AC-8 — operators can populate a fresh DB from an existing `docs/work-items/` directory without manually starting runs. Mirrors the FEAT-013 `migrate-traces` shape.

**Acceptance Criteria:**
- [ ] Running against `docs/work-items/` on a fresh DB inserts one row per `FEAT-*.md` / `BUG-*.md` / `IMP-*.md` file (currently 26 files).
- [ ] Re-running is idempotent: zero new inserts, all rows counted as "reused".
- [ ] `--dry-run` writes nothing; output lists each file with intended action.
- [ ] Files with names that don't match the pattern are skipped with a WARNING line (no failure).
- [ ] If the orchestrator returns 409 for a file whose body diverges from the stored row, the command exits with code 1 and lists every conflicting file at the end.

**Files to Modify/Create:**
- `src/app/cli.py` — new `import-work-items` command.
- `src/app/modules/ai/router.py` — `POST /api/v1/work-items` endpoint (thin wrapper around `register_work_item`).
- `tests/modules/ai/test_cli_import_work_items.py` — new.

**Technical Notes:**
The brief explicitly excludes `GET /api/v1/work-items`. POST is included because `import-work-items` needs to write without running. Per CLAUDE.md, the endpoint shares the response envelope `{ data }` and returns the `WorkItem` DTO on success.

---

## Testing

### T-291: Service + route integration tests — AC-1..AC-4, AC-7 coverage

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-285, T-288

**Description:**
Consolidate the per-task tests from T-284, T-285, T-288 into one named-by-AC suite to make the acceptance mapping obvious to reviewers:

- `test_first_sight_inserts_row` (AC-1).
- `test_second_sight_reuses_row` (AC-2).
- `test_content_conflict_returns_409` (AC-3).
- `test_no_row_no_content_returns_400` (AC-4).
- `test_legacy_work_item_path_still_completes_with_warning` (AC-7).
- `test_kind_conflict_returns_409` (edge case from Section 9 of brief).
- `test_concurrent_first_sight_race_resolves_idempotently` (edge case from Section 9).
- `test_oversized_body_returns_413` (edge case from Section 9).

Each test mirrors the per-task unit coverage at the HTTP boundary (real Postgres, no SQL mocks per CLAUDE.md).

**Rationale:**
AC-1..AC-4 + AC-7 + brief Section 9 edge cases. The per-task suites can stay narrow; this file is the regression net.

**Acceptance Criteria:**
- [ ] Eight tests as named above, all passing.
- [ ] Each test docstring cites the AC or edge case it covers.
- [ ] Real-Postgres fixture (no mocks); follows the FEAT-013 parity-test pattern.

**Files to Modify/Create:**
- `tests/integration/test_work_item_upload_acceptance.py` — new.

**Technical Notes:**
Reuse the `parity_run` cleanup pattern from `test_trace_backend_parity.py` so the suite doesn't leak rows across tests.

---

### T-292: End-to-end "no filesystem access" test + structural guard

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-286, T-287

**Description:**
Two artefacts in one task:

1. **End-to-end test.** Start the orchestrator in a tmp directory, register `FEAT-005` by upload (via the test client), then run `lifecycle-agent@0.3.0` through to the first `propose_tasks` dispatch. Assert the run advances past `load_work_item` without ever opening a file under the test's project root — implemented via a `pathlib.Path` patch or an `os.open` audit that fails the test if a non-allowlisted path is opened.
2. **Structural guard (subprocess).** A unit test in `tests/test_executors_dont_read_briefs.py` that spawns a subprocess importing `src/app/modules/ai/executors/bootstrap.py` and asserts `Path` is *not* invoked on any `*-work-items/*` path during import. Same shape as `tests/test_runtime_deterministic_is_pure.py` (FEAT-009).

**Rationale:**
AC-5 directly; the structural guard prevents regression (someone reintroduces a disk read in a future PR).

**Acceptance Criteria:**
- [ ] End-to-end test passes against a clean tmp dir with no `docs/work-items/` mounted.
- [ ] Structural guard catches a deliberate regression (a test that monkey-patches a disk read in bootstrap.py fails the guard).
- [ ] CI runs both in the standard `pytest` invocation.

**Files to Modify/Create:**
- `tests/integration/test_runs_no_filesystem_access.py` — new.
- `tests/test_executors_dont_read_briefs.py` — new structural guard.

**Technical Notes:**
The audit approach: wrap `builtins.open` and `pathlib.Path.read_text` / `read_bytes` for the duration of the test, asserting no call resolves to a path under `<repo_root>/docs/work-items/`.

---

### T-293: CLI smoke tests — `--work-item` flag + `import-work-items` command

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-289, T-290

**Description:**
`typer.testing.CliRunner` smoke tests for the two CLI surfaces. Coverage:

- `--work-item` happy path against `respx`-stubbed orchestrator.
- `--work-item ./missing.md` exits 2 without HTTP.
- `--work-item` + `--intake workItemPath=...` exits 2 with a "mutually exclusive" message.
- `import-work-items` against a tmp dir with three fixture files (one FEAT, one BUG, one IMP) reports inserted/reused/conflicted correctly.
- `import-work-items --dry-run` writes nothing (verified by checking `respx` received zero requests).
- `import-work-items` with an orchestrator that returns 409 exits 1 and lists the offender.

**Rationale:**
AC-6, AC-8 at the CLI boundary. Integration tests in T-291/T-292 cover the server; this covers the client.

**Acceptance Criteria:**
- [ ] Six tests as named above, all passing.
- [ ] All HTTP is `respx`-stubbed; no live network or running orchestrator.

**Files to Modify/Create:**
- `tests/modules/ai/test_cli_run_work_item.py` — extends from T-289.
- `tests/modules/ai/test_cli_import_work_items.py` — extends from T-290.

**Technical Notes:**
Per CLAUDE.md testing priorities, CLI smoke tests are priority 3 — keep them small and deterministic.

---

## Polish

### T-294: Documentation sweep — data-model.md, api-spec.md, CLAUDE.md + anti-pattern

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-285, T-290, T-288

**Description:**
Round out the doc updates that touch CLAUDE.md's "Documentation Maintenance Discipline" rows:

- `docs/data-model.md` — `WorkItem` entity gains `body_md` and `body_sha256` fields; add immutability note ("briefs are content-addressed; mismatched re-upload is 409"). Changelog entry.
- `docs/api-spec.md` — document `intake.workItem` shape, the three new error codes (`work-item-not-registered`, `work-item-content-conflict`, `work-item-kind-conflict`), the 413 cap, and the new `POST /api/v1/work-items` endpoint. Changelog entry.
- `CLAUDE.md` — add a Patterns entry ("Work-item bodies live in the DB; executors read from `WorkItem.body_md`, never from disk") and an Anti-Patterns entry ("Don't read a work-item brief from the filesystem in an executor — the body is in `WorkItem.body_md`"). Update the Quick Reference command examples that use `--intake workItemPath=...` to use `--work-item ./path.md`.
- `docs/work-items/FEAT-014-*.md` — flip Status to `Completed` at the end of the FEAT.

**Rationale:**
AC-9 — every code-doc rule in CLAUDE.md applies. Without the anti-pattern entry, the next AI agent will reintroduce a disk read.

**Acceptance Criteria:**
- [ ] All three docs updated; each has a changelog entry at the bottom referencing FEAT-014.
- [ ] `grep -n "workItemPath" CLAUDE.md` returns the deprecation note only, not as a recommended pattern.
- [ ] The Quick Reference's `orchestrator run` example uses `--work-item`.
- [ ] FEAT-014 brief flipped to `Completed` in the final commit.

**Files to Modify/Create:**
- `docs/data-model.md`
- `docs/api-spec.md`
- `CLAUDE.md`
- `docs/work-items/FEAT-014-work-item-upload-not-filesystem-read.md`

**Technical Notes:**
Changelog format per `.ai-framework/guides/maintenance.md`. The CLAUDE.md anti-pattern entry sits next to the existing FEAT-013 "Trace writes go through a protocol" line — same review-blocker tone.

---

## Summary

**Total tasks:** 13 (T-282..T-294).

**By type:**
- Database: 1 (T-282)
- Backend: 6 (T-283, T-284, T-285, T-286, T-287, T-288)
- CLI: 2 (T-289, T-290)
- Testing: 3 (T-291, T-292, T-293)
- Documentation: 1 (T-294)

**Complexity:**
- S: 7 (T-282, T-283, T-288, T-289, T-293, T-294, T-286)
- M: 6 (T-284, T-285, T-287, T-290, T-291, T-292)
- L: 0
- XL: 0

**Critical path:** T-282 → T-284 → T-285 → T-286 → T-287 → T-294 (six-step chain; everything else branches off mid-path).

**Risks / open questions:**
- The deprecation shim (T-288) registers a `WorkItem` row from a disk read of the legacy `workItemPath`. If the legacy run is started against a brief that was *also* uploaded with different content, T-288 needs to decide: 409 here too, or accept legacy as authoritative? Brief says briefs are immutable, so 409 — but worth a callout in the plan for T-288.
- `_derive_title` for briefs missing an H1 is a soft policy. If `external_ref` is used as a placeholder, the existing `title` column never holds the actual title. Probably fine for v1 but worth re-visiting if a UI lists work-items.
- `POST /api/v1/work-items` is the one new endpoint and it lives only because of `import-work-items`. If we ever decide to ship `GET /api/v1/work-items`, this endpoint already exists and the entity gains a real REST surface — that's a feature, but it's also creeping scope. Documented as out-of-scope in the brief's Section 4.2.
