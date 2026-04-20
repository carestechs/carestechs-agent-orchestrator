# Task Breakdown: FEAT-006 — Deterministic Lifecycle Flow

> **Source:** `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md`
> **Design input:** `docs/design/deterministic-flow-transitions.md`
> **Generated:** 2026-04-19
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

Twenty-one tasks, grouped: **Foundation** (5 entity/migration tasks) → **Service layer** (3 state-machine + cross-cutting tasks) → **Signal endpoints** (5 REST clusters) → **GitHub integration** (2 tasks) → **Testing** (4 integration/E2E tasks) → **Closeout** (2 tasks). Task IDs continue from FEAT-005's final `T-106`.

Critical path depth is 10: `T-107 → T-112 → T-115 → T-118 → T-120 → T-121 → T-122 → T-123 → T-125 → T-127`. Other chains (T-108→T-113→T-116; T-110→T-116; T-111→T-120) branch off in parallel once prerequisites land. Every task is `Workflow: standard`.

---

## Foundation

### T-107: `WorkItem` entity — SQLAlchemy model + migration + enums + DTO

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Add the `WorkItem` entity to persist the work-item state machine. SQLAlchemy model in `src/app/modules/ai/models.py` with all columns from `docs/data-model.md` §WorkItem. Alembic migration creates the `work_items` table and the supporting enum check constraints. New `WorkItemDto` in `schemas.py` with camelCase aliases. Introduces `WorkItemStatus` and `WorkItemType` text-enum check constraints per the project's "enums as text + check" convention.

**Rationale:**
The work-item state machine (`open → in_progress ⇄ locked → ready → closed`) is the entity at the top of FEAT-006's hierarchy. Every other entity references it; all signal endpoints that target a work-item resolve to a row here. Ships first so `Task.work_item_id` (T-108) has a target.

**Acceptance Criteria:**
- [ ] SQLAlchemy `WorkItem` model with all fields from the data-model doc (`id`, `external_ref`, `type`, `title`, `source_path`, `status`, `locked_from`, `opened_by`, `closed_at`, `closed_by`, `created_at`, `updated_at`).
- [ ] `external_ref` UNIQUE (`uq_work_items_external_ref`).
- [ ] BTREE on `(status, updated_at DESC)` (`ix_work_items_status_updated_at`).
- [ ] Check constraints: `status in (...)`, `type in (...)`, `locked_from in (...)` (nullable).
- [ ] Alembic migration round-trips cleanly on a local Postgres (`upgrade` then `downgrade`).
- [ ] `WorkItemDto` Pydantic model with camelCase aliases + `extra="forbid"`.
- [ ] `uv run pyright`, `uv run ruff check .`, `uv run pytest tests/modules/ai/test_models.py` green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `WorkItem` class.
- `src/app/modules/ai/schemas.py` — new `WorkItemDto`, enum `WorkItemStatus`, enum `WorkItemType`.
- `src/app/migrations/versions/<ts>_add_work_items.py` — new migration.
- `tests/modules/ai/test_models.py` — round-trip + constraint tests.

**Technical Notes:**
Keep enum values as Python `StrEnum` subclasses with lowercase snake_case; the DB check constraint uses the same strings. `locked_from` only accepts `in_progress` in v1 but the column is text — document in the model docstring that future states may be added. Do NOT cascade-delete `Task` rows when a work item is deleted; work items are never deleted, only closed.

---

### T-108: `Task` entity — SQLAlchemy model + migration + enums + DTO

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-107

**Description:**
Add the `Task` entity with its 9-value `TaskStatus` enum (including `deferred` terminal) and `ActorType` enum for proposer tracking. Migration adds `tasks` table with FK to `work_items`. New `TaskDto` in `schemas.py`.

**Rationale:**
Tasks carry the main state machine in FEAT-006 (proposed → done/deferred with rejection edges). All 10 task-targeting signals (S5-S14) write to this entity. Ships second so `TaskAssignment` and `Approval` have a target.

**Acceptance Criteria:**
- [ ] SQLAlchemy `Task` model with all fields per data-model doc.
- [ ] UNIQUE on `(work_item_id, external_ref)` (`uq_tasks_work_item_ref`).
- [ ] BTREE on `(work_item_id, status)` (`ix_tasks_work_item_status`).
- [ ] Check constraint on `status` covering all 9 values.
- [ ] FK `work_item_id → work_items.id` with `ON DELETE RESTRICT`.
- [ ] Alembic migration round-trips.
- [ ] `TaskDto` with camelCase aliases + optional `currentAssignment: TaskAssignmentDto | None` (populated by the service when loading; DB column N/A).
- [ ] `uv run pyright`, `ruff`, model tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `Task` class.
- `src/app/modules/ai/schemas.py` — `TaskDto`, `TaskStatus`, `ActorType`.
- `src/app/migrations/versions/<ts>_add_tasks.py`.
- `tests/modules/ai/test_models.py` — extend.

**Technical Notes:**
`currentAssignment` is a computed field on the DTO, not a column — populated by the service via a query against `TaskAssignment.superseded_at IS NULL`. Don't add a `current_assignment_id` FK on `Task` — it would denormalize and fight the append-only `TaskAssignment` model.

---

### T-109: `TaskAssignment` entity — SQLAlchemy model + migration + DTO

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-108

**Description:**
Append-only assignment history for tasks. One "active" row per task enforced by a partial unique index `(task_id) WHERE superseded_at IS NULL`. Adds `AssigneeType` enum. New `TaskAssignmentDto`.

**Rationale:**
The assignment row is what drives the approval matrix at `plan_review` and `impl_review` — routing depends on whether the active assignee is a dev or an agent. Append-only history also records reassignments for audit.

**Acceptance Criteria:**
- [ ] SQLAlchemy `TaskAssignment` model with all fields per data-model doc.
- [ ] Partial UNIQUE index `ix_task_assignments_active` on `(task_id) WHERE superseded_at IS NULL`.
- [ ] BTREE on `(task_id, assigned_at DESC)` (`ix_task_assignments_task_assigned`).
- [ ] FK `task_id → tasks.id` with `ON DELETE RESTRICT`.
- [ ] Check constraint on `assignee_type in ('dev','agent')`.
- [ ] Alembic migration round-trips.
- [ ] `TaskAssignmentDto` with camelCase aliases.
- [ ] `uv run pyright`, `ruff`, model tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `TaskAssignment` class.
- `src/app/modules/ai/schemas.py` — `TaskAssignmentDto`, `AssigneeType`.
- `src/app/migrations/versions/<ts>_add_task_assignments.py`.
- `tests/modules/ai/test_models.py` — constraint test proving the partial-unique index rejects a second active assignment.

**Technical Notes:**
Postgres partial-unique: `CREATE UNIQUE INDEX ... ON task_assignments (task_id) WHERE superseded_at IS NULL`. Use `sqlalchemy.Index` with `postgresql_where=...`. Tests must prove that a concurrent second INSERT raises `UniqueViolation` before the first row's `superseded_at` is updated.

---

### T-110: `Approval` entity — SQLAlchemy model + migration + enums + DTO

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-108

**Description:**
Append-only record of every approve/reject decision on a task across its three decision stages (`proposed`, `plan`, `impl`). Adds `ApprovalStage`, `ApprovalDecision`, and `ActorRole` enums. New `ApprovalDto`. The rejection-iteration count for a `(task_id, stage)` pair is derived by counting `decision='reject'` rows — no denormalization.

**Rationale:**
AC-5 (rejection loops preserve owner + attach feedback) and AC-8 (no duplicate approval rows under idempotency) depend on this table. Audit trail for "why we didn't do it that way" also lives here.

**Acceptance Criteria:**
- [ ] SQLAlchemy `Approval` model with all fields per data-model doc.
- [ ] BTREE on `(task_id, stage, decided_at)` (`ix_approvals_task_stage_time`).
- [ ] FK `task_id → tasks.id` with `ON DELETE RESTRICT`.
- [ ] Check constraints on `stage`, `decision`, `decided_by_role`.
- [ ] Service-layer check (not DB): `feedback` non-empty when `decision='reject'` (DB check would be awkward; tests assert the service guard).
- [ ] Alembic migration round-trips.
- [ ] `ApprovalDto` with camelCase aliases.
- [ ] `uv run pyright`, `ruff`, model tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `Approval` class.
- `src/app/modules/ai/schemas.py` — `ApprovalDto`, `ApprovalStage`, `ApprovalDecision`, `ActorRole`.
- `src/app/migrations/versions/<ts>_add_approvals.py`.
- `tests/modules/ai/test_models.py` — extend.

**Technical Notes:**
`decided_by_role` differs from `ActorType` (proposer) because approvers are always humans; an agent can *propose* but never *decide*. Keep the two enums distinct even though `admin` overlaps — fusing them invites confusion in T-113's approval-matrix logic.

---

### T-111: Extend `WebhookEvent` with `source` column + new event types

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add a `source text NOT NULL DEFAULT 'engine'` column to `webhook_events` and extend the event_type check constraint with `github_pr_opened` and `github_pr_closed`. Add `WebhookSource` enum (`engine`, `github`). The existing engine path is untouched (default `source='engine'`); the GitHub webhook (T-120) writes `source='github'`.

**Rationale:**
GitHub webhook intake needs a discriminator so the existing engine handlers don't accidentally consume a `pull_request` payload. Default value keeps all existing rows valid without a backfill.

**Acceptance Criteria:**
- [ ] Alembic migration adds `source text NOT NULL DEFAULT 'engine'` and drops/recreates the `event_type` check constraint with the two new values.
- [ ] SQLAlchemy model updated: `source: Mapped[str]` with the enum default, `event_type` typing widened.
- [ ] `WebhookEventDto` gains `source: str`.
- [ ] Migration `downgrade` is reversible.
- [ ] Existing webhook-event tests pass unchanged.
- [ ] Dedupe-key helper extended: `github:pr:<pr_number>:<delivery_id>` shape implemented and unit-tested; engine shape unchanged.
- [ ] `uv run pyright`, `ruff`, existing webhook tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — extend `WebhookEvent`.
- `src/app/modules/ai/schemas.py` — extend `WebhookEventDto`, add `WebhookSource`.
- `src/app/modules/ai/repository.py` — extend `compute_webhook_dedupe_key` (or equivalent) with a source-aware branch.
- `src/app/migrations/versions/<ts>_webhook_event_source.py` — new migration.
- `tests/modules/ai/test_repository.py` — dedupe-key shape tests for both sources.

**Technical Notes:**
Adding a column with a NOT NULL default to a large table normally requires batched backfill, but `webhook_events` in v1 is bounded and local-dev sized; a single `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT 'engine'` is fine. If the production footprint grows, revisit with a two-step migration (add nullable → backfill → alter NOT NULL).

---

## Service layer

### T-112: Work-item state-machine service + W2/W5 derivation

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-107, T-108

**Description:**
Service functions in `src/app/modules/ai/lifecycle/work_items.py` (new submodule) that perform work-item transitions (`open`, `transition_to_in_progress`, `lock`, `unlock`, `mark_ready`, `close`) with state guards. Also implements the two derived transitions: `maybe_advance_to_in_progress(work_item_id)` fires only when the caller reports "a task just reached `approved`" and the work-item is still `open`; `maybe_advance_to_ready(work_item_id)` fires when all child tasks are in `{done, deferred}` and the work-item is `in_progress`. Both are idempotent — re-calling when already in the target state returns the row unchanged without fighting concurrent writes.

**Rationale:**
AC-2 (every transition covered by tests), AC-3 (derivation idempotency), AC-9 (engine has zero logic — this task puts the logic in the orchestrator where it belongs). Service-layer is the right place: routes and CLI both call it, tests can drive it directly.

**Acceptance Criteria:**
- [ ] All 6 explicit transitions (W1, W3, W4, W6) + 2 derived (W2, W5) implemented as service functions.
- [ ] Each function uses `SELECT ... FOR UPDATE` on the work-item row to serialize concurrent writes.
- [ ] Illegal transitions raise `ConflictError` with a descriptive RFC 7807 detail.
- [ ] Derivation idempotent: calling `maybe_advance_to_in_progress` twice in a row is a no-op the second time; same for `maybe_advance_to_ready`.
- [ ] `maybe_advance_to_ready` requires `count(tasks) >= 1` before advancing (edge case: zero-task work-item stays `in_progress`).
- [ ] Locking blocks: `lock` only valid from `in_progress`; `unlock` only valid from `locked`; both rejected with `409` otherwise.
- [ ] `close` rejects with `409` if `status != ready`.
- [ ] Unit tests cover all 8 transitions + every illegal-transition case.
- [ ] `uv run pyright`, `ruff`, unit tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/__init__.py` — new submodule.
- `src/app/modules/ai/lifecycle/work_items.py` — transition functions.
- `tests/modules/ai/lifecycle/test_work_items.py` — unit tests.

**Technical Notes:**
Use `async with session.begin():` per transition to keep the lock scope tight. Don't put derivation inside the DB (no triggers) — fire it from the orchestrator after the parent signal's state write completes. Tests should drive against real Postgres per CLAUDE.md's "no SQLite" rule.

---

### T-113: Task state-machine service + T4 derivation + approval matrix

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-108, T-109, T-110

**Description:**
Service functions in `src/app/modules/ai/lifecycle/tasks.py` (new) for all task transitions plus `approval_matrix(task)` — given a task and its current assignment, returns the `ActorRole` required to approve at `plan_review` or `impl_review`. T4 (`approved → assigning`) fires as part of T2 (`propose → approve`). Rejection edges (T3, T8, T11) preserve owner and append an `Approval` row with feedback.

**Rationale:**
AC-2, AC-4 (role enforcement), AC-5 (rejection loops preserve owner), AC-6 (defer from any non-terminal), AC-10 (full E2E). Approval matrix is the single source of truth for "who can approve at which stage" — routes call into it, not into ad-hoc logic.

**Acceptance Criteria:**
- [ ] All 12 task transitions (T1-T11, T12 defer) implemented as service functions.
- [ ] T4 fires automatically inside the `approve_task` function after the state write.
- [ ] `approval_matrix(task)` returns `ActorRole.admin` when no assignment exists or when the active assignment is `agent`; returns `ActorRole.dev` (or `admin` in v1 solo-dev — see below) for dev-assigned at `plan_review`; returns `admin` for `impl_review` in v1.
- [ ] `reject_*` functions require non-empty feedback (raise `ValidationError` otherwise).
- [ ] `defer_task` rejects with `409` from terminal states (`done`, `deferred`).
- [ ] `v1 solo-dev` flag: `SOLO_DEV_MODE: bool` in `app.config` (default `True`). When `True`, `approval_matrix` collapses the `impl_review` approver to `admin` always. When `False`, returns `dev` (different from the implementer — reviewer-picker is out of scope; the service returns the required role but does not assign a specific person).
- [ ] Unit tests for every transition + every matrix combination (dev/agent × proposed/plan/impl × solo-dev on/off).
- [ ] `uv run pyright`, `ruff`, unit tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/tasks.py` — transition + matrix functions.
- `src/app/config.py` — add `solo_dev_mode: bool = True`.
- `tests/modules/ai/lifecycle/test_tasks.py` — unit tests.
- `tests/modules/ai/lifecycle/test_approval_matrix.py` — dedicated matrix tests.

**Technical Notes:**
`approval_matrix` is pure (given task + active assignment + config), no DB writes — keep it easy to unit-test. The caller (signal endpoint) loads the task+assignment, consults the matrix, and compares against `X-Actor-Role` (T-114). Don't put the role check inside the transition function — that mixes concerns.

---

### T-114: `X-Actor-Role` dependency + signal idempotency helper

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Two cross-cutting helpers consumed by every FEAT-006 signal endpoint. `src/app/modules/ai/dependencies.py` gains `require_actor_role(*allowed: ActorRole)` FastAPI dependency that reads `X-Actor-Role`, validates it against the allowed set, and raises `403` (RFC 7807) on mismatch. `src/app/modules/ai/lifecycle/idempotency.py` provides `compute_signal_key(entity_id, signal_name, payload)` (SHA-256 over canonical JSON) + `check_and_record(session, key)` which inserts into a new `lifecycle_signals` table returning `(is_new, first_seen_at)`. Endpoints branch on `is_new=False` to return `202` + `meta.alreadyReceived=true` without re-running side effects.

**Rationale:**
AC-4 (role enforcement), AC-8 (signal idempotency), AC-9 (composition-integrity — duplicate signals produce no duplicate state writes). Centralizing these avoids 14 near-identical copy-pastes in the signal handlers.

**Acceptance Criteria:**
- [ ] `require_actor_role` dependency: returns the validated role on success; raises `403` Problem Details with `detail="Actor role <role> not allowed for this endpoint"` on mismatch; raises `400` if header is missing on a role-required endpoint.
- [ ] `lifecycle_signals` table: `(key text PRIMARY KEY, entity_id uuid, signal_name text, recorded_at timestamptz default now())`. Alembic migration round-trips.
- [ ] `compute_signal_key` canonicalizes JSON: sorted keys, no whitespace, UTF-8. Same input → same key across runs.
- [ ] `check_and_record` uses `INSERT ... ON CONFLICT DO NOTHING RETURNING key`; `is_new = (returned row is not None)`.
- [ ] Unit tests cover: role mismatch, missing header, payload hash stability, ON CONFLICT path.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/dependencies.py` — extend with `require_actor_role`.
- `src/app/modules/ai/lifecycle/idempotency.py` — new module.
- `src/app/modules/ai/models.py` — new `LifecycleSignal` model.
- `src/app/migrations/versions/<ts>_add_lifecycle_signals.py`.
- `tests/modules/ai/test_dependencies.py` — extend.
- `tests/modules/ai/lifecycle/test_idempotency.py` — new.

**Technical Notes:**
Don't try to dedupe inside FastAPI middleware — the body needs parsing first to compute the hash, and middleware runs before body read. Keep it as a service call inside each handler. The `lifecycle_signals` table grows unboundedly; a retention job (out of scope) would prune after N days.

---

## Signal endpoints

### T-115: Work-item lifecycle endpoints — S1/S2/S3/S4

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-112, T-114

**Description:**
Implement `POST /api/v1/work-items`, `/{id}/lock`, `/{id}/unlock`, `/{id}/close` in `src/app/modules/ai/router.py`. All require `X-Actor-Role: admin`. S1 dispatches task-generation (stub: record an "awaiting tasks" marker; actual agent wiring is out of scope for FEAT-006).

**Rationale:**
AC-1 (14 signal surface), AC-10 (E2E). S1-S4 are the admin's primary handles on the work-item state.

**Acceptance Criteria:**
- [ ] All 4 endpoints live under `/api/v1/work-items/...` with the response envelope defined in `api-spec.md`.
- [ ] `require_actor_role(ActorRole.admin)` on each.
- [ ] `compute_signal_key` + `check_and_record` before side effects; idempotent replay returns `alreadyReceived=true`.
- [ ] S1 creates the work item and (v1 stub) records a `task_generation_dispatched` audit row; no actual agent invocation until follow-up FEAT.
- [ ] S2/S3/S4 delegate to T-112's service functions and surface `ConflictError` as `409`.
- [ ] Route tests (FastAPI `httpx.AsyncClient`) for each happy path + each illegal-state case (lock when already locked, close when not ready, etc.).
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — 4 new routes.
- `src/app/modules/ai/service.py` — 4 new service functions (thin adapters over T-112).
- `tests/modules/ai/test_router_work_items.py` — new test file.

**Technical Notes:**
The "dispatch task-generation" placeholder should be a named service function (`dispatch_task_generation(work_item)`) even if its v1 implementation only writes a log line — this is the seam where a follow-up FEAT wires in a specific agent.

---

### T-116: Task proposal + assignment endpoints — S5/S6/S7

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-113, T-114

**Description:**
Implement `POST /api/v1/tasks/{id}/approve`, `/reject`, `/assign`. All require `X-Actor-Role: admin`. `/approve` fires the W2 derivation (T-112) and T4 in-line. `/reject` writes an `Approval` row with non-empty feedback. `/assign` inserts a new `TaskAssignment` row (closing any prior active one via `superseded_at`).

**Rationale:**
AC-1, AC-3 (W2 derivation), AC-4 (role enforcement), AC-5 (rejection preserves owner).

**Acceptance Criteria:**
- [ ] 3 endpoints implemented with the envelope + status codes from `api-spec.md`.
- [ ] `/approve` fires T4 (`approved → assigning`) atomically (same transaction); then calls `maybe_advance_to_in_progress` on the parent work-item.
- [ ] `/reject` returns `422` on missing/empty feedback.
- [ ] `/assign` inserts + supersedes in the same transaction; returns `409` if called from a non-`assigning` state.
- [ ] Idempotency + role enforcement wired via T-114.
- [ ] Route tests for each endpoint including idempotent replay and illegal-state cases.
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — 3 new routes.
- `src/app/modules/ai/service.py` — 3 new service functions.
- `tests/modules/ai/test_router_tasks_proposal.py` — new test file.

**Technical Notes:**
The W2 derivation inside `/approve` must run after the task's state write commits — a nested transaction issue. Use a single outer transaction that writes the `approved` state, fires T4, and fires W2 as a sub-call; if any step fails the whole thing rolls back. Don't split across requests.

---

### T-117: Plan endpoints — S8/S9/S10

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-113, T-114

**Description:**
Implement `/tasks/{id}/plan`, `/plan/approve`, `/plan/reject`. Role enforcement via the approval matrix: `plan` submission accepts `dev` (dev-assigned) or `admin` (agent-assigned path, written by the orchestrator); `plan/approve` and `plan/reject` accept the role returned by `approval_matrix(task)` at stage `plan`.

**Rationale:**
AC-1, AC-4, AC-5.

**Acceptance Criteria:**
- [ ] 3 endpoints implemented.
- [ ] `/plan` stores `planPath` + `planSha` on a new `TaskPlan` row (simple append-only audit table: `id, task_id, plan_path, plan_sha, submitted_by, submitted_at`); transitions `planning → plan_review`.
- [ ] `/plan/approve` queries `approval_matrix(task, stage=plan)`, asserts `X-Actor-Role` matches, writes `Approval(decision=approve)`, transitions `plan_review → implementing`.
- [ ] `/plan/reject` requires non-empty feedback, writes `Approval(decision=reject)`, transitions back to `planning`.
- [ ] Idempotency + role via T-114 (role allowlist is matrix-derived per-request, not static).
- [ ] Route tests for each endpoint + matrix branches (dev-assigned + agent-assigned).
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `TaskPlan` entity (simple: id, task_id, plan_path, plan_sha, submitted_by, submitted_at).
- `src/app/migrations/versions/<ts>_add_task_plans.py`.
- `src/app/modules/ai/router.py` — 3 new routes.
- `src/app/modules/ai/service.py` — 3 new service functions.
- `tests/modules/ai/test_router_tasks_plan.py` — new test file.

**Technical Notes:**
The approval matrix is consulted inside the endpoint handler, not via a static `require_actor_role(...)` dependency — the allowed role depends on the task's current assignment. Keep the role check inline with the transition to avoid TOCTOU (task reassigned between check and transition). Use `SELECT ... FOR UPDATE` on the task row to serialize.

---

### T-118: Implementation + review endpoints — S11/S12/S13

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-113, T-114, T-117

**Description:**
Implement `/tasks/{id}/implementation` (S11 agent path), `/review/approve` (S12), `/review/reject` (S13). S12 triggers the W5 derivation and (if GitHub is configured — see T-121) releases the merge gate. S13 marks the PR check red. Role enforcement via the approval matrix at stage `impl`.

**Rationale:**
AC-1, AC-3 (W5), AC-5, AC-7 (GitHub check flip).

**Acceptance Criteria:**
- [ ] 3 endpoints implemented.
- [ ] `/implementation` stores `prUrl`, `commitSha`, `summary` on a new `TaskImplementation` row; transitions `implementing → impl_review`.
- [ ] `/review/approve` writes `Approval(decision=approve, stage=impl)`, transitions `impl_review → done`, fires `maybe_advance_to_ready` on the parent work-item. If GitHub is configured, resolves the `orchestrator/impl-review` check to `success` (delegated to T-121's client).
- [ ] `/review/reject` writes `Approval(decision=reject, stage=impl)`, transitions back to `implementing`. If GitHub is configured, resolves the check to `failure`.
- [ ] If GitHub is not configured, the Check calls are skipped with a single log line; the rest of the flow is unchanged (composition integrity per AC-9).
- [ ] Route tests mock T-121's client and assert the correct conclusions (`success` / `failure`) are passed.
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — new `TaskImplementation` entity (id, task_id, pr_url, commit_sha, summary, submitted_by, submitted_at).
- `src/app/migrations/versions/<ts>_add_task_implementations.py`.
- `src/app/modules/ai/router.py` — 3 new routes.
- `src/app/modules/ai/service.py` — 3 new service functions.
- `tests/modules/ai/test_router_tasks_review.py` — new test file.

**Technical Notes:**
The GitHub-client call is the first external-system integration. Inject it as a FastAPI dependency so tests can override it with a recording double. If the call fails transiently, log + continue (the orchestrator state is authoritative); if it fails authorization, surface a `500` — operator needs to fix the config.

---

### T-119: Defer endpoint — S14

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-113, T-114

**Description:**
Implement `POST /api/v1/tasks/{id}/defer`. Admin-only. Writes `Task.status = deferred`, sets `deferred_from`, and fires `maybe_advance_to_ready` on the parent work-item.

**Rationale:**
AC-1, AC-6 (defer from any non-terminal; not from `done`/`deferred`).

**Acceptance Criteria:**
- [ ] 1 endpoint implemented.
- [ ] Rejects `409` if `task.status in {done, deferred}`.
- [ ] Sets `deferred_from` to the prior state; never cleared.
- [ ] Fires `maybe_advance_to_ready` transactionally.
- [ ] Idempotency + admin-role enforcement via T-114.
- [ ] Route tests for each non-terminal source state + both terminal rejections.
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — 1 new route.
- `src/app/modules/ai/service.py` — 1 new service function.
- `tests/modules/ai/test_router_tasks_defer.py` — new test file.

**Technical Notes:**
`deferred_from` is for audit only — the derivation (`maybe_advance_to_ready`) treats `deferred` and `done` identically.

---

## GitHub integration

### T-120: GitHub PR webhook ingress — `/hooks/github/pr`

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-111, T-118

**Description:**
New endpoint `POST /hooks/github/pr`. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`. Persists a `WebhookEvent` with `source='github'` regardless of signature outcome (per the "persist before anything" invariant). On `pull_request.opened|reopened` with a body/title containing `closes T-NNN` or `orchestrator: T-NNN`, resolves to a task and invokes the S11 transition (`implementing → impl_review`) via the service layer. On `pull_request.closed`, records audit only. Ignored event types (non-`pull_request`) return `202` without processing.

**Rationale:**
AC-1 (S11 human path), AC-7 (webhook → check creation within 5 s — this task handles the ingress; T-121 handles the check API call).

**Acceptance Criteria:**
- [ ] Signature verification uses `hmac.compare_digest`; missing/invalid persists the event with `signature_ok=false` and returns `401`.
- [ ] PR-to-task regex matches both `closes T-NNN` and `orchestrator: T-NNN` (case-insensitive). Unmatched PRs persist the event with `matched_task_id=null` and return `202`.
- [ ] Dedupe on `github:pr:<pr_number>:<delivery_id>` (from T-111's extended helper).
- [ ] On match + `opened|reopened`, invokes the S11 service; then (if GitHub client configured) creates an `orchestrator/impl-review` check in `pending` state on the PR.
- [ ] On match + `closed` with `merged=true` before review approval, logs "merged before approval" as a trace entry on the task (audit only).
- [ ] Route test with a recorded GitHub webhook fixture.
- [ ] `uv run pyright`, `ruff`, route tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — 1 new route.
- `src/app/modules/ai/webhooks/github.py` — new module for signature verification + payload parsing.
- `src/app/modules/ai/service.py` — `handle_pr_webhook(...)` service function.
- `tests/modules/ai/test_webhooks_github.py` — new test file with `tests/fixtures/github/pr_opened.json` fixture.
- `src/app/config.py` — `github_webhook_secret: SecretStr | None = None`.

**Technical Notes:**
The PR-to-task regex should be strict: `r"(?:closes|orchestrator:)\s+(T-\d+)"` — loose matching risks false positives. If multiple `T-NNN` references appear, use the first match and log a warning. Don't raise — the operator will see the mismatch at review time.

---

### T-121: GitHub Checks API client + merge gating

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-120

**Description:**
New `src/app/modules/ai/github/checks.py` with a thin `GitHubChecksClient` protocol + concrete `HttpxGitHubChecksClient` implementation. Supports `create_check(pr, name, status)`, `update_check(pr, name, conclusion)`. Wired into T-118's review endpoints. Config via `GITHUB_APP_ID` / `GITHUB_PRIVATE_KEY` (App path) or `GITHUB_PAT` (PAT path); if neither is set, the composition root returns a `NoopGitHubChecksClient` so tests and operators without GitHub config still work (AC-9).

**Rationale:**
AC-7 (PR check flips within 5 s), AC-9 (composition integrity without GitHub config), FEAT-006 scope: "first external-system capability."

**Acceptance Criteria:**
- [ ] `GitHubChecksClient` Protocol with `create_check`, `update_check`.
- [ ] `HttpxGitHubChecksClient` targets `POST /repos/{owner}/{repo}/check-runs` and `PATCH /repos/{owner}/{repo}/check-runs/{check_id}`. Authenticates via App JWT or PAT.
- [ ] `NoopGitHubChecksClient` logs a single warning on first call and is the default when config is missing.
- [ ] Composition-root factory `get_github_checks_client()` in `core/github.py` picks App > PAT > Noop.
- [ ] T-118's review endpoints injected with the client via `Depends(get_github_checks_client)`.
- [ ] Unit tests with `respx` mocking the two GitHub endpoints.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/core/github.py` — factory + config reading.
- `src/app/modules/ai/github/__init__.py` — new package.
- `src/app/modules/ai/github/checks.py` — protocol + implementations.
- `src/app/config.py` — extend with GitHub-related fields.
- `src/app/modules/ai/router.py` / `service.py` — wire the client into review endpoints.
- `tests/modules/ai/github/test_checks.py` — unit tests.

**Technical Notes:**
GitHub App auth requires JWT signing (RS256) — use `PyJWT[crypto]` which is already an indirect dep via `cryptography`. Do NOT hardcode the App installation id — derive it per-repo at first use and cache per-run. PAT auth is a fallback for solo-dev convenience; in production the App flow is strongly preferred.

---

## Testing

### T-122: Per-transition integration tests

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-115, T-116, T-117, T-118, T-119

**Description:**
Comprehensive integration tests under `tests/integration/test_lifecycle_transitions.py` that drive real Postgres and hit each transition in the W* and T* tables via the HTTP API. Covers: all 12 task transitions, all 6 work-item transitions (W1-W6 incl. derived), rejection loops ≥3 iterations, defer from every non-terminal state, idempotent replay of every signal.

**Rationale:**
AC-2 (every transition covered), AC-5 (rejection loops ≥3 iter), AC-6 (defer from any non-terminal), AC-8 (idempotency).

**Acceptance Criteria:**
- [ ] One test per explicit transition (W1, W3, W4, W6, T1-T3, T5-T12).
- [ ] One test per derived transition (W2, W5, T4) proving it fires exactly once even under duplicated parent signals.
- [ ] One test per rejection edge (T3, T8, T11) that loops 3+ times without state corruption; asserts `Approval` table has N rows after N rejections.
- [ ] Defer tests cover all 7 non-terminal states + both terminal rejections.
- [ ] Idempotency tests replay each of the 14 signals and assert no second state-write, no duplicate `Approval` or `TaskAssignment` row.
- [ ] Tests use real Postgres via the existing session-scoped fixture; no SQLite.
- [ ] `uv run pytest tests/integration/test_lifecycle_transitions.py` green in < 60 s.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_transitions.py` — new.
- `tests/integration/conftest.py` — extend with admin/dev actor fixtures if needed.

**Technical Notes:**
Parameterize heavily (`pytest.mark.parametrize`) to keep the file from exploding. Share fixture factories for "a work-item in state X with N tasks in state Y" — these will be reused by T-123 and T-125.

---

### T-123: Composition-integrity test (AD-3)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-122

**Description:**
Add a test in `tests/integration/test_composition_integrity_feat006.py` that runs the full 14-signal lifecycle end-to-end with `LLM_PROVIDER=stub` and `GITHUB_WEBHOOK_SECRET=None` (noop Checks client). Proves the orchestrator produces identical state sequences with or without GitHub and with or without a real LLM.

**Rationale:**
AC-9 (engine has zero logic — prove it by swapping adapters), AD-3.

**Acceptance Criteria:**
- [ ] Test configures `LLM_PROVIDER=stub`, `GITHUB_WEBHOOK_SECRET=None`.
- [ ] Test drives a work-item through open → 2 tasks approved → 1 assigned to dev + 1 to agent → plan+impl+review for each → done → ready → closed, exercising all 14 signal types.
- [ ] Asserts terminal state on every entity (work_item, tasks, assignments, approvals).
- [ ] Replaces the real engine client with a recording double; asserts the double received zero transition-logic calls (only state writes for engine-backed entities, which in v1 is none for FEAT-006).
- [ ] Runs in < 30 s.
- [ ] Green in CI.

**Files to Modify/Create:**
- `tests/integration/test_composition_integrity_feat006.py` — new.

**Technical Notes:**
Keep this test as a single long scenario — it's the regression guard for "did someone accidentally put logic in the engine or make GitHub mandatory." Don't parametrize it; failure needs to point at a specific step, not a fan-out case.

---

### T-124: GitHub integration test — mocked Checks API + recorded webhook

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-120, T-121

**Description:**
Integration test in `tests/integration/test_github_integration.py` using `respx` to mock the Checks API and `tests/fixtures/github/` for recorded webhook payloads. Drives a PR-opened webhook → check created → review approve → check resolved `success`; separate scenario for review reject → check resolved `failure`.

**Rationale:**
AC-7 (PR check flips within 5 s — test asserts the call happens), FEAT-006 scope.

**Acceptance Criteria:**
- [ ] Fixtures: `pr_opened.json`, `pr_closed_merged.json`, `pr_closed_unmerged.json`.
- [ ] Happy path: webhook posted → S11 transition → check created (POST to GitHub mocked) → `/review/approve` → check resolved `success` (PATCH mocked).
- [ ] Reject path: webhook posted → S11 → `/review/reject` → check resolved `failure`.
- [ ] Bad-signature webhook: persisted with `signature_ok=false`, returns `401`, no transition.
- [ ] Unmatched PR: persisted with `matched_task_id=null`, returns `202`, no transition.
- [ ] `respx` asserts the exact number of GitHub API calls (no extras).
- [ ] `uv run pytest tests/integration/test_github_integration.py` green.

**Files to Modify/Create:**
- `tests/fixtures/github/pr_opened.json`, `pr_closed_merged.json`, `pr_closed_unmerged.json` — recorded payloads.
- `tests/integration/test_github_integration.py` — new.

**Technical Notes:**
Record real GitHub webhook payloads via a throwaway repo+App, then sanitize any secrets before committing. The test must use the signing helper to produce a valid `X-Hub-Signature-256` for the fixture bodies; don't skip that verification.

---

### T-125: Full E2E lifecycle test — AC-10

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-122, T-123, T-124

**Description:**
End-to-end test in `tests/integration/test_feat006_e2e.py` that executes the AC-10 scenario: open → generate 2 tasks → approve one, reject and re-approve one → assign both (one dev, one agent) → plan + review each → implement + review each → done → auto-transition to `ready` → admin closes. All 14 signal types exercised; real Postgres; stub LLM; noop GitHub client.

**Rationale:**
AC-10 (full-lifecycle E2E — the feature's marquee test).

**Acceptance Criteria:**
- [ ] Single test function that exercises all 14 signals in a realistic sequence.
- [ ] Uses the HTTP API throughout (no direct DB writes outside setup).
- [ ] Asserts final state on work-item (`closed`), both tasks (`done`), expected `Approval` and `TaskAssignment` row counts.
- [ ] Asserts the trace stream contains one audit entry per signal (via `GET /api/v1/trace` or equivalent if added; otherwise assert on a DB-level audit query).
- [ ] Runs in < 45 s.
- [ ] Green in CI.

**Files to Modify/Create:**
- `tests/integration/test_feat006_e2e.py` — new.

**Technical Notes:**
This is the marquee demo of FEAT-006. If it passes, the feature's contract holds. If it regresses later, triage it first over any other FEAT-006 test failure.

---

## Closeout

### T-126: FEAT-005 regression test — AC-11

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-125

**Description:**
Verify the FEAT-005 lifecycle agent continues to work unchanged after FEAT-006 ships. Extend the existing FEAT-005 composition-integrity test (or add a sibling) that runs the lifecycle agent end-to-end on a small IMP work item with `LLM_PROVIDER=stub` and asserts the same terminal state the IMP-002 proof produced.

**Rationale:**
AC-11 (FEAT-005 coexistence). Non-negotiable — the prior feature's behavior is a hard guarantee.

**Acceptance Criteria:**
- [ ] Existing FEAT-005 test suite passes unchanged after FEAT-006 lands.
- [ ] One new test explicitly validates "both lifecycle paths run in the same process" — spins up the service, runs a FEAT-005 lifecycle-agent run and a FEAT-006 signal-driven work-item flow in sequence, both complete successfully.
- [ ] `uv run pytest tests/integration/` fully green.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_coexistence.py` — new.
- (no changes to FEAT-005 code or tests expected)

**Technical Notes:**
If this test needs any production-code changes to pass, something in FEAT-006 broke FEAT-005 — fix the regression rather than touching FEAT-005's tests.

---

### T-127: Docs sweep + changelogs — AC-12

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-125, T-126

**Description:**
Final documentation pass verifying AC-12. Confirm `docs/data-model.md` and `docs/api-spec.md` reflect every shipped entity and endpoint (already scaffolded pre-task-generation; this task reconciles any drift). Update `docs/ARCHITECTURE.md` with the new lifecycle module if its surface diverged from the original sketch. Update `CLAUDE.md`'s "Key Directories" with the new `modules/ai/lifecycle/` submodule + `modules/ai/github/` package. Update `FEAT-006` status to `Completed`. Update `docs/stakeholder-definition.md` Success Metric #1 wording if the multi-actor capability warrants flipping or expanding the metric.

**Rationale:**
AC-12 (docs updated) + CLAUDE.md documentation-maintenance discipline.

**Acceptance Criteria:**
- [ ] Every entity + endpoint shipped in T-107 through T-121 has a corresponding doc entry.
- [ ] Changelog entries on `data-model.md` and `api-spec.md` are accurate (may require amending the pre-existing entries if anything drifted).
- [ ] `ARCHITECTURE.md` has a section or subsection on the deterministic lifecycle flow (even if brief).
- [ ] `CLAUDE.md` lists the new submodules.
- [ ] `FEAT-006-deterministic-lifecycle-flow.md` Status field flipped to `Completed` with a completion date.
- [ ] `README.md` self-hosted section mentions FEAT-006 as an alternative to FEAT-005's lifecycle-agent flow.
- [ ] Post-generation checklist from `.ai-framework/prompts/feature-tasks.md` passes on re-read.

**Files to Modify/Create:**
- `docs/data-model.md` — drift reconciliation.
- `docs/api-spec.md` — drift reconciliation.
- `docs/ARCHITECTURE.md` — new section.
- `CLAUDE.md` — directory list + any new patterns.
- `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md` — Status flip.
- `docs/stakeholder-definition.md` — metric update if warranted.
- `README.md` — brief mention.

**Technical Notes:**
Be surgical. This task is about closing loops, not re-architecting. If something substantive needs to change, open a follow-up instead.

---

## Summary

### Totals by Type

| Type | Count |
|------|-------|
| Database | 5 (T-107, T-108, T-109, T-110, T-111) |
| Backend | 8 (T-112, T-113, T-114, T-115, T-116, T-117, T-118, T-119) |
| Backend (GitHub) | 2 (T-120, T-121) |
| Testing | 5 (T-122, T-123, T-124, T-125, T-126) |
| Documentation | 1 (T-127) |

### Complexity Distribution

| Complexity | Count |
|------------|-------|
| S | 5 (T-109, T-110, T-111, T-119, T-126) |
| M | 12 |
| L | 4 (T-113, T-118, T-122, ... + T-127 is S; see per-task) |
| XL | 0 |

### Critical Path

`T-107 → T-108 → T-113 → T-116 → T-117 → T-118 → T-120 → T-121 → T-124 → T-125 → T-127`

Depth 11, dominated by the task-state-machine → plan endpoints → review endpoints → GitHub → E2E chain. Parallel chains (T-109, T-110, T-111, T-112, T-114) can land earlier.

### Risks and Open Questions

- **Derivation race conditions.** W2/W5/T4 fire inside signal handlers. Under concurrent admin actions (two `/approve` calls for tasks in the same work item), the derivation must be serialized by the work-item's row lock. T-112 claims to use `SELECT ... FOR UPDATE`; verify during T-122 under actual concurrent load.
- **GitHub App vs PAT.** The feature brief allows both; operational reality may force picking one. Revisit in T-121 if App setup exceeds the task's complexity estimate.
- **`lifecycle_signals` growth.** Idempotency keys accumulate indefinitely. Not a v1 blocker; flag for a retention job in a future IMP.
- **Merge gating under force-merge.** AC-7 requires the check to flip, but an admin can force-merge without waiting. FEAT-006 logs this ("merged before approval") but cannot block it. Document as known v1 gap.
- **Dispatch task-generation placeholder (T-115).** Kept as a named stub; the follow-up FEAT that wires an agent into FEAT-006 will replace it. Make sure the seam is clean — a single service function call.
- **Solo-dev config flag.** `SOLO_DEV_MODE` defaults to `True` and collapses `impl_review` approver to admin. When a second dev joins, the flip is a one-line config change + a T-113 test re-run. Flag for release notes.
