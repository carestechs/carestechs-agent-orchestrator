# FEAT-014 — Work-items are uploaded, not read from the filesystem

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-014 |
| **Name** | Work-item upload (deprecate `workItemPath` filesystem read) |
| **Target Version** | Continuous |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | Carlos (self-host / DevTools umbrella deployment) |
| **Date Created** | 2026-05-12 |

---

## 2. User Story

**As an** operator running the orchestrator (CLI or UI), **I want to** start a run by *uploading* the work-item content as part of the request, **so that** the orchestrator no longer needs read access to the caller's filesystem — runs become portable across hosts, containers, and remote clients.

---

## 3. Goal

A run can be started without the orchestrator process having any access to the markdown file on disk; the work-item body travels in the request payload on first sight and is dedup'd by `external_ref` on every sight after.

---

## 4. Feature Scope

### 4.1 Included

- Add a `body_md` column (TEXT) and `body_sha256` column (TEXT, 64 hex chars) to `work_items`. Indexed implicitly by the existing `external_ref` UNIQUE.
- New intake schema field `intake.workItem: { id, kind, content? }` (camelCase JSON, snake_case in Python):
  - `id` (required) — maps to `WorkItem.external_ref`.
  - `kind` (required) — `FEAT | BUG | IMP`, maps to `WorkItem.type`.
  - `content` (optional markdown body) — required on first sight; rejected on conflict with stored body.
- Service-layer registration semantics on `POST /api/v1/runs`:
  - **No row + no content** → 400 `work-item-not-registered` (Problem Details).
  - **No row + content** → INSERT new `WorkItem` with `body_md`, `body_sha256`, `external_ref=id`, `type=kind`, `title` derived from first H1 of content (fallback `external_ref`).
  - **Row exists + no content** → reuse row; respond 202 as today.
  - **Row exists + content whose sha256 matches stored** → reuse row idempotently.
  - **Row exists + content whose sha256 differs** → 409 `work-item-content-conflict` (briefs are immutable; bump `external_ref` if the body must change).
- Executor seam:
  - `load_work_item` (`executors/bootstrap.py::_handle_request_work_item_load`) reads `body_md` from the DB row keyed on `external_ref`, not from disk.
  - Downstream executors that read brief content (`propose_tasks`, `generate_tasks`, etc.) consume the same DB-backed content; no executor opens a file under `docs/work-items/` after this FEAT lands.
- CLI:
  - `orchestrator run <agent> --work-item ./path/to/FEAT-XXX.md` reads the file *client-side*, parses `id` + `kind` from filename or front-matter, and POSTs `intake.workItem = {id, kind, content}`.
  - Backward-compat `--intake workItemPath=...` continues to work for one minor version with a deprecation log entry on each use.
- Intake schema sweep — collapse the parallel keys (`workItemPath`, `workItemId`, `engineItemId`, `workItemEngineId`) so the public surface is `intake.workItem.id` plus the orchestrator-internal `engineItemId` (still used after `load_work_item` resolves the engine link).
- One-shot migration helper `orchestrator import-work-items <dir>` that walks a directory of markdown briefs and POSTs each one to a configured orchestrator instance — useful for backfilling the new column from the current `docs/work-items/` directory.

### 4.2 Excluded

- A `GET /api/v1/work-items` list endpoint or pickers in any UI. The DB row is created and persists, but the only public read path in v1 is via running a run. Listing endpoints are a candidate for a follow-up FEAT once a UI consumer exists.
- Editing or versioning briefs. Briefs are immutable by `external_ref`; the 409 case is *not* a "PATCH the body" affordance. A changed brief uses a new `external_ref` (e.g., `FEAT-100-v2`).
- A separate `POST /api/v1/work-items` endpoint. Decided against in design discussion (2026-05-12): the one-POST shape with optional `content` gets us the resource-like semantics without a second endpoint or two-round-trip ceremony.
- Front-matter parsing standards / schema for the markdown body itself. The body is opaque text to the orchestrator; downstream executors parse it as they do today.
- Removing `workItemPath` in this FEAT. Deprecation only; full removal is a follow-up after the next minor cut.

---

## 5. Acceptance Criteria

- **AC-1**: `POST /api/v1/runs` with `intake.workItem = {id: "FEAT-100", kind: "FEAT", content: "<markdown>"}` against a fresh DB returns 202 *and* inserts a `work_items` row with `body_md = "<markdown>"`, `body_sha256` set, `external_ref = "FEAT-100"`, `type = "FEAT"`.
- **AC-2**: A second `POST /api/v1/runs` with `intake.workItem = {id: "FEAT-100", kind: "FEAT"}` (no content) returns 202, does NOT insert a new row, and the run's `load_work_item` executor produces the same `body_md` to downstream nodes as AC-1.
- **AC-3**: `POST /api/v1/runs` with `intake.workItem = {id: "FEAT-100", kind: "FEAT", content: "<different markdown>"}` returns **409** with `type: "https://orchestrator.local/problems/work-item-content-conflict"` and does NOT touch the DB row.
- **AC-4**: `POST /api/v1/runs` with `intake.workItem = {id: "FEAT-999", kind: "FEAT"}` where `FEAT-999` does not exist returns **400** with `type: ".../work-item-not-registered"`.
- **AC-5**: An end-to-end run of `lifecycle-agent@0.3.0` succeeds when the orchestrator container has **no** mounted access to the caller's `docs/work-items/` directory; the brief content reaches `propose_tasks` and `generate_tasks` via DB, not via file read.
- **AC-6**: `orchestrator run <agent> --work-item ./FEAT-100.md` succeeds against a remote orchestrator on a different host, with no shared filesystem.
- **AC-7**: An existing run started with `intake.workItemPath=...` (legacy form) still completes end-to-end and emits a deprecation log entry at WARNING level with `code = "intake-work-item-path-deprecated"`.
- **AC-8**: `orchestrator import-work-items docs/work-items/` against a fresh DB inserts one row per `FEAT-*.md` / `BUG-*.md` / `IMP-*.md` file with correct `external_ref`, `type`, `body_md`, `body_sha256`. Re-running is idempotent (no inserts, no errors).
- **AC-9**: `docs/data-model.md` describes the two new columns; `docs/api-spec.md` documents the intake shape and the two new error codes; `CLAUDE.md` includes a pattern entry forbidding new filesystem reads of work-item briefs.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `WorkItem` | Gains `body_md TEXT NULL` and `body_sha256 TEXT NULL` columns. Existing rows pre-FEAT-014 have NULL in both and read from `source_path` for one minor version (legacy fallback). | `body_sha256` is content-addressed: any client POST with matching sha256 is idempotent; mismatch is 409. Briefs are immutable once `body_md` is set. |
| `Run.intake` | Schema gains a typed `workItem: { id, kind, content? }` sub-object via a new Pydantic model `RunIntakeWorkItem`. Top-level `workItemPath` retained as deprecated alias. | `kind` MUST match one of the `WorkItemType` enum values. `id` MUST match `^[A-Z]+-\d+(-[a-z0-9-]+)?$` (validation, not enforcement of uniqueness — that's `WorkItem.external_ref`). |

**New entities required:** None. Two columns added to `work_items`. The `pending_aux_writes` / `Dispatch` / `EffectorCall` / `ExecutorCall` surfaces are untouched.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs` | POST | Modified | Accepts `intake.workItem = {id, kind, content?}`. Returns 400 `work-item-not-registered` or 409 `work-item-content-conflict` per Section 5. `workItemPath` still accepted but logged as deprecated. |

**New endpoints required:** None. (A future `GET /api/v1/work-items` is explicitly out of scope per 4.2.)

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| — | — | No UI in v1; CLI-only change. |

**New screens required:** None.

---

## 9. Edge Cases

- **Content with trailing whitespace or CRLF differences.** `body_sha256` is computed over the raw bytes as received (no normalization). Two clients sending the "same" markdown with different line endings will trigger a 409. Documented in `api-spec.md`; expected behavior — briefs are byte-immutable.
- **Concurrent first-sight POSTs for the same `external_ref`.** Two clients race to register `FEAT-100` with identical content; one INSERT wins, the other catches `UniqueViolation` on `uq_work_items_external_ref` and re-reads the row, compares sha256, returns 202 on match or 409 on mismatch. No retry loop, no advisory lock.
- **Content uploaded for an item created pre-FEAT-014 (NULL `body_md`).** First POST with content backfills `body_md` + `body_sha256` and treats it as the canonical content from now on. Subsequent mismatched POSTs are 409 as usual. Pre-FEAT-014 rows that never receive an upload continue to read from disk via the legacy `source_path` fallback until the deprecation window closes.
- **Very large briefs (>1 MB).** Reject at the route boundary with 413 `payload-too-large`. Server-side limit configurable via `INTAKE_WORK_ITEM_MAX_BYTES`, defaulting to 1 MB. Practical briefs are well under 100 KB.
- **`kind` mismatch on second sight.** Row exists with `type="FEAT"`; client POSTs `{id: "FEAT-100", kind: "BUG"}`. Return 409 `work-item-kind-conflict` — the kind is part of the immutable identity of the brief.
- **CLI invoked against a missing file path.** `orchestrator run --work-item ./missing.md` exits with code 2 *before* any HTTP call, with a clear error. The orchestrator never sees the request.
- **The intake's `workItem` is omitted entirely** (agent doesn't need one — non-lifecycle agents). Today no other agent requires a work-item; if a non-lifecycle agent is started without `intake.workItem`, the run proceeds as today. Validation lives in the lifecycle-agent's executor binding, not at the route level.

---

## 10. Constraints

- **Forward-only Alembic migration.** New columns are NULL-able to accommodate pre-existing rows; no destructive downgrade path in v1.
- **No new external dependencies.** sha256 via stdlib `hashlib`; no S3 / blob store.
- **Backward-compat for one minor.** `intake.workItemPath` remains functional with a WARNING log; removal lands in a follow-up tagged FEAT-014a or absorbed into the next CLI cut.
- **Pydantic-at-boundary discipline (CLAUDE.md).** The new intake sub-object MUST be a typed Pydantic model — no `dict[str, Any]` access in route handlers or executors.
- **Composition-root LLM access (CLAUDE.md).** The body-loading executor remains the only producer site for brief content; the runtime loop continues not to import `core.llm` and does not gain a filesystem dependency.
- **AD-6 / self-host discipline.** This FEAT is itself a candidate to run through the lifecycle-agent once registered as a work-item via the new upload path — eat-the-dog-food data point.

---

## 11. Motivation and Priority Justification

**Motivation:** The current `workItemPath` shape requires the orchestrator to read a file path that lives in the *caller's* filesystem. In containerized / DevTools-umbrella deployments this forces a `docker cp` dance (or a volume mount) every time someone wants to start a run on a new brief, and it blocks remote callers (CLI on a developer laptop talking to an orchestrator on a server) entirely. It also produces drift: three parallel intake keys today (`workItemPath`, `workItemId`, `engineItemId`) all identifying the same work-item.

**Impact if delayed:** Every new self-host run hits the filesystem-mount friction. The intake schema continues to accumulate parallel keys, and the orchestrator stays coupled to the caller's process environment. Remote-CLI use cases (a future cloud orchestrator, CI runs) require this to land first.

**Dependencies on this feature:** Any future "remote orchestrator" or "cloud-hosted lifecycle agent" work needs the upload path. Any UI feature that lets a user paste a brief into a textarea and click "start run" needs it. AD-6 self-host adoption is constrained until this lands.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` — the lifecycle-agent operator |
| **Stakeholder Scope Item** | "Headless service drives the ia-framework's feature lifecycle as an agent-driven loop" (orchestrator must be deployable independent of the caller's filesystem to fulfill this at scale) |
| **Success Metric** | Increased self-host runs (AD-6) — measured by count of lifecycle-agent runs started via the upload path |
| **Related Work Items** | FEAT-005 (lifecycle agent — original consumer of `workItemPath`), FEAT-011 (deterministic port — current consumer of `workItemPath` via executor seam), AD-6 in `docs/ARCHITECTURE.md` (self-delivery discipline) |

---

## 13. Usage Notes for AI Task Generation

When generating tasks from this Feature Brief:

1. **Migration before route.** Add the `body_md` + `body_sha256` columns and migration first; only then wire the route logic. Coverage tests for the route assume the columns exist.
2. **Typed intake first.** The `RunIntakeWorkItem` Pydantic model lands as its own task before the registration service logic — every other task imports it.
3. **Executor swap is the user-visible cutover.** The task that flips `_handle_request_work_item_load` from disk-read to DB-read is the moment AC-5 / AC-6 become testable end-to-end. Schedule it after route + service.
4. **CLI changes are client-side.** The `--work-item` flag adds file IO at the *client*, not the server. No new server route. The CLI task can land in parallel with the route task.
5. **Backward-compat task is separate.** A dedicated task captures the deprecation log entry + the legacy `workItemPath` path's continued functioning. Don't bundle it with the new shape.
6. **`import-work-items` is one-shot.** Like `migrate-traces` (FEAT-013 / T-276), this is an operator helper, not a continuous reconciler. Should not introduce new model methods beyond what the run-start path already needs.
7. **Doc-update tasks are required (CLAUDE.md).** `data-model.md`, `api-spec.md`, `CLAUDE.md` all need entries in the same PRs that land the code change; missing doc updates are a review blocker.
8. **Anti-pattern entry in CLAUDE.md.** Land a new "Don't" line: *"Don't read a work-item brief from the filesystem in an executor — the body lives in `WorkItem.body_md`."*
