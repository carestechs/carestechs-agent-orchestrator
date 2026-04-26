# Implementation Plan: BUG-002 — `engine_workflows` cache is not tenant-scoped

## Task Reference
- **Bug brief:** [`docs/work-items/BUG-002-engine-workflows-tenant-scope.md`](../docs/work-items/BUG-002-engine-workflows-tenant-scope.md)
- **Task list:** [`tasks/BUG-002-tasks.md`](../tasks/BUG-002-tasks.md) — T-200 (investigation), T-201 (schema), T-202 (runtime), T-203 (tests).
- **Workflow:** investigation-first, then standard.
- **Complexity:** S + S + M + M = ~1.5 dev days end-to-end.
- **Rationale:** the bug brief's §10 fix sketch is concrete; this plan turns it into ordered, file-level edits with exact code shapes for each step.

## Overview

Single-PR fix in three commits, gated by a one-pager investigation up front:

1. **T-200 investigation** lands as a 3-paragraph note on the PR description (or appended to BUG-002 §6) before any code change. Confirms the tenant-id source and rules out other tenant-blind caches.
2. **T-201 schema** — Alembic migration adds `tenant_id` to `engine_workflows`, makes the PK composite, refuses on populated tables.
3. **T-202 runtime** — `lifecycle.bootstrap.ensure_workflows` becomes tenant-aware and gains stale-cache 404 recovery; `Settings` gains `flow_engine_tenant_id`; lifespan refuses to boot if engine is configured but tenant id is not.
4. **T-203 tests** — five new cases in `tests/modules/ai/lifecycle/test_bootstrap.py` + a migration round-trip case.

The whole thing lands in one PR because the schema and the runtime are inseparable: shipping just the migration breaks every operator with `engine_workflows` rows; shipping just the runtime fails type-checks against the model. Reviewers can still read the diff per-file.

## Implementation Steps

### Step 0: T-200 investigation

**Action:** Investigation. No file changes.

Read `src/app/modules/ai/lifecycle/engine_client.py` end-to-end. Specifically locate:
- How the JWT is minted from `FLOW_ENGINE_TENANT_API_KEY`. Is the tenant id present as a discrete claim, or only embedded in the subject? Decide: explicit `FLOW_ENGINE_TENANT_ID` setting (preferred) vs JWT introspection inside the bootstrap (fragile).
- Whether `client.get_workflow_by_id(...)` exists. If not, the eager-validation path in T-202 needs to add it; trivial — just a GET against `/workflows/<id>`.

Run:

```bash
rg -n "engine_item_id|engine_workflow_id" src/app/
```

For each hit, classify:
- **Cache** (rebuilt across runs from engine state): tenant-blind = bug. Currently only `engine_workflows`.
- **Reference** (set once when a record is created in a known-tenant context, never re-resolved against a different tenant): tenant scope is implicit in the row's other foreign keys. Examples: `tasks.engine_item_id`, `work_items.engine_item_id`. Not a bug.

Document the classification in 3 paragraphs. Either append to BUG-002 §6 in the same PR (recommended, since BUG-002 is already merged and this completes the brief) or paste into the PR description.

**Decision expected:** explicit `FLOW_ENGINE_TENANT_ID` setting; `engine_workflows` is the only tenant-blind cache; eager validation chosen for stale-cache recovery (deterministic startup over lazy first-failed-signal).

If the investigation contradicts any of those, stop and update the task list before coding — re-deriving the implementation under a different tenant-id source costs an hour, debugging it after merge costs days.

### Step 1: T-201 — schema migration

**File:** `src/app/migrations/versions/<rev>_engine_workflows_add_tenant_id.py`
**Action:** Create

```python
"""engine_workflows: add tenant_id, change PK to (tenant_id, name) (BUG-002)

Revision ID: <auto>
Revises: a1e4d58c9033
Create Date: 2026-04-26

The cache key was tenant-blind, so switching FLOW_ENGINE_TENANT_API_KEY
caused the orchestrator to return the prior tenant's engine_workflow_id
on every lookup. See docs/work-items/BUG-002-engine-workflows-tenant-scope.md.

This migration is destructive in the operator-facing sense: it refuses
to run while engine_workflows has rows. Operators must TRUNCATE before
upgrading. Truncation has no functional cost — the orchestrator's
lifespan re-bootstraps the cache against the current tenant on next
boot via one engine round-trip per declared workflow.
"""
from alembic import op
import sqlalchemy as sa


revision = "<auto>"
down_revision = "a1e4d58c9033"
branch_labels = None
depends_on = None


def _refuse_if_populated(direction: str) -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM engine_workflows")).scalar()
    if count:
        raise RuntimeError(
            f"engine_workflows has {count} row(s); TRUNCATE engine_workflows "
            f"before {direction}-applying this migration so the orchestrator "
            f"re-bootstraps against the current tenant. See BUG-002."
        )


def upgrade() -> None:
    _refuse_if_populated("up")
    op.drop_constraint("engine_workflows_pkey", "engine_workflows", type_="primary")
    op.add_column(
        "engine_workflows",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_primary_key(
        "engine_workflows_pkey",
        "engine_workflows",
        ["tenant_id", "name"],
    )


def downgrade() -> None:
    _refuse_if_populated("down")
    op.drop_constraint("engine_workflows_pkey", "engine_workflows", type_="primary")
    op.drop_column("engine_workflows", "tenant_id")
    op.create_primary_key(
        "engine_workflows_pkey",
        "engine_workflows",
        ["name"],
    )
```

Generate the revision via `uv run alembic revision -m "engine_workflows add tenant_id (BUG-002)"`, then replace the body with the above. The actual revision id is autogenerated; `down_revision` should be whatever the current head is (currently `a1e4d58c9033` from T-168).

**File:** `src/app/modules/ai/models.py`
**Action:** Modify

```python
class EngineWorkflow(Base):
    """Local cache of a flow-engine workflow ID keyed by (tenant, name).

    BUG-002: the original PK was `name` only; under a tenant change the
    cache returned the prior tenant's engine_workflow_id and every
    transition 404'd. Composite PK fixes that.
    """

    __tablename__ = "engine_workflows"

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    engine_workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

**File:** `tests/modules/ai/test_models.py`
**Action:** Modify

Find the assertion list for `EngineWorkflow` columns and add `tenant_id`. Update any composite-PK assertion if one exists (most likely there isn't — add a new `assert EngineWorkflow.__table__.primary_key.columns.keys() == ["tenant_id", "name"]` near the column check).

Verify:

```bash
uv run alembic upgrade head           # against an empty test DB — clean
uv run alembic downgrade -1           # round-trip
uv run alembic upgrade head           # back to the new shape
uv run pytest tests/modules/ai/test_models.py
```

### Step 2: T-202 — bootstrap edits + Settings

**File:** `src/app/config.py`
**Action:** Modify

Add the new setting next to the existing `flow_engine_lifecycle_base_url` / `flow_engine_tenant_api_key`:

```python
class Settings(BaseSettings):
    ...
    flow_engine_lifecycle_base_url: AnyHttpUrl | None = None
    flow_engine_tenant_api_key: SecretStr | None = None
    flow_engine_tenant_id: uuid.UUID | None = None
    ...

    @model_validator(mode="after")
    def _validate_engine_lifecycle_settings(self) -> "Settings":
        if self.flow_engine_lifecycle_base_url is not None:
            missing = []
            if self.flow_engine_tenant_api_key is None:
                missing.append("FLOW_ENGINE_TENANT_API_KEY")
            if self.flow_engine_tenant_id is None:
                missing.append("FLOW_ENGINE_TENANT_ID")
            if missing:
                raise ValueError(
                    f"FLOW_ENGINE_LIFECYCLE_BASE_URL is set but "
                    f"{', '.join(missing)} is not. See BUG-002."
                )
        return self
```

If a `model_validator` for engine settings already exists, extend it rather than adding a second.

**File:** `src/app/modules/ai/lifecycle/bootstrap.py`
**Action:** Modify

```python
async def ensure_workflows(
    db: AsyncSession,
    client: FlowEngineLifecycleClient,
    *,
    tenant_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    """Ensure every declared workflow exists in the engine for *tenant_id*.

    Cache key is (tenant_id, name) — see BUG-002. Tenant-scoped cache
    hits are validated against the engine via ``get_workflow_by_id``;
    a 404 means the cache row is stale (engine data reset, or the row
    was orphaned by a prior bug) and triggers re-resolution.
    """
    result: dict[str, uuid.UUID] = {}
    for decl in declarations.ALL_WORKFLOWS:
        name: str = decl["name"]
        workflow_id = await _resolve(db, client, decl, tenant_id=tenant_id)
        result[name] = workflow_id
    return result


async def _resolve(
    db: AsyncSession,
    client: FlowEngineLifecycleClient,
    decl: dict[str, Any],
    *,
    tenant_id: uuid.UUID,
) -> uuid.UUID:
    name = decl["name"]

    cached = await db.scalar(
        select(EngineWorkflow.engine_workflow_id).where(
            EngineWorkflow.tenant_id == tenant_id,
            EngineWorkflow.name == name,
        )
    )
    if cached is not None:
        if await _engine_recognizes(client, cached):
            logger.debug("workflow %s resolved from cache: %s", name, cached)
            return cached
        logger.warning(
            "stale engine_workflows row tenant=%s name=%s old_id=%s — re-resolving",
            tenant_id, name, cached,
        )
        await db.execute(
            EngineWorkflow.__table__.delete().where(
                EngineWorkflow.tenant_id == tenant_id,
                EngineWorkflow.name == name,
            )
        )
        await db.commit()

    try:
        engine_id = await client.create_workflow(
            name=name,
            statuses=decl["statuses"],
            transitions=decl["transitions"],
            initial_status=decl["initial_status"],
        )
        logger.info("workflow %s created in engine: %s", name, engine_id)
    except EngineError as exc:
        if exc.engine_http_status != 409:
            raise
        existing = await client.get_workflow_by_name(name)
        if existing is None:
            raise EngineError(
                f"engine reported 409 for workflow {name} but lookup returned None",
            ) from exc
        engine_id = existing
        logger.info("workflow %s already exists in engine: %s", name, engine_id)

    await _upsert_cache(db, tenant_id=tenant_id, name=name, engine_id=engine_id)
    await db.commit()
    return engine_id


async def _engine_recognizes(
    client: FlowEngineLifecycleClient, engine_id: uuid.UUID
) -> bool:
    """Return False on engine 404 (stale cache); re-raise other errors."""
    try:
        return await client.get_workflow_by_id(engine_id) is not None
    except EngineError as exc:
        if exc.engine_http_status == 404:
            return False
        raise


async def _upsert_cache(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    engine_id: uuid.UUID,
) -> None:
    stmt = (
        pg_insert(EngineWorkflow)
        .values(tenant_id=tenant_id, name=name, engine_workflow_id=engine_id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "name"])
    )
    await db.execute(stmt)
```

Notes:
- The `FIXME(BUG-002)` comment from PR #29 is removed. Replace with a one-line "tenant_id required — see BUG-002" so the rationale survives.
- The cache-hit branch now does an extra engine round-trip per declared workflow per startup. With two workflows, this adds tens of ms; acceptable. Track the lifespan boot duration post-deploy.

**File:** `src/app/modules/ai/lifecycle/engine_client.py`
**Action:** Modify (add method if missing)

```python
async def get_workflow_by_id(self, workflow_id: uuid.UUID) -> dict[str, Any] | None:
    """GET /workflows/{id} — returns the row, or None on 404."""
    try:
        response = await self._client.get(f"/workflows/{workflow_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise self._wrap_error(exc) from exc
```

Use whatever wrapping helper the file already has (likely `_wrap_error` or similar). Mirror the shape of `get_workflow_by_name`.

**File:** `src/app/lifespan.py`
**Action:** Modify

Find the `ensure_workflows` call site and pass `tenant_id`:

```python
workflow_ids = await ensure_workflows(
    db, lifecycle_client, tenant_id=settings.flow_engine_tenant_id
)
```

Settings validation in Step 2 already enforced that `flow_engine_tenant_id` is set whenever `flow_engine_lifecycle_base_url` is — so no None-check needed here.

**Files:** `.env.example` and `.env.production.example`
**Action:** Modify

Add `FLOW_ENGINE_TENANT_ID` next to `FLOW_ENGINE_TENANT_API_KEY`, with a comment that it is required when `FLOW_ENGINE_LIFECYCLE_BASE_URL` is set:

```bash
# Required when FLOW_ENGINE_LIFECYCLE_BASE_URL is set (BUG-002): the
# orchestrator's engine_workflows cache is keyed by (tenant_id, name);
# the tenant id is the one that owns the items the orchestrator reads
# and writes through the engine API. Get it from your engine admin /
# JWT subject. Required as a UUID.
# FLOW_ENGINE_TENANT_ID=
```

Verify:

```bash
uv run pyright src/app/modules/ai/lifecycle/bootstrap.py src/app/lifespan.py src/app/config.py src/app/modules/ai/lifecycle/engine_client.py
uv run ruff check src/app/modules/ai/lifecycle/bootstrap.py src/app/lifespan.py src/app/config.py src/app/modules/ai/lifecycle/engine_client.py
```

### Step 3: T-203 — tests

**File:** `tests/modules/ai/lifecycle/test_bootstrap.py`
**Action:** Modify

Five new cases. Use the existing test fixtures — there should already be a stub `FlowEngineLifecycleClient` mock in this file or one of its fixtures.

```python
async def test_ensure_workflows_does_not_return_other_tenants_id(
    db_session: AsyncSession,
) -> None:
    """BUG-002 regression: tenant change must not return tenant A's id."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    uuid_a, uuid_b = uuid.uuid4(), uuid.uuid4()

    db_session.add(
        EngineWorkflow(
            tenant_id=tenant_a, name="work_item_workflow", engine_workflow_id=uuid_a
        )
    )
    await db_session.commit()

    client = _stub_client_returning(create_workflow=uuid_b, get_by_id=None)
    result = await ensure_workflows(db_session, client, tenant_id=tenant_b)

    assert result["work_item_workflow"] == uuid_b
    rows = (await db_session.scalars(select(EngineWorkflow))).all()
    assert {(r.tenant_id, r.engine_workflow_id) for r in rows} == {
        (tenant_a, uuid_a),
        (tenant_b, uuid_b),
    }


async def test_ensure_workflows_recovers_from_stale_cache(
    db_session: AsyncSession,
) -> None:
    """BUG-002 regression: cached id the engine 404s on triggers re-resolve."""
    tenant_id = uuid.uuid4()
    stale_id, new_id = uuid.uuid4(), uuid.uuid4()

    db_session.add(
        EngineWorkflow(
            tenant_id=tenant_id, name="work_item_workflow", engine_workflow_id=stale_id
        )
    )
    await db_session.commit()

    client = _stub_client_returning(get_by_id=lambda i: None, create_workflow=new_id)
    result = await ensure_workflows(db_session, client, tenant_id=tenant_id)

    assert result["work_item_workflow"] == new_id
    rows = (
        await db_session.scalars(
            select(EngineWorkflow).where(EngineWorkflow.tenant_id == tenant_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].engine_workflow_id == new_id


async def test_ensure_workflows_caches_on_cold_boot(
    db_session: AsyncSession,
) -> None:
    """Sanity: empty cache → create + cache; second call hits cache."""
    tenant_id = uuid.uuid4()
    new_id = uuid.uuid4()
    client = _counting_stub(create_workflow=new_id, get_by_id=new_id)

    await ensure_workflows(db_session, client, tenant_id=tenant_id)
    await ensure_workflows(db_session, client, tenant_id=tenant_id)

    assert client.create_workflow_calls == 1   # cold boot only
    assert client.get_by_id_calls == 1         # second boot validated cache once


def test_settings_refuse_engine_url_without_tenant_id() -> None:
    """BUG-002: lifespan must refuse to boot if tenant id is missing."""
    with pytest.raises(ValidationError, match="FLOW_ENGINE_TENANT_ID"):
        Settings(
            ...,  # required base fields
            flow_engine_lifecycle_base_url="http://engine.test",
            flow_engine_tenant_api_key=SecretStr("k"),
            # flow_engine_tenant_id deliberately unset
        )
```

`_stub_client_returning` and `_counting_stub` are local helpers in the test file — write them inline or use whatever stub pattern is already in `tests/modules/ai/lifecycle/test_bootstrap.py`. If the existing stub uses `unittest.mock.AsyncMock`, follow that.

**File:** `tests/test_migrations_roundtrip.py`
**Action:** Modify

Add a case that exercises the populated-table refusal:

```python
def test_engine_workflows_tenant_id_migration_refuses_on_populated_table(
    test_database_url: str,
) -> None:
    """BUG-002: pre-flight refuses the destructive migration on populated tables."""
    # Drive Alembic to the previous head, seed engine_workflows, attempt upgrade,
    # assert RuntimeError mentions BUG-002. Then TRUNCATE and re-run; assert clean.
```

If `test_migrations_roundtrip.py` already has a similar pattern for FEAT-008/T-168's destructive migration, copy that shape — same script, different revision target.

Verify:

```bash
uv run pytest tests/modules/ai/lifecycle/test_bootstrap.py -v
uv run pytest tests/test_migrations_roundtrip.py -v
uv run pytest                              # full suite — 943+ pass
uv run pyright tests/modules/ai/lifecycle/test_bootstrap.py
```

### Step 4: Manual repro confirmation

Local, against a real flow engine (or the stubbed integration harness from PR #28's smoke test):

1. Start the engine. Provision two tenants A and B.
2. Boot the orchestrator with `FLOW_ENGINE_TENANT_ID=<A>` and `FLOW_ENGINE_TENANT_API_KEY=<A's key>`. Confirm `engine_workflows` has `(<A>, work_item_workflow, <UUID-A>)`.
3. Stop the orchestrator. Reconfigure to tenant B (`FLOW_ENGINE_TENANT_ID=<B>` + B's key). Boot.
4. Confirm `engine_workflows` now has both A's and B's rows. POST a lifecycle signal — confirm it succeeds (engine accepts the transition under tenant B's workflow id).
5. Repeat without setting `FLOW_ENGINE_TENANT_ID`. Confirm boot fails with the validator error referencing BUG-002.

Step 4–5 are the BUG-002 §2 reproduction; success here is the bug closed.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/migrations/versions/<rev>_engine_workflows_add_tenant_id.py` | Create | Composite PK migration with destructive pre-flight. |
| `src/app/modules/ai/models.py` | Modify | `EngineWorkflow.tenant_id` + composite PK. |
| `src/app/modules/ai/lifecycle/bootstrap.py` | Modify | Tenant-aware lookup + stale-cache 404 recovery. |
| `src/app/modules/ai/lifecycle/engine_client.py` | Modify (likely) | Add `get_workflow_by_id` if missing. |
| `src/app/lifespan.py` | Modify | Pass `tenant_id` through to bootstrap. |
| `src/app/config.py` | Modify | `flow_engine_tenant_id` setting + validator. |
| `.env.example`, `.env.production.example` | Modify | Document the new required setting. |
| `tests/modules/ai/lifecycle/test_bootstrap.py` | Modify | Five regression cases. |
| `tests/test_migrations_roundtrip.py` | Modify | Populated-table refusal case. |
| `tests/modules/ai/test_models.py` | Modify | Composite PK + `tenant_id` column assertion. |
| `docs/data-model.md` | Modify | `EngineWorkflow` description: tenant-scoped cache, link BUG-002, changelog entry. |
| `docs/work-items/BUG-002-engine-workflows-tenant-scope.md` | Modify | Status → Resolved; append T-200 investigation note to §6. |

## Edge Cases & Risks

- **Operator forgets to TRUNCATE.** The pre-flight refusal on populated tables surfaces this at migration time with a BUG-002 reference. The orchestrator's entrypoint shells out to `alembic upgrade head` — failure exits non-zero, the container restarts, the operator sees the error in `docker logs`. Acceptable.
- **Tenant id doesn't actually identify the engine tenant.** If T-200 finds that the engine doesn't expose tenant id discretely (only embeds it in the JWT), the explicit `FLOW_ENGINE_TENANT_ID` setting becomes a self-attestation by the operator. Wrong values silently mis-key the cache. Mitigation: log the tenant id at startup (already in `logger.info` call from `_resolve`); operators see a mismatch immediately. Heavier mitigation (probe the engine for "what tenant am I?") is out of scope.
- **The `get_workflow_by_id` round-trip on every startup.** Adds two engine calls per boot (one per declared workflow). Acceptable today; revisit if the orchestrator gains many workflows or if startup latency becomes a bottleneck. A lazy-validation alternative (validate on first use, not at startup) is a 5-line pivot if needed.
- **Engine returns 5xx during validation.** The validator currently re-raises non-404 `EngineError`. If the engine is flaky at orchestrator startup, this fails the lifespan and the container restarts. That's the right behavior — the orchestrator can't operate without valid workflow ids — but document the failure mode in the bootstrap docstring.
- **Test isolation.** The new bootstrap tests run against `db_session` (the SAVEPOINT-wrapped session from `tests/conftest.py`). The migration test in `tests/test_migrations_roundtrip.py` uses a separate ad-hoc database — verify it follows the same teardown pattern as the existing T-168 round-trip test.

## Acceptance Verification

- [ ] T-200 investigation note appended to BUG-002 §6 (or in PR description) before any code commits.
- [ ] T-201: schema migration applies cleanly on empty tables, refuses on populated tables, round-trip works.
- [ ] T-202: cold boot caches `(tenant_id, name, engine_id)`; tenant-switch boot adds a new row without touching the prior tenant's; stale-cache hit triggers logged re-resolution; lifespan refuses to boot if `flow_engine_lifecycle_base_url` is set without `flow_engine_tenant_id`.
- [ ] T-203: 5 new bootstrap test cases + migration round-trip case pass.
- [ ] `uv run pytest` — full suite green (943+).
- [ ] `uv run pyright` clean on touched files.
- [ ] Manual BUG-002 §2 reproduction succeeds without `TRUNCATE engine_workflows`.
- [ ] BUG-002 work item Status → Resolved.
- [ ] `FIXME(BUG-002)` comment from PR #29 removed.
