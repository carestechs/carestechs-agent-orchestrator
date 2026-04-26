# CLAUDE.md

> This file provides guidance to Claude Code (or any AI assistant) when working with this codebase.

## Pre-Work Checklist

Before generating specs, tasks, mockups, or implementation plans, you MUST follow these steps:

1. **Identify the task type** using the routing table in the "AI-Assisted Development Framework" section below. **If working on a specific task (T-XXX), check its Workflow field** and follow the Workflow Enforcement rules before starting implementation.
2. **Read the required files** listed in the routing table for your task type — read them directly, do not ask the user to paste them.
3. **Read the prompt template** from `.ai-framework/prompts/` — this defines the required sections, structure, and quality criteria for the deliverable.
4. **Derive structure from the prompt template, NOT from existing output files.** Specs, tasks, and plans are *outputs* — they may reflect an older version of the framework. The prompt templates in `.ai-framework/prompts/` are the authoritative source for format and structure.

---

## Project Overview

`carestechs-agent-orchestrator` is a headless Python service that drives the ia-framework's feature lifecycle as an agent-driven loop on top of `carestechs-flow-engine`. It owns the loop end-to-end (policy decisions via LLM tool-calling, per-run memory, durable traces) and drives the engine over HTTP while receiving progress events back via webhooks. See `docs/ARCHITECTURE.md` for the six project-specific decisions (AD-1 through AD-6) that govern the shape of this codebase.

**Tech Stack:** Python 3.12+, FastAPI, Typer (CLI), SQLAlchemy 2.0 async + Alembic, PostgreSQL 16+ (post-JSONL migration), Pydantic v2, httpx, provider-agnostic LLM abstraction (Anthropic by default), Docker + Docker Compose.
**Repo Type:** Single app — FastAPI service + Typer CLI sharing one codebase (`src/app/`). No frontend, no separate worker in v1.
**Stack Profile:** `python-ai-agent-service-docker-compose` (shared architecture repo). Every ADR listed in that profile is in force for this codebase.

---

## Quick Reference

### Common Commands

```bash
# Dev dependencies
uv sync

# Start backing services (Postgres; Redis only if Celery is later enabled)
docker compose up -d

# Run migrations
uv run alembic upgrade head

# Start the service (webhooks + control plane)
uv run uvicorn app.main:app --reload
# or: uv run orchestrator serve --reload

# Invoke the CLI
uv run orchestrator run lifecycle-agent@0.3.0 --intake featureBriefPath=docs/work-items/FEAT-042.md --follow
uv run orchestrator doctor

# Tests (fast unit) / integration / all
uv run pytest tests/modules
uv run pytest tests/integration
uv run pytest

# Type check + lint
uv run pyright
uv run ruff check .
uv run ruff format .

# New migration
uv run alembic revision --autogenerate -m "describe the change"

# Containerized — standalone (self-contained postgres bundled in compose)
docker compose -f docker-compose.yml up -d --build

# Containerized — DevTools umbrella (joins shared infra network at the parent
# folder ~/Desktop/Repos/DevTools/; assumes `infra` network + `postgres`
# container are already up). See ../devtools-umbrella.md.
docker compose -f docker-compose.prod.yml up -d --build
```

### Key Directories

```
src/app/
├── main.py                     — FastAPI app, router registration, LLM provider wiring
├── cli.py                      — Typer CLI (thin adapter over modules/ai/service.py)
├── config.py                   — pydantic-settings (env + pyproject.toml)
├── core/
│   ├── database.py             — AsyncEngine, async_sessionmaker, Base
│   ├── dependencies.py         — Shared FastAPI deps (get_db_session, get_api_key, etc.)
│   ├── exceptions.py           — Global handlers, RFC 7807 Problem Details
│   └── llm.py                  — Provider-agnostic LLM client factory
├── contracts/
│   └── ai.py                   — IAIService protocol for cross-module callers (none in v1)
├── modules/ai/                 — The only feature module in v1
│   ├── router.py               — /api/v1/* + /hooks/engine/*
│   ├── service.py              — Agent runtime loop, trace emission, stop conditions
│   ├── models.py               — Run, Step, PolicyCall, WebhookEvent, RunMemory, RunSignal, WorkItem, Task, TaskAssignment, Approval, LifecycleSignal, EngineWorkflow (SQLAlchemy)
│   ├── schemas.py              — Pydantic DTOs mirroring data-model.md
│   ├── dependencies.py         — AI-specific FastAPI deps (policy factory, engine client, lifecycle engine client, actor-role guard)
│   ├── tools/
│   │   ├── __init__.py         — build_tools + TERMINATE_TOOL_NAME
│   │   └── lifecycle/          — FEAT-005 lifecycle agent tools + local registry
│   ├── lifecycle/              — FEAT-006 deterministic-flow submodule
│   │   ├── declarations.py     — work_item_workflow + task_workflow state/transition constants
│   │   ├── engine_client.py    — FlowEngineLifecycleClient (JWT, retries, correlation-id encoding)
│   │   ├── bootstrap.py        — ensure_workflows on startup (engine registration, tenant-scoped cache, stale-cache 404 recovery)
│   │   ├── work_items.py       — work-item transitions (W1-W6) with optional engine mirror
│   │   ├── tasks.py            — task transitions (T1-T12) with optional engine mirror + approval matrix
│   │   ├── approval_matrix.py  — pure function: who may approve at each stage
│   │   ├── service.py          — signal-endpoint adapters (idempotency + transaction boundary)
│   │   ├── idempotency.py      — lifecycle_signals helper
│   │   └── reactor.py          — engine webhook dispatcher (W2/W5 derivations)
│   └── webhooks/
│       └── github.py           — GitHub PR webhook signature + parsing (FEAT-006)
└── migrations/                 — Alembic

agents/                         — YAML agent definitions (e.g. lifecycle-agent@0.1.0.yaml)
docs/                           — Framework docs (stakeholder, architecture, data-model, api-spec, ui-specification, personas, work-items)
tests/                          — conftest + modules/ai + integration/ + contract/
```

---

## Code Style & Conventions

- **Strict typing.** `pyright` in strict mode; no `# type: ignore` without an explanatory comment; prefer `Protocol` over ABCs for contracts.
- **Pydantic at boundaries.** Every HTTP request/response body, webhook payload, and CLI JSON output validates through a Pydantic model. NEVER return `dict` / `Any` from a FastAPI route or CLI command.
- **Async all the way.** All I/O is `async def`. DB sessions are `AsyncSession`. Never mix sync provider SDKs into async paths without an explicit adapter.
- **Service layer owns logic.** Route handlers and CLI commands are thin adapters — they parse inputs, call a service function, and format output. Business logic lives in `modules/<mod>/service.py` and is reachable from both entry points identically.
- **LLM through the abstraction.** Service code imports `core.llm` interfaces only. Provider SDKs (Anthropic/OpenAI/etc.) appear only in adapters or the composition root.
- **Policy via tool calling.** Every "decide next step" call uses the provider's native tool-calling API with one tool per candidate node. Never free-form JSON parsing. See `adrs/ai/policy-via-tool-calling.md` in the shared arch repo. *(Being superseded for runtime-loop node selection by FEAT-009 — see `docs/design/feat-009-pure-orchestrator.md`. The pattern survives **inside individual executors** that need to generate content; only the loop's node-selection use is removed. Full sweep lands with T-229.)*
- **Append-only traces.** `Step`, `PolicyCall`, and `WebhookEvent` records are append-only once terminal fields are set. No post-hoc edits.

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Files / modules | snake_case | `webhook_receiver.py`, `run_service.py` |
| Classes | PascalCase | `Run`, `PolicyCall`, `AgentRuntime` |
| Functions / methods | snake_case | `start_run`, `advance_loop` |
| Constants | UPPER_SNAKE | `MAX_STEPS_PER_RUN` |
| Pydantic model attributes | snake_case (Python) with camelCase JSON aliases | `started_at` ↔ `"startedAt"` |
| Database tables | snake_case, plural | `runs`, `policy_calls`, `webhook_events` |
| Database columns | snake_case | `engine_run_id`, `created_at` |
| Enum values (DB) | snake_case strings | `'in_progress'`, `'budget_exceeded'` |
| Env vars | UPPER_SNAKE with project prefix | `ORCHESTRATOR_API_KEY`, `ENGINE_WEBHOOK_SECRET` |
| Alembic revisions | Descriptive slug, not autoincrement only | `2026_04_15_add_run_memory.py` |

---

## Patterns & Anti-Patterns

### Patterns to Follow

- **Two entry points, one core.** Any capability exists once — as a service function — and is exposed by both FastAPI and Typer. New behavior starts in `service.py`, not in the adapter.
- **Tool definition doubles as policy action space.** Exposing a capability to the policy means adding a tool in `modules/ai/tools/`. Omit it from the per-call tool list to gate availability — don't use prompt text to hide it.
- **Pre-persist webhook events.** Inbound engine events MUST be persisted (including ones with bad signatures, with `signature_ok=false`) before any runtime action is taken. Idempotency via `dedupe_key` unique constraint.
- **RFC 7807 errors.** All 4xx/5xx responses use Problem Details. Helpers in `core/exceptions.py`.
- **Response envelope `{ data, meta? }`.** All 2xx JSON responses. Collection responses always include `meta`; single-resource responses omit it.
- **202-Accepted for runs.** Starting a run returns immediately with the run id. Completion is observed via polling or the trace stream, never via a blocking POST.
- **HMAC verification before parsing.** `/hooks/engine/*` verifies the signature on the raw body first, persists the event regardless, then validates the payload shape.
- **Trace writes go through a protocol.** `modules/ai/service.py` emits traces via an interface. JSONL writer (v1) and SQLAlchemy writer (v2) are interchangeable implementations per AD-5.
- **Each runtime-loop iteration opens its own `AsyncSession`.** The loop runs inside a supervised task, not inside a request handler — never share the request's `AsyncSession` with the loop, and never reuse one session across iterations. Use `session_factory` injected via `get_session_factory`.
- **Webhook pipeline is ordered: persist → reconcile → wake.** Events are written first (even on bad signature, with `signature_ok=false`), then the step state machine advances, then the owning run-loop is woken. Never wake before persisting.
- **Local tools bypass the engine.** Lifecycle-agent tools in `modules/ai/tools/lifecycle/` are registered in `tools/lifecycle/registry.py` and executed in-process by the runtime (no HTTP to the flow engine). The policy still selects them via native tool calling; the runtime detects the selection, runs the handler against `LifecycleMemory`, and persists the step as `completed` with `engine_run_id=NULL`. Adding a new lifecycle tool = new module in `tools/lifecycle/` + register it in `registry.py`. Don't reinvent this path for non-lifecycle agents until a second consumer demands it.
- **FEAT-006 + FEAT-008 lifecycle state lives in the engine.** Signal handlers in `modules/ai/lifecycle/service.py` forward every transition to the flow engine via `modules/ai/lifecycle/engine_client.py`, enqueue a `PendingAuxWrite` outbox row keyed on a fresh correlation id, commit, and return 202. The engine echoes the correlation id back in the `item.transitioned` webhook; `lifecycle/reactor.py` runs an ordered pipeline (materialize aux row from outbox → write `tasks.status` / `work_items.status` cache → consume correlation context → dispatch effectors via `EffectorRegistry.fire_all` → fire W2/W5 derivations). The engine is the authoritative state owner; status columns are reactor-managed caches; aux rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) land via the reactor, not inline. The `locked_from` / `deferred_from` columns were dropped — engine transition history is authoritative. Rejection transitions (T3/T8/T11) still don't call the engine; the `Approval` row alone records the event. Engine-absent fallback (no `lifecycle_engine_client` configured) preserves the pre-FEAT-008 inline path for dev mode but is not the target shape. See `docs/design/feat-008-engine-as-authority.md` for the rationale.
- **Effector registry is the outbound surface.** Every declared transition either fires a registered effector or carries an explicit `no_effector("reason")` exemption — `validate_effector_coverage()` runs at lifespan startup and refuses to boot otherwise. New external integrations (Slack notifications, audit hooks, additional check providers) land as effectors registered in `modules/ai/lifecycle/effectors/bootstrap.py` against the relevant transition key — not as inline calls in signal handlers. Every fire emits a `trace_kind="effector_call"` entry under `<trace_dir>/effectors/<entity_id>.jsonl`.
- **Per-request effector dispatch is the narrow exception.** When an effector needs DI-bound state that the lifespan-built registry can't supply (e.g. `GitHubChecksClient` from a per-request DI), the signal adapter calls `dispatch_effector(effector, ctx, trace)` directly and the registry slot for that transition carries a `no_effector` exemption pointing at the dispatch site (see the GitHub Checks transitions in `bootstrap.py`). Default to permanent registration; reach for per-request only when DI demands it.
- **Outbox + reconciliation backstop.** `pending_aux_writes` captures aux-write intent in the same transaction that commits the engine mirror. If the webhook is lost, the row stays orphaned. `uv run orchestrator reconcile-aux [--since=24h] [--dry-run]` queries the engine for the matching item, verifies the expected target state, and materializes the missed row. Idempotent — safe to re-run. Treat a growing orphan count as a health signal, not as a bug.
- **Pause-for-signal contract.** A local tool that returns `(LifecycleMemory, PauseForSignal)` tells the runtime to suspend the step as `in_progress` and `await supervisor.await_signal(...)`. `POST /api/v1/runs/{id}/signals` with `name=implementation-complete` persists a `RunSignal` row and calls `supervisor.deliver_signal(...)` — persist-first, then wake. Idempotent on `(run_id, name, task_id)`; duplicate calls return `202` with `meta.alreadyReceived=true`.
- **Correction-attempt bound.** `LIFECYCLE_MAX_CORRECTIONS` (default `2`) caps the number of `corrections` entries per task; exceeding it terminates the run with `stop_reason=error` and `final_state.reason=correction_budget_exceeded`. The bound lives in `stop_conditions.correction_budget_exceeded` and fires inside the existing `error` priority bucket — `cancelled > error > budget_exceeded > policy_terminated > done_node`.

### Anti-Patterns to Avoid

- **Don't block the run start.** `POST /api/v1/runs` and `orchestrator run` (without `--wait`/`--follow`) MUST return immediately. Synchronous loops here violate AD-2.
- **Don't call the flow engine from the policy.** Only the runtime loop drives the engine. The policy returns decisions; it does not have side effects.
- **Don't parse free-form JSON for policy decisions.** Always use native tool calling.
- **Don't put logic in tool handlers.** Tools are thin adapters that delegate to service functions. No business logic inside `modules/ai/tools/<tool>.py`.
- **Don't share memory across runs.** Per-run scope only in v1 (AD-4). Any caching that survives a run id MUST flag itself as a scope violation to revisit.
- **Don't add fields without updating `docs/data-model.md`.** Schema changes are doc-first; the data model is the contract, migrations follow.
- **Don't hardcode model names or endpoints.** All provider details via config. Swapping providers is a composition-root change.
- **Don't bypass the HTTP boundary from the CLI.** The CLI is a client of the service, not a back door to the database. Shared *code* is fine (via the service layer); direct DB access from the CLI is not.
- **Don't introduce a frontend, pgvector, or Celery in v1.** All three are out of scope until a feature demands them and the docs are updated.
- **Don't modify `carestechs-flow-engine` to simplify agent behavior.** Composition over extension — the orchestrator adapts to the engine, not the other way around.
- **Don't run multiple uvicorn workers in v1.** `RunSupervisor` is process-local; `--workers > 1` gives each worker its own supervisor and duplicates spawns. Single-worker only until a cross-worker coordinator lands.
- **Don't write `tasks.status` or `work_items.status` from a signal adapter under engine-present mode.** The reactor is the only writer. A stale-read window between signal-202 and webhook arrival is by design; don't paper over it with inline writes.
- **Don't write aux rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) from a signal adapter under engine-present mode.** Adapters enqueue a `PendingAuxWrite` and let the reactor materialize the row when the engine confirms the transition. Inline aux writes break correlation matching and re-create the rc2 anti-pattern that FEAT-008 inverted.
- **Don't add inline outbound calls for new external integrations.** The effector registry is the seam — register a new effector (or, with explicit justification + exemption, a per-request `dispatch_effector` site). A "quick" inline call regrows the FEAT-008 drift and forces the next pivot.

---

## Runtime Loop

FEAT-002's runtime loop has a few non-obvious invariants worth calling out:

- **`StopReason` → `RunStatus` mapping** (set by `_terminate`):
    - `done_node`, `policy_terminated` → `completed`.
    - `budget_exceeded`, `error` → `failed`.
    - `cancelled` → `cancelled`.
- **Priority order in `stop_conditions.evaluate`** (first match wins): `cancelled > error > budget_exceeded > policy_terminated > done_node`. User intent never masks a concurrent failure; a failure never masks a budget trip; etc.
- **Reserved tool name `terminate`.** The tool builder appends it to every tool list; selecting it stops the run with `policy_terminated`. Node names MUST NOT collide — the agent loader rejects them.
- **Step status is monotonic.** `reconciliation.next_step_state` rejects any event that would roll the step backward. A late `node_started` after `node_finished` is a no-op; the event is still persisted for forensics.

### LLM Providers

- **`stub`** (default, CI). `StubLLMProvider` replays a scripted sequence of tool calls deterministically; no network.
- **`anthropic`** (opt-in). `AnthropicLLMProvider` calls the Anthropic Messages API via the native tool-calling path. Requires `ANTHROPIC_API_KEY`. Model defaults to `claude-opus-4-7`; override with `LLM_MODEL`. Per-call token ceiling via `ANTHROPIC_MAX_TOKENS`, timeout via `ANTHROPIC_TIMEOUT_SECONDS`. Bounded retry (3 attempts, 500 ms → 4 s backoff + jitter) fires only on transient failures (5xx / 429 / connection / timeout); 400/401/403 do not retry.

Provider selection is a composition-root swap in `app.core.llm.get_llm_provider` — no other module touches any SDK. Adding a third provider follows the same shape: new module under `src/app/core/llm_<name>.py`, new branch in the factory, extend the `anthropic` import-quarantine test in `tests/test_adapters_are_thin.py` with an equivalent allow-list for the new SDK.

### Observability

- **Live trace streaming.** `GET /api/v1/runs/{id}/trace` returns the run's JSONL trace as `application/x-ndjson`. `?follow=true` keeps the stream open and closes cleanly once the run reaches a terminal state. Filter with `?since=<ISO-8601>` and repeatable `?kind=step|policy_call|webhook_event`. The CLI equivalent is `orchestrator runs trace <id> [--follow] [--since ...] [--kind ...]`.
- **Separation of reader from writer.** `JsonlTraceStore.tail_run_stream` opens a read-only file handle and polls for new lines every ~200 ms; it never contends on the writer's per-run `asyncio.Lock`. Two concurrent tails against the same run see the same lines in the same order.

---

## Error Handling

- **Typed exception hierarchy in `core/exceptions.py`.** Base `AppError` with subclasses: `ValidationError`, `NotFoundError`, `ConflictError`, `PolicyError`, `EngineError`, `ProviderError`, `AuthError`. Each carries a stable `code` (kebab-case) and maps to an RFC 7807 `type` URI plus an HTTP status.
- **Global handler converts `AppError` → Problem Details.** Routes raise, the handler serializes. NEVER return RFC 7807 bodies by hand from a route.
- **Structured logging.** Use the standard `logging` module with JSON formatter. Every log line includes `run_id` and `step_id` when available (pulled from contextvars). NEVER use `print`.
- **Never swallow silently.** If an exception is caught and not re-raised, the handler MUST either log it at `WARNING`+ with context or record it in the trace as a dedicated entry. Catching-and-pass is a review blocker.
- **Policy failures are run-terminating by default.** If the model returns zero or multiple tool calls, or a tool not in the allowed set, the runtime records a `PolicyError`-kind trace entry and transitions the run to `failed` (stop reason `error`) unless the agent definition specifies a repair policy. NEVER fabricate a "best guess" decision.
- **Engine errors are typed at the boundary.** `httpx` exceptions in the flow-engine client MUST be wrapped in `EngineError` with `http_status`, `engine_correlation_id`, and original body attached. NEVER let raw `httpx.HTTPError` leak into service code.
- **Webhook signature failures return 401 and persist the event** with `signature_ok=false`. NEVER drop an unsigned event silently.

---

## Testing Conventions

- **Test location:** Top-level `tests/` mirroring the `src/app/` tree (`tests/modules/ai/...`, `tests/integration/`).
- **Naming:** `test_<subject>.py`; test functions `test_<behavior>`.
- **Framework:** `pytest` + `pytest-asyncio` (strict mode) + `httpx.AsyncClient` for FastAPI; `respx` for outbound HTTP mocking; `freezegun` for time.
- **Real Postgres, not SQLite.** Integration tests spin up the same Postgres image as dev (via docker-compose or a session-scoped fixture); they MUST run real migrations. Mocking the database is a review blocker for anything involving SQL.
- **Stub the LLM at the abstraction seam.** Tests use a `StubPolicy` that picks nodes deterministically (first available, or scripted sequence). NEVER call a real provider from unit or CI tests. A small, explicitly-marked `tests/contract/` suite may hit the real provider under a `@pytest.mark.live` guard that is off by default.
- **Stub the flow engine with `respx`.** Engine HTTP calls and incoming webhooks are simulated in tests. The webhook signing helper MUST be used to produce valid HMAC test payloads.
- **Composition-integrity test (AD-3).** There MUST be at least one test that runs a real agent with a `StubPolicy` and asserts the run completes successfully — proving "remove the LLM → deterministic pipeline still runs."
- **Priority order:** 1) service-layer unit tests with stubs (fast, deterministic); 2) route integration tests (FastAPI + DB); 3) CLI smoke tests via `typer.testing.CliRunner`; 4) cross-component tests for runtime loop + webhook dispatch.

---

## Git Conventions

- **Branch naming:** `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`, `refactor/<slug>`.
- **Commit style:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`). Imperative mood; subject line ≤ 72 chars.
- **PR requirements:** CI green (typecheck + lint + tests). Every PR that changes an entity, endpoint, screen (N/A in v1), or pattern MUST update the corresponding doc in the same PR — see the Documentation Maintenance Discipline table. Changelog entries required on updates to `data-model.md`, `api-spec.md`, `ARCHITECTURE.md`, `ui-specification.md`.
- **Self-delivery discipline (AD-6).** Prefer opening a work item in `docs/work-items/` for any non-trivial change and running it through the orchestrator once the lifecycle agent is available. Manual PRs are fine before that, but each one is a data point we failed to self-host.

---

## AI-Assisted Development Framework

This project includes a bundled AI framework (`.ai-framework/`) with prompt templates, context assembly guides, and documentation maintenance rules.

**If you are an AI agent (e.g., Claude Code):** Read the files listed in the routing table below directly — do not ask the user to paste them. Read the prompt template for your task type to determine the output format. For manual/chat workflows, see `.ai-framework/guides/context-compilation.md` for XML assembly instructions.

### Task Generation Routing

When asked to generate tasks, identify the task type, read the required files, then read the prompt template for output format.

| Task Type | Prompt Template | Files to Read |
|-----------|----------------|---------------|
| New feature | `.ai-framework/prompts/feature-tasks.md` | `docs/work-items/FEAT-*.md` (target feature), `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/data-model.md`, `docs/api-spec.md`, `docs/ui-specification.md` |
| Bug fix | `.ai-framework/prompts/bugfix-tasks.md` | `docs/work-items/BUG-*.md` (target bug), `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| Refactoring | `.ai-framework/prompts/refactor-tasks.md` | `docs/work-items/IMP-*.md` (target improvement), `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| Spec generation | `.ai-framework/prompts/spec-generation.md` | `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| UI spec generation | `.ai-framework/prompts/ui-spec-generation.md` | `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/api-spec.md` |
| UI mockup | `.ai-framework/prompts/mockup-generation.md` | `docs/ui-specification.md` (target screen + Design System), `CLAUDE.md` |
| ADR compilation | `.ai-framework/prompts/compile-adrs.md` | ADR files (from shared ADR repo), `.ai-framework/templates/` |
| DDR compilation | `.ai-framework/prompts/compile-ddrs.md` | DDR files (from shared DDR repo), `.ai-framework/templates/` |
| Release transition | `.ai-framework/guides/release-lifecycle.md` | `docs/stakeholder-definition.md`, `CLAUDE.md` |
| Task implementation plan | `.ai-framework/prompts/plan-generation.md` | `CLAUDE.md`, task definition, files listed in task's "Files to Modify/Create" |

**Optional context** (read only when relevant to the specific task):

| Task Type | Optional Files | When to Include |
|-----------|---------------|-----------------|
| New feature | `docs/ARCHITECTURE.md`, `docs/personas/primary-user.md` | Multi-component features, user-facing features |
| Bug fix | `docs/data-model.md`, `docs/api-spec.md`, `docs/ui-specification.md` | Data/API/UI bugs respectively |
| Refactoring | `docs/data-model.md`, `docs/stakeholder-definition.md` | Data refactors, scope questions |
| Spec generation | `docs/personas/primary-user.md` | User-facing entity/endpoint decisions |
| UI mockup | `docs/api-spec.md`, `docs/personas/primary-user.md` | Data-driven screens, content tone |
| Prioritization | `docs/work-items/FEAT-*.md`, `docs/work-items/BUG-*.md`, `docs/work-items/IMP-*.md`, `docs/stakeholder-definition.md`, `docs/personas/` | Comparing and prioritizing work items |

**Work Items** (`docs/work-items/`): Feature Briefs, Bug Reports, and Improvement Proposals are the preferred input for task generation. If no work item document exists for a task, the prompts support inline fallbacks — but structured work items produce higher-quality task breakdowns.

### Workflow Enforcement

Each task definition includes a **Workflow** field. Before starting any task, check its Workflow value and follow the required steps:

| Workflow | Required Steps Before Implementation |
|----------|--------------------------------------|
| `standard` | 1. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 2. Implement following the plan. |
| `mockup-first` | 1. Generate an HTML mockup using `.ai-framework/prompts/mockup-generation.md`. Get stakeholder approval. See `.ai-framework/guides/getting-started.md` Step 7.5. 2. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 3. Implement following the plan. |
| `investigation-first` | 1. Complete all investigation steps in the task. Document findings (root cause, affected areas). 2. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 3. Implement following the plan. |

**If a task has no Workflow field** (legacy tasks), classify it yourself:
- Type is Frontend + adds/changes a screen → treat as `mockup-first`
- Task requires root cause analysis → treat as `investigation-first`
- Otherwise → treat as `standard`

### Development Pipeline

When implementing tasks from a generated task list, follow this sequence for **each task**:

1. **Pick a task** from the task list (respect dependency order).
2. **Check its Workflow field** and complete any prerequisites (see Workflow Enforcement above).
3. **Generate an implementation plan** using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`.
4. **Implement** following the steps in the plan.
5. **Verify** the acceptance criteria from the task definition are met.

This sequence applies to every task. The plan file is a developer-facing artifact — it bridges "what to do" (task definition) and "how to do it" (exact code changes).

### Context Assembly Rules

Read files in **Cone of Context** order — broad (strategic) to narrow (tactical):

| Layer | Files | Purpose |
|-------|-------|---------|
| Strategic | `docs/stakeholder-definition.md`, `docs/personas/primary-user.md` | Why? For whom? What's in scope? |
| Architectural | `docs/ARCHITECTURE.md` | What is the system? How is it structured? |
| Specification | `docs/data-model.md`, `docs/api-spec.md` | What are the entities and API contracts? |
| UI | `docs/ui-specification.md` | What do screens look like? What are the components? |
| Work Items | `docs/work-items/FEAT-*.md`, `docs/work-items/BUG-*.md`, `docs/work-items/IMP-*.md` | What specific work to do? Features, bugs, improvements |
| Implementation | `CLAUDE.md` | How do we build things? What are the conventions? |

**For large documents:** Read only the sections relevant to the task (e.g., for a task about labels, read only the Label entity from `data-model.md` and label endpoints from `api-spec.md`). Quality over quantity.

For the full context selection matrix and XML assembly examples, see `.ai-framework/guides/context-compilation.md`.

### Documentation Maintenance Discipline

When code changes happen, check which docs need updating per `.ai-framework/guides/maintenance.md`. Include doc updates in the same PR as the code change.

| Code Change | Document to Update |
|-------------|-------------------|
| New entity or field | `docs/data-model.md` |
| New/changed endpoint or DTO | `docs/api-spec.md` |
| New/changed screen or component | `docs/ui-specification.md` |
| New component or service | `docs/ARCHITECTURE.md` |
| New pattern or convention | `CLAUDE.md` |
| Scope or strategy change | `docs/stakeholder-definition.md` |
| Design token or screen layout change | `mockups/` (affected screens) |
| DDR updated in shared repo | Re-run DDR compilation, update Component Examples + CLAUDE.md Design Patterns |
| Feature tasks completed | `docs/work-items/FEAT-*.md` — update Status to "Completed" |
| Bug resolved | `docs/work-items/BUG-*.md` — update Status to "Resolved" |
| Improvement completed | `docs/work-items/IMP-*.md` — update Status to "Completed" |

**Changelog rule:** Every update to `data-model.md`, `api-spec.md`, `ARCHITECTURE.md`, or `ui-specification.md` must include a changelog entry at the bottom of the document. See `.ai-framework/guides/maintenance.md` for format.

### Framework Reference

For deeper reading on the full workflow and rules:

- `.ai-framework/guides/getting-started.md` — full workflow from docs to task generation
- `.ai-framework/guides/context-compilation.md` — context assembly details and task-type matrix
- `.ai-framework/guides/maintenance.md` — doc update triggers and review checklists
