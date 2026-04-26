# BUG-002 Task Breakdown — `engine_workflows` cache is not tenant-scoped

> **Source:** [`docs/work-items/BUG-002-engine-workflows-tenant-scope.md`](../docs/work-items/BUG-002-engine-workflows-tenant-scope.md)
>
> The bug brief's §10 already documents a concrete fix sketch (schema PK to `(tenant_id, name)`, tenant id from `FLOW_ENGINE_TENANT_API_KEY`, stale-cache 404 recovery). Investigation here is consequently light — one scope-confirmation task before the schema change, then two implementation tasks, then one verification task. Total: **4 tasks · ~1.5 dev days**.

---

## Phase 1 — Investigation

### T-200: Confirm tenant-id source + audit for sibling tenant-blind caches

**Type:** Investigation
**Workflow:** investigation-first
**Complexity:** S
**Dependencies:** None

**Investigation Goal:**
Decide where the orchestrator gets its tenant identity for cache keying, and verify `engine_workflows` is the *only* tenant-blind cache in the lifecycle subsystem before we lock in a fix shape.

**Rationale:**
The bug brief proposes two options for tenant identity (JWT subject claim vs an explicit `FLOW_ENGINE_TENANT_ID` setting). Picking one before writing the migration avoids a re-do. Separately, if other tables silently assume one tenant per orchestrator process, the migration should cover them too — discovering a second instance after the migration ships is much more expensive.

**Investigation Steps:**
1. Read `src/app/modules/ai/lifecycle/engine_client.py` end-to-end. Identify exactly how the JWT is minted from `FLOW_ENGINE_TENANT_API_KEY`. Is the tenant id a discrete field on the API key payload, or only embedded as the JWT subject? If it's only in the JWT, propose an explicit `FLOW_ENGINE_TENANT_ID` setting alongside the API key — simpler than parsing JWT material in the bootstrap.
2. `grep -rn "engine_item_id\|engine_workflow_id" src/app/` — list every cache or foreign key that stores an engine-side UUID. For each, ask: would this row's id be different under a different tenant? Document any other tenant-blind caches.
3. Review `pending_signal_context`, `pending_aux_writes`, `webhook_events` — these store engine-correlated data (correlation ids, webhook payloads). Confirm none of them are *cached lookups* that get reused across tenants (they should all be transient or per-event).
4. Re-read `docs/work-items/BUG-002-engine-workflows-tenant-scope.md` §10 fix sketch and §6 "Observations". Note whether anything in this investigation contradicts those proposals; update the brief in a follow-on PR if so.

**Expected Findings:**
- `engine_workflows` is the only tenant-blind cache. (Most engine-side ids — `engine_item_id` on `tasks`/`work_items` — are *foreign references* tied to specific tenant data, not cache lookups: they came in via signal processing under a known tenant and don't get re-used across tenant switches.)
- `FLOW_ENGINE_TENANT_ID` as an explicit setting is cleaner than JWT parsing — bootstrap already has access to `Settings`, no need for it to introspect the engine client's auth material.

**Output:**
A 3-paragraph note appended to BUG-002 §6 (or stitched into the plan) recording: (1) the tenant-id source decision, (2) the audit result (other tenant-blind caches: yes/no list), (3) any deviations from §10's fix sketch. The implementation task descriptions reference this note.

---

## Phase 2 — Implementation

### T-201: Schema migration — `engine_workflows` PK to `(tenant_id, name)`

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-200

**Description:**
Alembic migration that adds `tenant_id uuid NOT NULL` to `engine_workflows`, drops the existing `name`-only primary key, and recreates it as `PRIMARY KEY (tenant_id, name)`. Pre-flight check refuses on populated tables — operator must `TRUNCATE engine_workflows` first so the orchestrator's next boot re-fetches workflow ids against the *current* tenant.

**Rationale:**
Root cause from BUG-002: the cache key is `name` only. Without tenant scope in the schema, the bootstrap has nothing to filter on and the bug cannot be fixed.

**Root Cause Addressed:**
The schema-level half of the bug. The bootstrap code edit (T-202) is the runtime half; both are needed for the fix.

**Implementation Approach:**
1. Create `src/app/migrations/versions/<rev>_engine_workflows_add_tenant_id.py`:
   - **Pre-flight in `upgrade()`**: query `SELECT COUNT(*) FROM engine_workflows`. If non-zero, raise with operator-facing message: *"engine_workflows has N rows; truncate before applying this migration so the orchestrator re-bootstraps against the current tenant. See BUG-002."*
   - Drop the existing PK constraint on `name`.
   - Add `tenant_id uuid NOT NULL`.
   - Add new PK on `(tenant_id, name)`.
   - **Downgrade**: drop the new PK, drop `tenant_id`, recreate the PK on `name`. Same pre-flight on populated tables, same operator-facing message.
2. Update `src/app/modules/ai/models.py::EngineWorkflow`:
   - Add `tenant_id: Mapped[uuid.UUID]` column.
   - Change PK definition to composite `(tenant_id, name)`.
3. Update `tests/modules/ai/test_models.py` column assertion list and any composite-PK shape check.

**Files to Modify:**
- `src/app/migrations/versions/<rev>_engine_workflows_add_tenant_id.py` — new.
- `src/app/modules/ai/models.py` — `EngineWorkflow`: add `tenant_id`, composite PK.
- `tests/modules/ai/test_models.py` — column assertion update.

**Acceptance Criteria:**
- [ ] `uv run alembic upgrade head` applies cleanly against an empty `engine_workflows`.
- [ ] `uv run alembic upgrade head` against a populated `engine_workflows` aborts with the BUG-002 message.
- [ ] `uv run alembic downgrade -1` followed by `upgrade head` restores the original schema and re-applies the new schema cleanly (round-trip).
- [ ] `test_models.py` reflects the composite PK + new column.

**Regression Risk:**
The migration is destructive in the operator-facing sense — operators with existing `engine_workflows` rows must `TRUNCATE` before upgrading. Mitigated by: (a) the pre-flight refuses to run silently, (b) the orchestrator's `lifespan` re-bootstraps the cache on next boot, so truncation has no functional cost — only the engine round-trip on first start. Document the upgrade step in the migration's docstring + BUG-002 §10.

---

### T-202: Bootstrap edits — tenant-aware lookup + stale-cache 404 recovery

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-201

**Description:**
Plumb tenant identity into `lifecycle.bootstrap.ensure_workflows()` and the cache reads/writes so they filter / write `(tenant_id, name)`. Add a stale-cache recovery path that catches `EngineError` 404 from the engine on first use and re-resolves transparently. Remove the `FIXME(BUG-002)` comment landed in PR #29.

**Rationale:**
Closes the runtime half of BUG-002. The schema migration (T-201) creates the PK shape; this task makes the bootstrap actually use it. The stale-cache 404 path covers BOTH the tenant-change case and the in-tenant data-reset case from the bug brief §6.

**Root Cause Addressed:**
- Bootstrap reads/writes the cache without tenant scope (BUG-002 §3 actual behavior).
- No fallback for "cache populated, engine doesn't recognize the id" (BUG-002 §3 last paragraph).

**Implementation Approach:**
1. **Settings**: add `flow_engine_tenant_id: uuid.UUID | None` to `app.config.Settings` (or, if T-200 chose JWT-subject extraction instead, add a helper on `FlowEngineLifecycleClient` and use that). Required when `flow_engine_lifecycle_base_url` is configured; validation error if set inconsistently.
2. **`ensure_workflows(db, client, *, tenant_id)`**: accept the tenant id as a keyword-only parameter. Lifespan passes it through from settings. The mapping returned (`{name: engine_workflow_id}`) shape is unchanged — only the *lookup logic* changes.
3. **`_resolve()`**:
   - Cache read filters `WHERE tenant_id = :tenant_id AND name = :name`.
   - On cache hit, return immediately (current behavior).
   - On cache miss: call `client.create_workflow(...)` as today; on 409, fall back to `client.get_workflow_by_name(name)` (current behavior).
   - **NEW** (stale-cache recovery): wrap the *first downstream use* (callers of `ensure_workflows` ultimately exercise the id via signal adapters) — actually no, do this inside `_resolve` itself: after a cache hit, optimistically validate the id with a cheap engine call (`client.get_workflow_by_id(engine_workflow_id)` — add this method if it doesn't exist). On 404, log "stale cache for tenant X, workflow Y — re-resolving", delete the cache row, recurse into the create-or-409 path. The validation call is one extra round-trip per workflow per startup; acceptable.

   Alternative if the per-startup engine round-trip is judged too costly: lazy validation. The cache-hit path returns the id; the *first signal that 404s on a workflow id* should trigger re-resolution. This shifts the recovery cost from startup to first-failed-signal. Simpler to keep startup deterministic — go with the eager validation unless T-200 finds a reason not to.
4. **`_upsert_cache()`**: include `tenant_id` in the insert; the `ON CONFLICT` target becomes the composite PK.
5. **`lifespan.py`**: read `tenant_id` from settings, pass it into `ensure_workflows()`. If unset and the engine is configured, fail startup with a clear error pointing at BUG-002.
6. **Remove `FIXME(BUG-002)` comment** at `bootstrap.py:60` (landed in PR #29). Replace with a brief inline comment: "`tenant_id` filter required — see BUG-002 history" so future readers see the rationale without the alarm.

**Files to Modify:**
- `src/app/modules/ai/lifecycle/bootstrap.py` — `ensure_workflows`, `_resolve`, `_upsert_cache`, FIXME removal.
- `src/app/lifespan.py` — pass `tenant_id` into bootstrap call.
- `src/app/config.py` — `flow_engine_tenant_id` setting (validation paired with `flow_engine_lifecycle_base_url`).
- `src/app/modules/ai/lifecycle/engine_client.py` — add `get_workflow_by_id` if not present (for the eager-validation path); harmless if already there.
- `src/app/modules/ai/lifecycle/__init__.py` — re-export updated signature if applicable.
- `.env.example` + `.env.production.example` — add `FLOW_ENGINE_TENANT_ID` alongside `FLOW_ENGINE_TENANT_API_KEY`.

**Acceptance Criteria:**
- [ ] Cold boot against empty `engine_workflows` + tenant A → cache populated with `(tenant_a_id, work_item_workflow, <UUID-A>)`. Subsequent boot reads from cache, no engine create call.
- [ ] Cold boot with `FLOW_ENGINE_TENANT_ID` set to tenant B but cache populated for tenant A → bootstrap writes a fresh row for `(tenant_b_id, work_item_workflow, <UUID-B>)`; tenant A's row is untouched.
- [ ] Cache hit returns an id the engine no longer recognizes → eager validation 404s, the row is deleted, the bootstrap re-resolves and writes the fresh id. Single startup, no operator intervention.
- [ ] Engine-absent fallback unchanged: when `flow_engine_lifecycle_base_url` is unset, the bootstrap is not invoked (existing behavior); no `flow_engine_tenant_id` requirement either.
- [ ] Lifespan refuses to boot if `flow_engine_lifecycle_base_url` is set but `flow_engine_tenant_id` is not — error message references BUG-002.

**Regression Risk:**
- **Existing single-tenant operators** must add `FLOW_ENGINE_TENANT_ID` to their env on upgrade or the lifespan refuses to start. Documented in the migration's docstring (T-201) and BUG-002 §10.
- **Eager-validation round-trip** on every startup adds 1 engine call per declared workflow (currently 2 workflows = 2 calls, ~tens of ms). Acceptable.
- **The startup ordering is**: migrations (T-201) run via the entrypoint, then lifespan calls `ensure_workflows`. If the migration's pre-flight refuses on populated tables, the orchestrator surfaces it via `entrypoint.sh` exit code, not via the lifespan. Operator sees one clear error.

---

## Phase 3 — Verification

### T-203: Tenant-switch + stale-cache integration coverage

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-202

**Description:**
Lock the BUG-002 repro into a regression test. Two new test cases under `tests/modules/ai/lifecycle/test_bootstrap.py` that exercise the tenant-aware lookup and the stale-cache 404 recovery path against a stubbed engine client.

**Rationale:**
Without a regression test, the next refactor of `bootstrap.py` re-introduces the bug silently. Lock the contract now while the BUG-002 brief is fresh.

**Test Cases:**

1. **Tenant change does not return the prior tenant's id.**
   - Seed `engine_workflows` with `(tenant_a_id, work_item_workflow, <UUID-A>)`.
   - Call `ensure_workflows(db, client_for_tenant_b, tenant_id=tenant_b_id)` against a stub engine client that returns `<UUID-B>` from `create_workflow` and 404s on `get_workflow_by_id`.
   - Assert: returned mapping has `<UUID-B>`, `engine_workflows` has both rows (A and B), no cross-contamination.

2. **Stale cache 404 triggers re-resolution.**
   - Seed `engine_workflows` with `(tenant_a_id, work_item_workflow, <STALE-UUID>)`.
   - Stub `client.get_workflow_by_id(STALE-UUID)` → 404. Stub `client.get_workflow_by_name("work_item_workflow")` → `<NEW-UUID>` (or `create_workflow` if name is free).
   - Call `ensure_workflows(db, client, tenant_id=tenant_a_id)`.
   - Assert: returned mapping has `<NEW-UUID>`; `engine_workflows` for `(tenant_a_id, work_item_workflow)` now has `<NEW-UUID>` (stale row deleted, new row inserted).

3. **Cold boot in solo tenant — sanity.**
   - Empty `engine_workflows`. Stub `client.create_workflow` → `<UUID>`.
   - Call `ensure_workflows(db, client, tenant_id=t)`.
   - Assert: cache populated with `(t, name, <UUID>)`; second call (same db + client) hits the cache and skips `create_workflow`.

4. **Lifespan validation: missing `FLOW_ENGINE_TENANT_ID` with engine configured.**
   - Test that constructs `Settings` with `flow_engine_lifecycle_base_url` set but `flow_engine_tenant_id` unset → either Pydantic validation error, or `lifespan` startup error; assert message references BUG-002.

5. **Migration round-trip (extends `test_migrations_roundtrip.py`).**
   - Apply migration on empty schema, downgrade, re-apply. Assert column / PK shape after each step.
   - Apply migration on populated `engine_workflows` → expect the pre-flight `op.execute('SELECT 1/0')` style refusal or explicit Python raise.

**Verification Steps:**
1. `uv run pytest tests/modules/ai/lifecycle/test_bootstrap.py -v` — all five new cases green.
2. `uv run pytest tests/test_migrations_roundtrip.py -v` — round-trip + populated-table refusal cases green.
3. `uv run pytest` — full suite green (943+ passes, no regressions).
4. `uv run pyright src/app/modules/ai/lifecycle/bootstrap.py src/app/lifespan.py src/app/config.py` — clean.
5. Manual: re-run the BUG-002 reproduction (boot against tenant A, switch env to tenant B, boot again) — confirm the orchestrator now routes to tenant B's workflow ids without manual `TRUNCATE`.

---

## Summary

**Most likely root cause hypothesis:** confirmed in BUG-002 — `engine_workflows` PK omits tenant scope, and there is no engine-side validation of cached ids on subsequent boots. The fix is two-step: schema gets the `tenant_id` column + composite PK; bootstrap uses it on lookup + adds 404 recovery on cache hits.

**Confidence level in diagnosis:** High. The cache lookup is an 8-line function; the schema is a single declared column. The smoke test reproduced it deterministically.

**Risk assessment of proposed fix:**
- *Schema migration* is destructive on populated tables. Mitigated by the pre-flight refusal + the truncate cost being functionally zero (re-bootstrap re-fetches from the engine).
- *Eager validation* adds one engine round-trip per declared workflow per startup. Acceptable; opt out by switching to lazy validation in T-202 if T-200 finds a reason.
- *New `FLOW_ENGINE_TENANT_ID` setting* is a breaking change for operators using `flow_engine_lifecycle_base_url`. Documented in T-201 + T-202 acceptance criteria; the lifespan refusal makes the requirement explicit at first start.

**Monitoring recommendations post-fix:**
- Add a structured log line in the stale-cache recovery branch (`log.warning("stale engine_workflows row tenant=%s name=%s old_id=%s new_id=%s")`). If this fires regularly post-deploy, that is a signal the engine's data is being reset more often than expected — surface it.
- Track the lifespan boot duration. The eager-validation round-trip should add tens of ms; if it inflates by orders of magnitude, the engine call is timing out and the bootstrap should switch to lazy validation.

**Related areas to audit for similar issues:**
- T-200's audit step is the formal version of this. Specifically: any other table the orchestrator uses as a *lookup cache* of engine-side identifiers (vs as a *reference* to data already scoped to one tenant). The brief's §6 already argues there is none, but re-confirm during T-200.

**Suggested PR shape:** one PR per task is preferable for review. T-201 is a pure schema + model change, T-202 is the runtime change, T-203 is tests + the manual repro confirmation. T-200's investigation note can land inline in T-201's PR description rather than as its own commit since it is documentation that informs the implementation.
