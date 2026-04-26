# Bug Report: BUG-002 — `engine_workflows` cache is not tenant-scoped

> **Purpose**: Capture a concurrency / multi-tenancy gap surfaced by the orchestrator + flow-engine smoke test, ahead of fix scoping.
> **Template reference**: `.ai-framework/templates/bug-report.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-002 |
| **Summary** | `engine_workflows` cache table keys workflow ids by `name` only; switching the configured tenant silently returns the prior tenant's ids. |
| **Severity** | Medium |
| **Status** | Resolved |
| **Reported By** | Smoke test running orchestrator + flow-engine together (DevTools umbrella adaptation) |
| **Date Reported** | 2026-04-25 |
| **Date First Observed** | 2026-04-25 |

### Severity Justification

In single-tenant production this never fires — the orchestrator points at one tenant for its lifetime and the cache is consistent. The bug bites in three real-world scenarios: (1) integration / smoke testing where each run typically targets a fresh tenant, (2) tenant rotation in dev or staging when the flow engine's data is reset behind the same name, (3) operator misconfiguration that points one orchestrator at a different tenant via `FLOW_ENGINE_TENANT_API_KEY` without clearing the cache. None of those are user-facing today, but all three will become routine under the DevTools umbrella where multiple developers share one Postgres cluster — so this is medium, not low.

---

## 2. Steps to Reproduce

**Preconditions:** flow engine running with at least one tenant; orchestrator deployed against that tenant; `engine_workflows` table populated by a prior `lifespan.ensure_workflows()` boot.

1. Boot the orchestrator against tenant A. Confirm `engine_workflows` has `(name='work_item_workflow', engine_workflow_id=<UUID-A>)`.
2. Stop the orchestrator. Reconfigure it to authenticate as tenant B (different `FLOW_ENGINE_TENANT_API_KEY`). Tenant B has its own freshly-created workflows in the engine — different `engine_workflow_id`s.
3. Boot the orchestrator. `lifespan.ensure_workflows()` reads `engine_workflows`, finds `name='work_item_workflow'`, returns `<UUID-A>`. The engine create path is skipped.
4. POST a lifecycle signal (e.g. `/api/v1/work-items`). The signal adapter calls the engine with `<UUID-A>` as the workflow id.
5. **Observe:** the engine returns 404 (workflow id `<UUID-A>` is not visible to tenant B). Operator's smoke test loop uses `TRUNCATE engine_workflows` between runs as a workaround.

**Reproducibility:** Always — the bug is deterministic given the cache state and tenant change.

---

## 3. Expected vs Actual Behavior

### Expected Behavior

`ensure_workflows()` should return the engine's workflow ids for the *currently configured tenant*. Cache entries from a different tenant must not be returned. If the cache has no entry for the current tenant, the bootstrap should create-or-lookup the workflow against the engine and cache the result keyed by `(tenant_id, name)`.

This matches FEAT-008's engine-as-authority claim: the engine is the source of truth, the orchestrator caches engine identifiers for read-time convenience, and a stale cache must never override what the engine would say.

### Actual Behavior

`engine_workflows` has a single-column primary key on `name`. `bootstrap._resolve()` reads the cache with `WHERE name = :name` only — no tenant filter, because tenant identity is not in the schema. After a tenant switch the orchestrator transitions items in tenant B using workflow ids minted for tenant A, and every transition fails (or worse, succeeds against the wrong tenant if engine permissions were ever loosened).

The 409-fallback path in `_resolve()` covers the inverse case (cache wiped, engine still has the workflow) but there is no fallback for the relevant case here (cache populated, engine doesn't recognize the id). The engine call fails and the lifespan / signal handler bubbles a runtime error.

---

## 4. Environment

| Field | Value |
|-------|-------|
| **App Version** | main as of 2026-04-25 (after PR #28) |
| **Platform** | Any — bug is in the data model, not platform-specific |
| **User Context** | Operator running orchestrator + engine integration; smoke-test harness; future multi-orchestrator umbrella deployments |
| **Deployment** | Reproducible in dev + staging + production any time the configured tenant changes |

---

## 5. Error Evidence

### Error Messages / Logs

The exact error depends on the engine's response shape, but the smoke test surfaced this as a 404 from the engine when the orchestrator tried to transition an item:

```
EngineError: flow-engine returned 404 for POST /workflows/<UUID-A>/items/<item-id>/transitions
  engine_correlation_id=...
  body={"detail": "workflow not found in tenant scope"}
```

The orchestrator's `engine_workflows` table at the time of the failure:

```
 name                | engine_workflow_id                   | created_at
---------------------+--------------------------------------+----------------------------
 work_item_workflow  | <UUID-A>                             | 2026-04-25 12:00:00+00
 task_workflow       | <UUID-A2>                            | 2026-04-25 12:00:00+00
```

(Both ids belong to tenant A, but the orchestrator is now configured for tenant B.)

### Network / API Evidence

The orchestrator's outbound call to the engine carries a JWT minted from `FLOW_ENGINE_TENANT_API_KEY` (so the engine knows the request is from tenant B), but the URL path encodes `<UUID-A>` (tenant A's workflow id). That mismatch is what the engine rejects.

### Screenshots / Recordings

N/A.

---

## 6. Additional Context

| Field | Value |
|-------|-------|
| **Frequency** | Always under tenant change; never under stable single-tenant operation |
| **First occurrence** | Surfaced 2026-04-25 by the orchestrator + engine smoke test; the bug has existed since FEAT-006/T-129 introduced `engine_workflows` (2026-04-19). |
| **Workaround exists** | Yes — `TRUNCATE engine_workflows` between tenant changes. The smoke-test harness already does this. |
| **Related bugs** | None |
| **Regression** | No — this has never worked across tenants. |

### Observations

- The cache row also has no `tenant_id` column on which to filter, so even adding a `WHERE` clause to `_resolve()` requires a schema change first.
- The 409-on-create fallback in `_resolve()` was designed for "cache wiped, engine still has workflow" recovery. It does not help here because the cache *hits* with the wrong id; the create call is never made.
- Under the DevTools umbrella (parent doc: `~/Desktop/Repos/DevTools/devtools-umbrella.md`) multiple orchestrator instances may eventually share one Postgres database. If they ever pointed at different tenants while sharing `engine_workflows`, they would race and intermittently see each other's ids — same root cause, harder to debug.
- A separate but adjacent gap: even within a single tenant, if the engine's data is reset (workflows recreated with new ids), the orchestrator's cache becomes stale with no automatic recovery. Worth fixing in the same change since the failure mode and code path are identical.

---

## 7. Affected Entities and Components

| Entity / Component | How Affected | Reference |
|--------------------|-------------|-----------|
| `EngineWorkflow` model | Primary key is `name` only — needs `tenant_id` column to disambiguate. | `src/app/modules/ai/models.py:536` |
| `lifecycle/bootstrap.py` | `_resolve()` cache lookup is tenant-blind; `_upsert_cache()` writes tenant-blind rows. | `src/app/modules/ai/lifecycle/bootstrap.py:53-99` |
| `engine_workflows` table | Schema migration required: add `tenant_id`, change PK to `(tenant_id, name)`. Pre-flight should refuse on populated tables (operator must `TRUNCATE` so re-bootstrap re-fetches). | New Alembic migration |
| `lifespan.py` | Calls `ensure_workflows()` at startup and stashes the resulting `dict[name, uuid]` on `app.state.lifecycle_workflow_ids`. The dict shape stays the same; the bootstrap just needs the tenant id passed in. | `src/app/lifespan.py` |
| `engine_client.FlowEngineLifecycleClient` | Has the JWT material; the tenant id can be derived from the JWT subject or from a new explicit `tenant_id` setting alongside `FLOW_ENGINE_TENANT_API_KEY`. | `src/app/modules/ai/lifecycle/engine_client.py` |

---

## 8. Impact Assessment

| Dimension | Assessment |
|-----------|------------|
| **Users affected** | Operators running smoke / integration tests; future umbrella deployments. Single-tenant prod is unaffected today. |
| **Feature affected** | FEAT-006 (deterministic lifecycle flow — engine bootstrap) and indirectly FEAT-008 (engine-as-authority — the cache silently overrides the engine's view under tenant churn). |
| **Data impact** | Incorrect routing only; no DB corruption. The orchestrator writes outbox rows + status caches scoped to its own tables, which remain consistent within the orchestrator. The mismatch is between orchestrator-side cached identifiers and engine-side records. |
| **Business impact** | Operational — adds a manual `TRUNCATE` step to any tenant change. Will block clean multi-orchestrator umbrella deployments later. |

---

## 9. Traceability

| Reference | Link |
|-----------|------|
| **Related Feature** | FEAT-006 (introduced `engine_workflows` in T-129) |
| **Violated AC** | None directly — FEAT-006 was scoped single-tenant. The bug is a latent assumption, not a regression of a stated AC. |
| **Spec Reference** | `docs/data-model.md` § EngineWorkflow (description does not mention tenant scope — should also be updated). |
| **Related Work Items** | FEAT-008 (engine-as-authority — tightens the contract this bug violates); umbrella adaptation PRs #27 + #28 (surfaced the bug during smoke testing). |

---

## 10. Fix Sketch (for task generation, not part of the bug report contract)

When this is picked up, a single PR should:

1. **Schema.** Alembic migration adding `tenant_id uuid NOT NULL` to `engine_workflows`, dropping the PK on `name`, recreating it as `PRIMARY KEY (tenant_id, name)`. Pre-flight: refuse on populated tables (operator must `TRUNCATE engine_workflows` first so re-bootstrap re-fetches against the current tenant). Mirrors the FEAT-008/T-168 destructive-pre-flight pattern.
2. **Tenant identity.** Derive the tenant id from the JWT minted from `FLOW_ENGINE_TENANT_API_KEY` (the JWT subject claim) — or add an explicit `FLOW_ENGINE_TENANT_ID` setting beside the API key, mandatory whenever `FLOW_ENGINE_LIFECYCLE_BASE_URL` is configured. The latter is simpler and avoids JWT introspection in the bootstrap.
3. **Bootstrap.** Plumb tenant id into `ensure_workflows(db, client, tenant_id)`; cache reads / writes filter / write `(tenant_id, name)`.
4. **Stale-cache recovery.** When a cached id makes the engine 404, treat it as a stale entry: log, delete the cache row, re-resolve via the existing create-or-409-lookup path, return the new id. Same code path covers tenant change *and* in-tenant data reset.
5. **Comment in `bootstrap.py`** removed once landed; until then, leave a `FIXME(BUG-002)` next to the cache lookup so the next reader sees the gap before stepping on it.

Out of scope for the fix:
- Multi-tenant per orchestrator instance (one orchestrator process serving multiple tenants concurrently). Single-tenant per process stays the contract; this bug is about *changing* the tenant cleanly, not running many.
- Bootstrapping different workflow declarations per tenant. Declarations remain Python constants per FEAT-006/T-129's design.
