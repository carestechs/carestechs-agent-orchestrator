# Task Breakdown: FEAT-001 — Project Skeleton per Stack Profile

> **Source:** `docs/work-items/FEAT-001-project-skeleton.md`
> **Generated:** 2026-04-15
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

Tasks are grouped in build order: Foundation → Data → Backend → CLI → Deployment → Testing → Polish. Every task's **Workflow** is `standard` — there are no user-facing screens in v1, so `mockup-first` never applies.

---

## Foundation

### T-001: Bootstrap `pyproject.toml` + tool configuration

**Type:** DevOps
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Create `pyproject.toml` with project metadata, pinned runtime and dev dependencies, the `orchestrator = "app.cli:main"` script entry, the `[tool.orchestrator]` config table, and tool config for `ruff`, `pyright` (strict), and `pytest` (asyncio strict + `live` marker). Use `uv` for dependency management per the stack profile.

**Rationale:**
Every subsequent task requires `uv sync` to succeed and consistent lint/type/test tooling. Addresses AC-4 (pyright + ruff clean) and underpins every other AC.

**Acceptance Criteria:**
- [ ] `uv sync` succeeds from a fresh clone.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` exit `0`.
- [ ] `uv run pyright` exits `0` with `strict = true` applied to `src/`.
- [ ] `uv run pytest --collect-only` returns `0` tests without error.
- [ ] `orchestrator --help` resolves to `app.cli:main` (stub is acceptable here; filled by T-019).

**Files to Modify/Create:**
- `pyproject.toml` — project metadata, deps, `[project.scripts]`, `[tool.ruff]`, `[tool.pyright]`, `[tool.pytest.ini_options]`, `[tool.orchestrator]`.
- `uv.lock` — generated.
- `.python-version` — `3.12`.
- `README.md` — reduce to a short pointer to `docs/` + "see CLAUDE.md for commands"; defer the full commands section to T-030.

**Technical Notes:**
Pin deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `httpx`, `typer`, `anthropic` (for later — import deferred per profile), `structlog` or stdlib `logging` JSON formatter. Dev: `pytest`, `pytest-asyncio`, `respx`, `freezegun`, `ruff`, `pyright`. Mark `anthropic` as optional if the stub provider is the only v1 wiring — acceptable either way.

---

### T-002: Create source layout and package scaffolding

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-001

**Description:**
Create the directory tree exactly as documented in `CLAUDE.md` → "Key Directories" with empty `__init__.py` files where needed and placeholder docstrings on each package describing its role. No logic yet.

**Rationale:**
Addresses AC-10 (every referenced directory exists; no files outside the documented tree). Locks the structure before services land.

**Acceptance Criteria:**
- [ ] Directory tree matches `CLAUDE.md` → Key Directories exactly.
- [ ] Every package has an `__init__.py` with a one-line docstring stating its role.
- [ ] `python -c "import app.main, app.cli, app.config, app.core.database, app.core.dependencies, app.core.exceptions, app.core.llm, app.modules.ai.router, app.modules.ai.service, app.modules.ai.models, app.modules.ai.schemas, app.modules.ai.dependencies"` imports cleanly (trivial stubs).

**Files to Modify/Create:**
- `src/app/__init__.py`, `src/app/main.py` (placeholder `app = FastAPI()`), `src/app/cli.py` (placeholder `def main(): ...`), `src/app/config.py`, `src/app/core/{__init__,database,dependencies,exceptions,llm}.py`, `src/app/contracts/{__init__,ai}.py`, `src/app/modules/__init__.py`, `src/app/modules/ai/{__init__,router,service,models,schemas,dependencies}.py`, `src/app/modules/ai/tools/__init__.py`, `src/app/migrations/__init__.py`.
- `tests/__init__.py`, `tests/conftest.py` (empty), `tests/modules/ai/__init__.py`, `tests/integration/__init__.py`, `tests/contract/__init__.py`.

**Technical Notes:**
Keep placeholders minimal — real content lands in later tasks. Intent of this task is to freeze the layout so later PRs don't drift.

---

### T-003: Config module (`app.config`)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-002

**Description:**
Implement `pydantic-settings` `Settings` class reading env vars and `[tool.orchestrator]` from `pyproject.toml` with the precedence documented in `ui-specification.md` → Configuration Sources. Validate at startup; expose a `get_settings()` function cached with `lru_cache`.

**Rationale:**
Required by every other component that needs config (DB URL, API key, webhook secret, engine base URL, LLM provider/model). Addresses the "validate at startup" profile requirement.

**Acceptance Criteria:**
- [ ] `Settings` fields: `database_url`, `orchestrator_api_key`, `engine_webhook_secret`, `engine_base_url`, `engine_api_key` (optional), `llm_provider` (default `"stub"`), `llm_model` (optional), `anthropic_api_key` (optional), `agents_dir` (default `"agents/"`), `log_level`.
- [ ] Precedence: CLI flags (passed at call-site) > env vars > `pyproject.toml [tool.orchestrator]` > defaults.
- [ ] Missing required fields cause `app.main` import to fail fast with a message naming the field.
- [ ] `get_settings()` is `lru_cache`-memoized and overridable in tests via a dependency-overrides helper.

**Files to Modify/Create:**
- `src/app/config.py` — `Settings` + `get_settings()`.
- `tests/test_config.py` — env-var override + pyproject layer + missing-field cases.

**Technical Notes:**
Use `pydantic_settings.BaseSettings` with `model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")`. The pyproject layer can be implemented as a custom settings source per `pydantic-settings` docs.

---

### T-004: Core database module

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-003

**Description:**
Create `core/database.py` with the async engine, `async_sessionmaker`, `Base = DeclarativeBase`, and an async `get_db_session()` FastAPI dependency that yields a session with correct commit/rollback/close semantics.

**Rationale:**
Required by data-layer tasks (T-009..T-011) and the health check (T-014). Centralizes session lifecycle per `adrs/python/sqlalchemy-async.md`.

**Acceptance Criteria:**
- [ ] `engine` uses `asyncpg` with `pool_pre_ping=True`.
- [ ] `get_db_session` yields an `AsyncSession`; commits on normal exit, rolls back on exception, always closes.
- [ ] `Base` is a `DeclarativeBase` subclass importable from `app.core.database`.
- [ ] Unit test with a real Postgres (via the fixture from T-024) opens a session, executes `SELECT 1`, closes cleanly.

**Files to Modify/Create:**
- `src/app/core/database.py`.
- `tests/core/test_database.py`.

---

### T-005: Exception hierarchy + RFC 7807 handler + response envelope

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-002

**Description:**
Implement `AppError` base class and subclasses (`ValidationError`, `NotFoundError`, `ConflictError`, `PolicyError`, `EngineError`, `ProviderError`, `AuthError`, `NotImplementedYet`) per `CLAUDE.md` → Error Handling. Implement a global FastAPI exception handler converting `AppError` and `RequestValidationError` to RFC 7807 Problem Details. Implement an `envelope(data, meta=None)` helper and a `ProblemDetails` Pydantic schema.

**Rationale:**
Addresses AC-6 (control-plane stubs return 501 as Problem Details) and the envelope convention from `api-spec.md`. All later routes depend on this.

**Acceptance Criteria:**
- [ ] Each `AppError` subclass has a stable `code` (kebab-case), `http_status`, and `problem_type` URI.
- [ ] Global handler emits bodies matching `api-spec.md` → "Error Response".
- [ ] `envelope()` returns `{"data": ..., "meta": ...}` (omits `meta` when `None`).
- [ ] `NotImplementedYet` maps to HTTP `501` with type URI `.../problems/not-implemented`.
- [ ] Pydantic `RequestValidationError` surfaces per-field `errors` dict per `api-spec.md` example.

**Files to Modify/Create:**
- `src/app/core/exceptions.py` — classes + handler.
- `src/app/core/envelope.py` — `envelope()` helper + `ResponseEnvelope` generic.
- `tests/core/test_exceptions.py` — per-subclass mapping + validation-error shape.

---

### T-006: Structured logging with `run_id` / `step_id` contextvars

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-003

**Description:**
Configure stdlib `logging` with a JSON formatter that injects `run_id` and `step_id` from `contextvars` when set, and omits them when `None`. Provide `bind_run_id(run_id)` / `bind_step_id(step_id)` context-manager helpers. Wire the formatter at app startup with level from `Settings.log_level`.

**Rationale:**
Required by CLAUDE.md Error Handling section; every later runtime log line needs correlation. Addresses the "structured logging, never use print" rule and the contextvars edge case in FEAT-001 §9.

**Acceptance Criteria:**
- [ ] Log lines are valid JSON with `timestamp`, `level`, `logger`, `message`, and `run_id` / `step_id` only when set.
- [ ] `bind_run_id` / `bind_step_id` are async-safe and resettable on context exit.
- [ ] Tests assert that a log line without a bound run id does NOT emit `"run_id": null`.

**Files to Modify/Create:**
- `src/app/core/logging.py` — formatter + contextvar accessors + `configure_logging()`.
- `src/app/main.py` — call `configure_logging()` at app factory time (scaffolded here; wired in T-012).
- `tests/core/test_logging.py`.

---

### T-007: LLM abstraction + stub provider

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-003

**Description:**
Define `LLMProvider` Protocol in `core/llm.py` with the minimum surface needed by future policy calls: `chat_with_tools(messages, tools) -> ToolCall` returning `(selected_tool_name, tool_arguments, usage_metadata)`. Implement `StubLLMProvider` that returns a deterministic scripted sequence of tool calls — no network, no SDK import. Expose a `get_llm_provider()` factory that dispatches on `Settings.llm_provider` (v1 only supports `"stub"`; wire an import-but-don't-initialize placeholder for `"anthropic"`).

**Rationale:**
Addresses the composition-integrity smoke test (AC-3 / AD-3) — `StubLLMProvider` is what lets a later runtime loop complete with no real LLM. Keeps provider SDKs out of service code per `adrs/ai/llm-abstraction-python.md`.

**Acceptance Criteria:**
- [ ] `LLMProvider` is a `Protocol` with `chat_with_tools` async method.
- [ ] `StubLLMProvider` is constructed from a scripted list of `(tool_name, args)` tuples; iterates through them on successive calls; raises `ProviderError` if the script is exhausted.
- [ ] `get_llm_provider()` with `llm_provider="stub"` returns a default stub that can be replaced via a FastAPI dependency override.
- [ ] No `anthropic` or `openai` import executes at module load time in v1.

**Files to Modify/Create:**
- `src/app/core/llm.py` — Protocol + stub + factory + `ToolCall` / `Usage` dataclasses.
- `tests/core/test_llm_stub.py`.

---

### T-008: HMAC webhook verifier + API-key Bearer dependency

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-003, T-005

**Description:**
Implement `verify_engine_signature(raw_body: bytes, header: str, secret: str) -> bool` using `hmac.compare_digest` over SHA-256. Implement a FastAPI dependency `require_engine_signature` that reads the raw body (without consuming it for downstream handlers), verifies the header, and attaches `signature_ok: bool` to `request.state`. Implement `require_api_key` dependency that validates `Authorization: Bearer <token>` against `Settings.orchestrator_api_key`, raising `AuthError`.

**Rationale:**
Addresses AC-6 (webhook endpoint behavior) and the control-plane auth pattern. Signing is load-bearing for the whole webhook trust boundary.

**Acceptance Criteria:**
- [ ] Signature header format `sha256=<hex>` is parsed and compared via constant-time compare.
- [ ] Missing header, malformed prefix, and wrong digest all result in `signature_ok=False`.
- [ ] `require_engine_signature` does NOT raise on bad signature — it sets state and lets the route decide. (The route will 401 and persist the event with `signature_ok=false` per data model.)
- [ ] `require_api_key` raises `AuthError` (→ 401 Problem Details) for missing/invalid tokens.
- [ ] Helper `sign_body(body, secret)` is exported for test use.

**Files to Modify/Create:**
- `src/app/core/webhook_auth.py`.
- `src/app/core/api_auth.py`.
- `tests/core/test_webhook_auth.py`, `tests/core/test_api_auth.py`.

**Technical Notes:**
Reading the raw body without consuming it in Starlette requires reading in a dependency, caching on `request.state.raw_body`, and having the route body-parser read from state rather than `await request.body()` a second time. Alternatively, use a `middleware` to stash the raw body per request — simpler and route-agnostic.

---

## Data Layer

### T-009: SQLAlchemy models for all five entities

**Type:** Database
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-004

**Description:**
Implement `Run`, `Step`, `PolicyCall`, `WebhookEvent`, `RunMemory` in `modules/ai/models.py` exactly matching the fields, types, enum check constraints, indexes, and uniqueness constraints documented in `docs/data-model.md`. Use UUIDv7 for PKs (via `uuid6` package or an inline generator) and `timestamptz` timestamps.

**Rationale:**
Addresses AC-5 (migration round-trips) and the entity half of AC-6. All subsequent runtime work reads/writes these tables.

**Acceptance Criteria:**
- [ ] Each model's fields, nullability, defaults, and types match `docs/data-model.md` exactly.
- [ ] Enum fields are stored as `text` with `CHECK` constraints listing the allowed values from `docs/data-model.md`.
- [ ] Indexes and uniqueness constraints match the data model (e.g., `UNIQUE(run_id, step_number)` on `steps`, `UNIQUE(dedupe_key)` on `webhook_events`, `UNIQUE(step_id)` on `policy_calls`).
- [ ] `Run.final_state`, `Step.node_inputs`, `PolicyCall.prompt_context`, `WebhookEvent.payload`, etc. are `JSONB` with non-null defaults where the data model specifies.
- [ ] Model classes carry a docstring noting append-only semantics where applicable.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py`.
- `tests/modules/ai/test_models.py` — one test asserting every documented field exists on the right class with the right type.

---

### T-010: Pydantic DTOs matching api-spec

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-002

**Description:**
Implement Pydantic v2 models in `modules/ai/schemas.py` for every DTO in `docs/api-spec.md` (`RunSummaryDto`, `StepDto`, `PolicyCallDto`, `WebhookEventDto`) plus request DTOs for `POST /api/v1/runs`, `POST /api/v1/runs/{id}/cancel`, and the webhook event body. All fields use Python snake_case with camelCase JSON aliases via `alias_generator`.

**Rationale:**
Every control-plane route's validation layer (even for 501 stubs) requires the correct request/response shape so OpenAPI is accurate. Addresses AC-6 and AC-2 (OpenAPI renders all documented endpoints).

**Acceptance Criteria:**
- [ ] Each DTO's fields match `docs/api-spec.md` → Shared DTOs exactly.
- [ ] Serialization round-trip: `dto.model_dump(by_alias=True)` produces camelCase JSON; `Dto.model_validate(json)` accepts camelCase input.
- [ ] Enums are shared with the SQLAlchemy models (single source of truth — preferred: a `common.py` with string-enum classes imported by both).
- [ ] Webhook event DTO rejects unknown `eventType` values with a 422-compatible validation error.

**Files to Modify/Create:**
- `src/app/modules/ai/schemas.py`.
- `src/app/modules/ai/enums.py` — shared string enums for `RunStatus`, `StepStatus`, `StopReason`, `WebhookEventType`.
- Update `src/app/modules/ai/models.py` to import enums from `enums.py`.
- `tests/modules/ai/test_schemas.py`.

---

### T-011: Alembic init + initial migration

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-009

**Description:**
Initialize Alembic with the async env template, point `target_metadata` at `Base.metadata` from `app.core.database`, and generate the initial migration creating all five tables with their indexes and check constraints.

**Rationale:**
Addresses AC-1 (`alembic upgrade head` succeeds), AC-5 (round-trip), and the AC-8 Docker image migration path.

**Acceptance Criteria:**
- [ ] `alembic.ini` at repo root; `src/app/migrations/env.py` uses async engine from settings.
- [ ] Initial revision named `2026_04_15_initial_schema.py`.
- [ ] `uv run alembic upgrade head` + `uv run alembic downgrade base` + `uv run alembic upgrade head` all succeed with no warnings.
- [ ] `autogenerate` against a clean database produces no diff after `upgrade head`.

**Files to Modify/Create:**
- `alembic.ini`.
- `src/app/migrations/env.py`, `src/app/migrations/script.py.mako`.
- `src/app/migrations/versions/2026_04_15_initial_schema.py`.

**Technical Notes:**
Common gotcha: async engines need `run_sync` inside `env.py`. Copy from the canonical SQLAlchemy async Alembic template.

---

## Backend

### T-012: FastAPI app factory + router registration + handler wiring

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-005, T-006, T-009, T-010

**Description:**
Implement `create_app()` in `app/main.py`: configure logging (T-006), register the `ai` module router (T-013–T-016) at `/api/v1` and `/hooks/engine`, register the `/health` route, install the global exception handler, and set up a raw-body-capture middleware for HMAC verification. Module-level `app = create_app()` for uvicorn.

**Rationale:**
Binds all the foundational pieces into a runnable service. Addresses AC-2 (service starts; `/docs` renders).

**Acceptance Criteria:**
- [ ] `uv run uvicorn app.main:app` starts without errors.
- [ ] `GET /docs` renders OpenAPI UI showing every endpoint from `api-spec.md`.
- [ ] `GET /openapi.json` paths exactly match `api-spec.md` → Endpoint Summary.
- [ ] Unhandled exceptions in any route return RFC 7807 JSON with HTTP 500.

**Files to Modify/Create:**
- `src/app/main.py`.
- `src/app/core/middleware.py` — raw-body capture.
- `tests/test_app_boot.py`.

---

### T-013: AI module service layer stubs

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-009, T-010

**Description:**
Create `modules/ai/service.py` with function signatures for every operation the routes and CLI will call: `start_run`, `list_runs`, `get_run`, `cancel_run`, `list_steps`, `list_policy_calls`, `stream_trace`, `list_agents`, plus webhook-side `ingest_engine_event`. Control-plane functions raise `NotImplementedYet`; `ingest_engine_event` is fully implemented (persist the `WebhookEvent`, enforce `dedupe_key` idempotency, return the persisted record — no downstream dispatch yet).

**Rationale:**
Addresses AC-9 (routes/CLI are thin; delegate to services even for stubs) and AC-6 (webhook endpoint fully works). Locks the service API the runtime-loop feature will later fill in.

**Acceptance Criteria:**
- [ ] Every control-plane service function has correct typed signature and raises `NotImplementedYet`.
- [ ] `ingest_engine_event` persists the event (including bad-signature events with `signature_ok=false`), is idempotent on `dedupe_key`, returns the persisted `WebhookEventDto`, and raises `NotFoundError` when `engine_run_id` is unknown.
- [ ] Unit tests cover: happy path, duplicate `dedupe_key` (idempotent 2nd insert), unknown `engine_run_id`, bad-signature persistence.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py`.
- `src/app/contracts/ai.py` — `IAIService` protocol mirroring the public service functions.
- `tests/modules/ai/test_service_ingest_event.py`.

---

### T-014: `/health` endpoint with dependency-check chain

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-004, T-007, T-012

**Description:**
Implement `GET /health` per `api-spec.md`. Checks: `database` (execute `SELECT 1`), `llm_provider` (stub always `ok`; real providers do a minimal call only if `LLM_LIVE_CHECK=1`), `flow_engine` (best-effort `GET {engine_base_url}/health`, `ok` if unconfigured).

**Rationale:**
Addresses AC-2. Also powers the `doctor` CLI in T-021.

**Acceptance Criteria:**
- [ ] Happy path returns `200` with `{data: {status: "ok", checks: {database, llm_provider, flow_engine}}}`.
- [ ] DB failure returns `200` with status `"degraded"` and `database: "down"` (service is still up; `/health` never itself 500s from a downstream failure).
- [ ] Unauthenticated — no bearer token required.
- [ ] Response time under 500 ms in the happy path.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` (add health router OR put health in a top-level `app/health.py` — choose one; prefer top-level for clarity).
- `src/app/health.py`.
- `tests/test_health.py`.

---

### T-015: Control-plane routes (stubs returning 501 via service)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-005, T-008, T-010, T-013

**Description:**
Implement the eight control-plane routes from `api-spec.md` under `/api/v1` (runs list/get/create/cancel, steps list, policy-calls list, trace stream, agents list). Each route: validates input via Pydantic, requires the API-key dependency, calls the corresponding service function, and lets the `NotImplementedYet` raised by the service bubble up to the global handler (producing 501 Problem Details). Response models are declared so OpenAPI is accurate even though stubs never return them.

**Rationale:**
Addresses AC-6 (all endpoints exist; return 501 Problem Details from stubs), AC-9 (thin adapters).

**Acceptance Criteria:**
- [ ] Each route has a Pydantic response model set via `response_model=`.
- [ ] Unauthenticated requests return `401` Problem Details (auth check happens before service call).
- [ ] Validation errors return `400` with per-field `errors` dict.
- [ ] Authenticated happy-path requests return `501` Problem Details with type URI `.../problems/not-implemented`.
- [ ] Query parameters for list endpoints (`page`, `pageSize`, `status`, `agentRef`) are parsed and validated even for the stub.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py`.
- `tests/modules/ai/test_routes_control_plane.py`.

---

### T-016: Webhook events endpoint (fully implemented)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-008, T-013

**Description:**
Implement `POST /hooks/engine/events` per `api-spec.md`: verify HMAC signature via the middleware-captured raw body, validate the payload, delegate to `service.ingest_engine_event`. 401 on bad signature (event still persisted with `signature_ok=false`); 404 on unknown `engineRunId`; 202 on success (including dedupe-key retries).

**Rationale:**
Addresses AC-6 (webhook fully functional) and the idempotency + bad-signature edge cases in FEAT-001 §9.

**Acceptance Criteria:**
- [ ] 202 on valid signed event with known `engineRunId`.
- [ ] 202 on duplicate `dedupe_key` (no second insert).
- [ ] 401 on missing or invalid signature; event persisted with `signature_ok=false`.
- [ ] 404 on unknown `engineRunId`; event persisted.
- [ ] 422 on valid signature but malformed payload body; event persisted.
- [ ] Integration test covers all five cases using the `sign_body` helper from T-008.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — webhook subrouter.
- `tests/modules/ai/test_routes_webhook.py`.

---

### T-017: Flow-engine HTTP client skeleton

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-003, T-005

**Description:**
Create `modules/ai/engine_client.py` defining `FlowEngineClient` — a typed `httpx.AsyncClient` wrapper with base URL and auth header from `Settings`. Public surface: `health()` and a stub `dispatch_node(...)` that raises `NotImplementedYet`. `httpx` exceptions are wrapped in `EngineError` with correlation metadata.

**Rationale:**
The health check (T-014) uses `health()`. Real `dispatch_node` ships with the runtime-loop feature. Addresses the engine-error-boundary rule in CLAUDE.md.

**Acceptance Criteria:**
- [ ] `FlowEngineClient` is injectable via FastAPI dependency; tests override it with a `respx` mock.
- [ ] `health()` returns `True` on 2xx, `False` on any other response (incl. connection errors), NEVER raises.
- [ ] `dispatch_node` raises `NotImplementedYet`.
- [ ] `httpx.HTTPStatusError` / `httpx.RequestError` in any method are wrapped in `EngineError` with `http_status`, `engine_correlation_id` (if present in response), and original body.

**Files to Modify/Create:**
- `src/app/modules/ai/engine_client.py`.
- `tests/modules/ai/test_engine_client.py`.

---

### T-018: Trace store protocol + no-op implementation

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-010

**Description:**
Define `TraceStore` Protocol in `modules/ai/trace.py` with `record_step`, `record_policy_call`, `record_webhook_event`, `open_run_stream` methods. Ship a `NoopTraceStore` as the default. Wire it as a FastAPI dependency. JSONL and Postgres implementations are deferred.

**Rationale:**
Locks the AD-5 abstraction boundary on day one so the runtime feature can swap in JSONL then Postgres without service-code changes. Addresses AC-9's spirit: behavior is behind a seam.

**Acceptance Criteria:**
- [ ] `TraceStore` Protocol with typed methods.
- [ ] `NoopTraceStore` silently accepts calls; `open_run_stream` returns an empty async iterator.
- [ ] Factory `get_trace_store()` returns the no-op by default; override path works in tests.

**Files to Modify/Create:**
- `src/app/modules/ai/trace.py`.
- `tests/modules/ai/test_trace_noop.py`.

---

## CLI

### T-019: Typer CLI entry + global options + stub commands

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-003, T-005

**Description:**
Implement `app/cli.py` using Typer: `main` as the Typer app; all global options (`--api-base`, `--api-key`, `--json`, `--quiet`, `--verbose`, `--help`) wired; subcommands `run`, `runs ls/show/cancel/trace/steps/policy`, `agents ls/show` registered but bodies print a "not implemented (FEAT-001 skeleton)" message and exit `2`. `serve` and `doctor` subcommands are declared but implementations land in T-020/T-021.

**Rationale:**
Addresses AC-7 (every CLI command invocable; `--help` renders the documented synopsis). AC-9 requires commands to delegate to services even as stubs — implemented by calling a stub service helper that raises `NotImplementedYet`, which the CLI then translates to the exit-code-2 message.

**Acceptance Criteria:**
- [ ] `orchestrator --help` lists every command from `docs/ui-specification.md` → Command Inventory.
- [ ] Every subcommand's `--help` matches its "Command Specifications" synopsis.
- [ ] Stub commands exit with code `2` and print `error: not implemented yet (FEAT-001 skeleton)`.
- [ ] `--json` flag is global and propagates to subcommands via Typer context.
- [ ] Global options follow the precedence rules (CLI > env > pyproject) from `ui-specification.md`.

**Files to Modify/Create:**
- `src/app/cli.py`.
- `src/app/cli_output.py` — human vs `--json` formatters.
- `tests/test_cli_stubs.py` — uses `typer.testing.CliRunner`.

---

### T-020: `orchestrator serve` command (full)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-012, T-019

**Description:**
Implement `orchestrator serve [--host] [--port] [--reload]` as a thin wrapper over `uvicorn.run("app.main:app", ...)`. Honors global config precedence. Exit code `0` on normal shutdown, `2` on bind failure.

**Rationale:**
Addresses AC-7 (`serve` fully works) and supports AC-1 / AC-2.

**Acceptance Criteria:**
- [ ] `orchestrator serve` starts uvicorn and `GET /health` responds within a timeout.
- [ ] `--port` override is honored.
- [ ] Bind failure (port in use) exits `2` with a clear message.

**Files to Modify/Create:**
- `src/app/cli.py` — implement `serve`.
- `tests/test_cli_serve.py` — smoke test using a random free port.

---

### T-021: `orchestrator doctor` command (full)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-003, T-014, T-017, T-019

**Description:**
Implement `orchestrator doctor`: run the checklist from `ui-specification.md` → `doctor` spec, in order. Render a human checklist with ✓/✗, or a structured JSON report under `--json`. Exit `2` if any check fails; `0` otherwise. For the fresh-clone edge case in FEAT-001 §9: present each missing env var on its own line with the expected name and a one-line hint.

**Rationale:**
Addresses AC-1 directly (`doctor` green on fresh clone) and the fresh-clone edge case.

**Acceptance Criteria:**
- [ ] Every listed check runs in order; failure of one does not short-circuit subsequent checks.
- [ ] Missing `ORCHESTRATOR_API_KEY` / `ENGINE_WEBHOOK_SECRET` / LLM config surface as specific named checks, not a raw `pydantic.ValidationError`.
- [ ] `--json` output matches a documented schema (one `check` object per check with `name`, `status`, `detail`).
- [ ] Exit code `0` when all required checks pass; `2` otherwise.
- [ ] Safe to run before `alembic upgrade head` — DB check reports `"needs migration"` rather than crashing.

**Files to Modify/Create:**
- `src/app/cli.py` — implement `doctor`.
- `src/app/doctor.py` — check registry + runner.
- `tests/test_cli_doctor.py` — covers the missing-env and migration-pending cases.

---

## Deployment

### T-022: Dockerfile (multi-stage)

**Type:** DevOps
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-001

**Description:**
Multi-stage `Dockerfile` based on `python:3.12-slim`. Stage 1: install `uv`, copy `pyproject.toml` + `uv.lock`, `uv sync --frozen --no-dev`. Stage 2: copy `src/` and the synced venv; expose 8000; `CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`. Include `.dockerignore`.

**Rationale:**
Addresses AC-8 (image builds and runs).

**Acceptance Criteria:**
- [ ] `docker build -t orchestrator .` succeeds.
- [ ] Final image size under 300 MB.
- [ ] Image runs as a non-root user.
- [ ] No `.env`, `.git`, `tests/`, or `docs/` in the final image.

**Files to Modify/Create:**
- `Dockerfile`.
- `.dockerignore`.

---

### T-023: Docker Compose (dev + prod) + env examples

**Type:** DevOps
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-022

**Description:**
`docker-compose.yml` for local dev with Postgres 16 (port-published, volume, healthcheck). `docker-compose.prod.yml` for production with the API service on a shared `infra` external network, no infrastructure containers (Postgres lives elsewhere), and `depends_on` with `condition: service_healthy` where applicable. `.env.example` and `.env.production.example` listing every env var consumed by `Settings`.

**Rationale:**
Addresses AC-1 (`docker compose up -d` starts Postgres) and AC-8 (prod build path exists).

**Acceptance Criteria:**
- [ ] `docker compose up -d` brings up Postgres; `pg_isready` healthcheck passes within 30 s.
- [ ] `.env.example` lists: `DATABASE_URL`, `ORCHESTRATOR_API_KEY`, `ENGINE_WEBHOOK_SECRET`, `ENGINE_BASE_URL`, `ENGINE_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, `LOG_LEVEL`, `AGENTS_DIR`.
- [ ] `docker compose -f docker-compose.prod.yml config` validates without errors.
- [ ] `docker compose -f docker-compose.prod.yml build` succeeds.

**Files to Modify/Create:**
- `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `.env.production.example`.

---

## Testing

### T-024: `conftest.py` with fixtures (DB, client, HMAC, StubPolicy)

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-004, T-007, T-008, T-011, T-012

**Description:**
Implement `tests/conftest.py` with session-scoped fixtures: `test_database_url` (uses a unique schema or database per test session), `apply_migrations` (runs Alembic), `db_session` (function-scoped `AsyncSession`), `app` (FastAPI app with overrides), `client` (`httpx.AsyncClient`), `sign_body` (HMAC helper), and `stub_policy` (scripted `StubLLMProvider`). A `respx` fixture for the engine client is also provided.

**Rationale:**
Prerequisite for every integration test. Addresses the "real Postgres, not SQLite" testing rule from CLAUDE.md.

**Acceptance Criteria:**
- [ ] `pytest` session starts Alembic migrations once and tears down at end.
- [ ] `db_session` yields a transactional session rolled back at function end — no test leakage.
- [ ] `client` is an `AsyncClient(transport=ASGITransport(app=app))`.
- [ ] `sign_body(payload, secret)` returns a valid `X-Engine-Signature` value the middleware accepts.
- [ ] `stub_policy` can be overridden per-test with a custom script.
- [ ] The `live` pytest marker is registered and tests with it are deselected unless `--run-live` is passed.

**Files to Modify/Create:**
- `tests/conftest.py`.
- `pyproject.toml` — add `[tool.pytest.ini_options]` markers + `addopts = "--strict-markers"`.

**Technical Notes:**
For per-session DB isolation prefer `CREATE DATABASE orchestrator_test_<uuid>` at session start and drop at end. Transaction-per-test via nested `SAVEPOINT`s keeps tests fast.

---

### T-025: Health + doctor + CLI boot smoke tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-014, T-019, T-020, T-021, T-024

**Description:**
Integration tests: `/health` happy path + DB-down degraded path; `orchestrator doctor` happy path + missing-env path + migration-pending path; `orchestrator serve` starts and serves `/health`; `orchestrator --help` shows every documented command.

**Rationale:**
Addresses AC-1, AC-2, AC-7.

**Acceptance Criteria:**
- [ ] `/health` test asserts full envelope shape.
- [ ] `doctor` missing-env test sets env with one variable unset and asserts that specific ✗ line.
- [ ] `serve` smoke test binds a free port and asserts `/health` returns 200.
- [ ] `--help` assertion iterates the Command Inventory from `ui-specification.md` and asserts each appears.

**Files to Modify/Create:**
- `tests/test_health.py` (exists from T-014; extend).
- `tests/test_cli_doctor.py` (extend from T-021).
- `tests/test_cli_help.py`.

---

### T-026: Webhook endpoint integration tests

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-016, T-024

**Description:**
Cover all five branches of `POST /hooks/engine/events` (valid signed + known run, valid signed + duplicate dedupe key, valid signed + unknown run, invalid signature, signed + malformed payload). Assert DB state in each case.

**Rationale:**
Addresses AC-6 webhook portion + the four webhook-related edge cases in FEAT-001 §9.

**Acceptance Criteria:**
- [ ] Five parameterized cases, each asserting HTTP status AND the persisted `WebhookEvent` row (count + `signature_ok`).
- [ ] Duplicate dedupe-key test asserts only one row after two POSTs.
- [ ] Invalid-signature test asserts the row is persisted with `signature_ok=false`.

**Files to Modify/Create:**
- `tests/modules/ai/test_routes_webhook.py` (extend from T-016).

---

### T-027: Control-plane 501 + auth shape tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-015, T-024

**Description:**
Parameterized tests over every control-plane endpoint: unauthenticated → 401 Problem Details; invalid input → 400 with per-field `errors`; authenticated valid → 501 with `not-implemented` type URI.

**Rationale:**
Addresses AC-6 control-plane portion and AC-9 spirit (the auth check short-circuits before any service logic).

**Acceptance Criteria:**
- [ ] Loop over the endpoint list from `api-spec.md` → Endpoint Summary (minus webhook and health).
- [ ] Each endpoint asserts the three branches above.

**Files to Modify/Create:**
- `tests/modules/ai/test_routes_control_plane.py` (extend from T-015).

---

### T-028: Migration round-trip test

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-011, T-024

**Description:**
Test that runs `alembic upgrade head`, asserts all five tables and documented constraints exist, runs `downgrade base`, asserts they're gone, then `upgrade head` again — against a fresh ephemeral DB.

**Rationale:**
Addresses AC-5 and AC-11's spirit (schema is the contract).

**Acceptance Criteria:**
- [ ] Test completes in under 10 s on local Postgres.
- [ ] Asserts presence of `runs`, `steps`, `policy_calls`, `webhook_events`, `run_memory` plus all UNIQUE constraints from `docs/data-model.md`.
- [ ] `autogenerate` against the upgraded DB produces no diff (catches model/migration drift).

**Files to Modify/Create:**
- `tests/test_migrations_roundtrip.py`.

---

### T-029: Thin-adapter AST check + composition-integrity smoke placeholder

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-013, T-015, T-019

**Description:**
Two tests. **(a)** An AST walker over `src/app/modules/ai/router.py` and `src/app/cli.py` asserting no function in those modules contains SQLAlchemy, httpx, or LLM-related calls — only calls to `app.modules.ai.service` functions, Pydantic validation, and output formatting (AC-9). **(b)** A placeholder composition-integrity test that imports the runtime loop entry-point (even if it's just a module that exists), constructs a `StubLLMProvider` from a canned script, and asserts the wiring reaches `NotImplementedYet` (i.e., the shape is in place even though the loop isn't implemented yet).

**Rationale:**
Addresses AC-9 directly and lays the scaffolding for the AD-3 composition-integrity test that FEAT-002 will flesh out.

**Acceptance Criteria:**
- [ ] AST check catches a deliberate injection (temporarily add `from sqlalchemy import ...` to `router.py` — the test must fail; revert).
- [ ] Composition-integrity placeholder asserts: importing the service module, constructing a stub provider, and calling the designated runtime entry-point raises `NotImplementedYet` (not `ImportError` or `AttributeError`). Marked with a comment pointing at FEAT-002 for the real implementation.

**Files to Modify/Create:**
- `tests/test_adapters_are_thin.py`.
- `tests/test_composition_integrity_smoke.py`.
- `src/app/modules/ai/runtime.py` — minimal stub module with an entry-point function `async def run_loop(...)` that raises `NotImplementedYet`, so the test has something to import.

---

## Polish

### T-030: README commands + developer onboarding section

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-021, T-023, T-024

**Description:**
Expand `README.md` with a short "Getting Started" section: prerequisites, clone + `uv sync`, `docker compose up -d`, `uv run alembic upgrade head`, `uv run orchestrator doctor`, `uv run orchestrator serve`, `uv run pytest`. Link out to `docs/ARCHITECTURE.md` and `CLAUDE.md`. Do NOT duplicate those documents.

**Rationale:**
Closes the onboarding loop for AC-1 end-to-end. Keeps docs DRY.

**Acceptance Criteria:**
- [ ] Running the README commands verbatim on a fresh clone reaches `doctor` green.
- [ ] README is ≤ 150 lines; anything longer belongs in `docs/`.
- [ ] Links to `CLAUDE.md` and `docs/ARCHITECTURE.md` resolve.

**Files to Modify/Create:**
- `README.md`.

---

## Summary

**Total tasks:** 30

**By type:**
| Type | Count |
|------|-------|
| Backend | 15 |
| Database | 3 |
| Testing | 6 |
| DevOps | 3 |
| Documentation | 1 |
| Frontend | 0 |

**By complexity:**
| Complexity | Count |
|------------|-------|
| S | 14 |
| M | 12 |
| L | 4 |
| XL | 0 |

**Critical path (longest dependency chain):**
`T-001 → T-002 → T-003 → T-007 → T-024 → T-025 → T-030` (7 tasks) — the bootstrap + config + LLM stub + fixtures + smoke tests + README onboarding chain. Parallel chains exist (models → migration, routes, CLI) but they all merge at `T-024` (fixtures) and then at `T-030` (end-to-end onboarding verification).

**Suggested parallelization after T-003:**
- Track A — data: T-004 → T-009 → T-010 → T-011.
- Track B — core crosscutting: T-005, T-006, T-007, T-008 in parallel (each independent after T-003).
- Track C — deployment: T-022 → T-023 in parallel with everything else.

**Risks / Open Questions:**

1. **Raw-body capture for HMAC.** The middleware approach (T-008 technical note) changes the request lifecycle globally. If a downstream route ever `await request.body()`s again it won't see a second read. Mitigation: always read from `request.state.raw_body` in route code; add a pytest that fails if a route calls `request.body()` directly. If problematic, fall back to a dependency that reads + caches per route.

2. **UUIDv7 library choice.** The `uuid6` package is third-party; Python stdlib lacks UUIDv7 until 3.13. Either pin `uuid6` now or use UUIDv4 in v1 and migrate later. Flagging so T-009 doesn't get blocked mid-implementation. Recommendation: use `uuid6` — the time-sortable property is meaningful for run-id ordering in traces.

3. **Postgres test isolation strategy.** Two viable options: new DB per session (slow startup, clean) vs. transaction-per-test with SAVEPOINTs (fast, trickier with nested transactions in code-under-test). T-024 picks one; a change later means rewriting many tests. Recommendation: new DB per session via `CREATE DATABASE`; transaction-per-test inside. Add a smoke test that verifies the rollback actually rolls back.

4. **Alembic `autogenerate` vs hand-written migrations.** `autogenerate` is convenient but can miss check-constraint diffs on enum columns. T-011 requires the initial migration to round-trip cleanly; we may need to hand-author the enum check constraints. Budget buffer in T-011's complexity for this.

5. **OpenAPI schema drift.** AC-2 requires `/openapi.json` to match `api-spec.md`. Without an automated check, drift is inevitable. Not in FEAT-001 scope, but an `IMP-002` for a drift check is worth opening after this ships.

6. **`httpx` body-reading + ASGI transport in tests.** When using `httpx.AsyncClient(transport=ASGITransport(app=app))`, the raw-body middleware sees the body once as bytes but streaming routes may behave differently than under uvicorn. Verify `/api/v1/runs/{id}/trace` streaming works under both transports in integration tests.

7. **`live`-marker contract test discipline.** We explicitly allow a `tests/contract/` directory gated by a flag. Nothing prevents it from being ignored forever. Recommendation: open an IMP to run it in a scheduled CI job with secrets, separate from PR CI. Not in scope for FEAT-001.
