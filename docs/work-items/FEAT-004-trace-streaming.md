# Feature Brief: FEAT-004 — Trace Streaming

> **Purpose**: Turn the one remaining `NotImplementedYet` in the control plane into a working NDJSON stream — so `orchestrator runs trace <id> [--follow]` and `GET /api/v1/runs/{id}/trace` deliver a run's JSONL trace as it happens, not just after the fact. Closes the AC-7 observability loop that FEAT-002 opened: "policy inputs, outputs, and selected next-node inspectable".
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-004 |
| **Name** | Trace Streaming (`GET /runs/{id}/trace` + `orchestrator runs trace --follow`) |
| **Target Version** | v0.4.0 |
| **Status** | Completed |
| **Priority** | High |
| **Requested By** | Tech Lead (`ai@techer.com.br`) |
| **Date Created** | 2026-04-18 |

---

## 2. User Story

**As a** solo tech lead driving feature delivery (see `docs/personas/primary-user.md`), **I want to** run `orchestrator runs trace <run-id> --follow` in a terminal and watch steps, policy calls, and webhook events scroll by as the run makes decisions — with optional `--since` and `--kind` filters — **so that** debugging a live run doesn't mean `tail -f .trace/<run-id>.jsonl` through a file path I have to reconstruct by hand, and so a remote operator (or CI job) can watch the same stream over HTTP without SSH'ing to the box.

---

## 3. Goal

`GET /api/v1/runs/{id}/trace` and `orchestrator runs trace <id>` both return the run's complete JSONL trace in insertion order, optionally filtered by `kind` and `since`, optionally *tailing* new lines as the runtime writes them. The stream ends cleanly when the run reaches a terminal state AND the file has no more unread lines. Non-existent runs return `404`. A run with an empty trace returns an empty (but valid) stream and closes. Every assertion in FEAT-002's AC-3 ("JSONL file contains one line per Step + PolicyCall + WebhookEvent ingested") continues to hold — this feature reads that file, it does not change what's written.

---

## 4. Feature Scope

### 4.1 Included

- **Trace-store protocol extension** (`src/app/modules/ai/trace.py`): add a second method to `TraceStore` — `async def tail_run_stream(run_id: uuid.UUID, *, follow: bool = False, since: datetime | None = None, kinds: frozenset[str] | None = None) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]`. Preserve the existing `open_run_stream` for callers that just want a one-shot replay.
- **`JsonlTraceStore.tail_run_stream` implementation**: open the run's `.jsonl` file, yield existing lines in order (filtering by `since` on the DTO's timestamp field when applicable, filtering by `kind` on the NDJSON discriminator). When `follow=True`, after the current EOF, poll every 200 ms for new lines and yield them. Filename-based await: if the file does not yet exist and `follow=True`, poll until it appears or the caller cancels.
- **`NoopTraceStore.tail_run_stream`**: yields nothing (regardless of `follow`). The endpoint closes immediately when the backend is noop — operators running with `TRACE_BACKEND=noop` see an empty stream, not a hang.
- **`service.stream_trace`** (`src/app/modules/ai/service.py`): replaces the `NotImplementedYet` body. Signature `async def stream_trace(run_id, *, db, trace, follow, since, kinds) -> AsyncIterator[str]` where each yielded string is a single NDJSON line ending in `\n`. Logic:
  1. Verify the run exists; raise `NotFoundError` if not.
  2. Delegate to `trace.tail_run_stream(...)`.
  3. For each yielded DTO, emit `json.dumps({"kind": ..., "data": dto.model_dump(mode="json", by_alias=True)}) + "\n"`.
  4. In `follow=True` mode, between iterator reads, check `Run.status` — once terminal AND the underlying iterator has no more lines for 2 consecutive polls, close cleanly.
- **`GET /api/v1/runs/{id}/trace`** endpoint (`router.py`): returns a FastAPI `StreamingResponse(content=<service iterator>, media_type="application/x-ndjson")` with headers `Cache-Control: no-cache` and `X-Accel-Buffering: no` (for nginx-behind-the-scenes setups). Query params: `?follow=true` (bool, default false), `?since=<ISO-8601 timestamp>` (optional), `?kind=step|policy_call|webhook_event` (repeatable).
- **CLI `orchestrator runs trace`** (`src/app/cli.py`): replaces the stub. Supports `--follow`, `--since TIMESTAMP`, `--kind KIND` (repeatable), and `--json` (default is human-formatted, one row per line; `--json` forwards the raw NDJSON to stdout). The CLI opens a `httpx.Client.stream(...)` on the endpoint, iterates `response.iter_lines()`, renders each line, and exits 0 on normal close. SIGINT during `--follow` exits 0 silently (operator-initiated).
- **Control-plane stub audit**: remove `runs trace` from the `_STUBBED_ENDPOINTS` list in `tests/modules/ai/test_routes_control_plane.py` and from `_STUB_INVOCATIONS` in `tests/test_cli_stubs.py`. The full control plane and CLI surface are now real.
- **Documentation updates**: `CLAUDE.md` loses the "Still 501 — deferred to FEAT-004" note; `ui-specification.md` loses the `runs trace` deferral; `README.md`'s First Run section adds `orchestrator runs trace <run-id> --follow` as a natural next step. Changelog entries dated 2026-04-18 on all four user-facing docs.

### 4.2 Excluded

- **Server-sent events / WebSocket transport.** NDJSON over HTTP/1.1 chunked-transfer is enough for v1. WebSocket adds a moving part (upstream proxies, framing) that buys no user-visible capability here.
- **Trace pagination / cursor-based resume.** Every reader downloads from the start unless `--since` is given. Resume-after-cursor is a later observability feature.
- **Server-side tail limits** (`?tail=100`). `--since` covers the most common "only recent" use case; numeric tail is a later convenience.
- **Real-time filtering beyond `kind` + `since`.** No `?tool_name=`, no `?run_id_in=`, no full-text search over payloads. Operators can pipe NDJSON through `jq` if they need richer filters — that's what `--json` is for.
- **Multi-run merge**. The endpoint is always per-run. Watching several runs at once is a client-side concern (`orchestrator runs trace a & orchestrator runs trace b`).
- **Postgres-backed trace-store tail** (AD-5 v2). The current `TraceStore` protocol grows one method; the JSONL implementation fills it. The future Postgres implementation must implement `tail_run_stream` too — but shipping it is out of scope until AD-5 v2 lands.
- **Trace replay from a remote JSONL URL** (e.g., S3, GCS). v1 reads local disk only (AD-5 v1).
- **Any change to what the runtime loop writes to the trace.** This feature is a reader on top of an existing contract; it does not add a new trace kind, modify Step/PolicyCall/WebhookEvent DTOs, or alter the JSONL schema.
- **Cross-process inotify / file-watcher**. Polling every 200 ms is sufficient for human-timescale observability; a watcher library adds a dependency without a user-visible gain.

---

## 5. Acceptance Criteria

- **AC-1**: `GET /api/v1/runs/{id}/trace` on a completed run returns every line from the JSONL file as NDJSON, in insertion order, Content-Type `application/x-ndjson`, and closes. Asserted by an integration test that completes a stub-policy run and reads every line back via the endpoint.
- **AC-2**: `GET /api/v1/runs/{id}/trace?follow=true` on a *running* run streams existing lines immediately, then stays open and yields additional lines as the runtime appends them, then closes cleanly once the run reaches a terminal state and no more lines arrive for two consecutive 200 ms polls. Asserted by an integration test that starts a multi-step run, opens the stream before the run completes, and collects every line end-to-end.
- **AC-3**: `?kind=step` and `?kind=policy_call` filters narrow the stream to only those record kinds; multiple `?kind=` values are OR'd. `?since=<ISO-8601 timestamp>` excludes records with an insertion timestamp strictly earlier than the cutoff. Asserted by parameterized tests.
- **AC-4**: `GET /api/v1/runs/{id}/trace` on a non-existent run returns `404` Problem Details (no partial stream started). Asserted by a direct test.
- **AC-5**: `GET /api/v1/runs/{id}/trace` with `TRACE_BACKEND=noop` returns an empty body (HTTP 200) and closes immediately — not a hang, not a 501, not a 5xx.
- **AC-6**: `orchestrator runs trace <id>` without `--follow` prints every trace line once, exit code 0. `--follow` on a still-running run blocks until the run terminates, then exits 0. `--json` emits the raw NDJSON from the server verbatim. Asserted by `respx`-mocked CLI tests.
- **AC-7**: `orchestrator runs trace <id>` on a non-existent run prints a clear "run not found" message to stderr and exits with code 1.
- **AC-8**: Concurrent readers: two `tail_run_stream(follow=True)` coroutines on the same run ID each see every line in order, with no cross-contamination. Asserted by a direct unit test on `JsonlTraceStore`. The existing per-run `asyncio.Lock` in `JsonlTraceStore` protects writes; readers use read-only file handles and never acquire the lock.
- **AC-9**: Filter semantics are pure: the `tail_run_stream` signature's `kinds` parameter is a `frozenset[str]`; empty set or `None` means "all kinds". `since` is an aware `datetime`. Verified by a direct unit test.
- **AC-10**: `uv run pyright` and `uv run ruff check .` stay clean. Full `uv run pytest` suite green. No previously-passing test modified except `test_routes_control_plane.py` + `test_cli_stubs.py` which shrink their stubbed lists.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|-----------------|--------------------|
| `Run` | Read-only. `stream_trace` checks `run.status` to decide when to close a `follow=true` stream. | Terminal states (`completed`, `failed`, `cancelled`) signal "no more writes coming". |
| `Step`, `PolicyCall`, `WebhookEvent` (via DTOs) | Read-only. The trace store replays them via typed DTOs; the endpoint serializes those DTOs to JSON with camelCase aliases. | Append-only invariant (FEAT-002) guarantees that once a record is in the trace file it is never rewritten — so readers never see partial updates. |

**New entities required:** None. This feature is a reader on top of the FEAT-002 contract.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs/{id}/trace` | GET | **Now real** | NDJSON stream; query params `follow`, `since`, `kind` (repeatable). |

**New endpoints required:** None (the endpoint already exists as a 501 stub per `api-spec.md`).

Response contract (unchanged from `api-spec.md`'s reservation in FEAT-001):
- Status: `200 OK`.
- `Content-Type: application/x-ndjson`.
- Body: one JSON object per line, each `{"kind": "step" | "policy_call" | "webhook_event", "data": <DTO with camelCase aliases>}`.
- `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- `Transfer-Encoding: chunked` (FastAPI handles this via `StreamingResponse`).

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| CLI (`orchestrator runs trace`) | **Now real** | Consumes the endpoint. Options `--follow`, `--since`, `--kind`, `--json`. Exit codes: `0` normal close or SIGINT, `1` run not found, `2` missing API key, `3` server 5xx. |

**New screens required:** None (v1 is CLI-only per stakeholder scope).

---

## 9. Edge Cases

- **Run exists but trace file does not yet** (service started, no writes yet): non-follow mode returns an empty stream immediately. Follow mode polls until the file appears, then streams as lines arrive.
- **Run is cancelled mid-stream**: the `follow=true` stream observes the final CANCELLED `Run` row, drains any remaining lines, closes. No error.
- **Run dies via `stop_reason=error`**: the stream drains and closes normally — the terminal-state check covers `failed` just like `completed`.
- **Operator hits Ctrl-C in `--follow` mode**: `httpx.Client.stream` raises `httpx.ReadError` or `KeyboardInterrupt`; the CLI catches either, exits 0 silently. The server's `StreamingResponse` tears down on client disconnect (FastAPI/Starlette default behavior).
- **Server restart mid-stream** (orchestrator process dies): the HTTP connection drops. Clients see the connection close; `--follow` CLI exits with code 3 (5xx class). The zombie-run reconciliation flips any `running` rows to `failed` on restart (FEAT-002), so a subsequent `orchestrator runs trace --follow` on the same run sees the terminal state and exits cleanly.
- **Malformed line in the JSONL file** (shouldn't happen — the writer commits whole lines atomically — but disk corruption, power loss, etc.): the tail loop logs a `WARNING` with the line number and skips it. The stream does not error.
- **Very large trace file** (e.g., 100 k lines): the non-follow endpoint streams them incrementally (no buffering). Memory footprint stays constant; no OOM.
- **Clock skew between writer and `since` filter**: timestamps in the trace are writer-side `created_at`. If the client's `since` is in the future, the stream is empty until a record with a later timestamp arrives (or the stream closes on terminal state).
- **Two readers racing a writer**: the `JsonlTraceStore` writer holds a per-run `asyncio.Lock` only for the duration of one `f.write(...) + f.flush()`. Readers never touch that lock. Each reader opens its own file handle. No cross-contamination.
- **`--kind invalid-kind`**: the server ignores unknown kinds (no 400), returning `kinds = {valid subset}`. The CLI's `--kind` option is a free-form string; validation happens server-side with a silent drop (kinds not in the enum are never emitted by the writer, so filtering to them is a no-op).

---

## 10. Constraints

- MUST NOT change what the runtime loop writes to the JSONL trace. Every AC in FEAT-002 still holds.
- MUST NOT introduce a new dependency (no `watchdog`, `aiofiles-watch`, SSE library, etc.). Polling + existing `aiofiles` is sufficient.
- MUST respect the thin-adapter rule: the router / CLI stay thin; the reader logic lives in `trace.py` / `service.py`.
- MUST keep the `TraceStore` protocol narrow — one new method, no breaking changes to `record_step` / `record_policy_call` / `record_webhook_event` / `open_run_stream`.
- MUST honor the existing `tests/test_adapters_are_thin.py` quarantine (no new forbidden imports in router/CLI).
- MUST preserve `raw_response` redaction from FEAT-003 — the stream only ever emits the same DTO fields that the JSONL writer persisted; no "helpful" additions by the stream layer.
- MUST NOT cache trace responses at the HTTP layer (`Cache-Control: no-cache`) — streams must be re-readable from scratch on every request.
- `--follow` CLI timeout semantics: the CLI does not impose its own wall-clock timeout on `--follow`. The operator decides when to stop (Ctrl-C). This mirrors `tail -f`'s contract.
- Work ships as a small stack of PRs, each leaving `doctor`, `pyright`, `ruff`, and the full test suite green. No partial-merge half-wired states.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-002 persistently writes every run's decisions to disk, but the only way to read them back today is via the filesystem (`cat .trace/<run-id>.jsonl`). That's fine for a local developer and useless for a remote operator, a CI job, or a second terminal where the user wants to watch a `--follow` stream without interleaving with `orchestrator serve`'s stdout. It's also an un-closed loop with the stakeholder's "observability is non-negotiable" principle — observability that requires SSH is not observability for a team operating the service. This feature closes the loop with almost no new surface area: one endpoint, one CLI command, one `TraceStore` method.

**Impact if delayed:** The observability story stays half-done — FEAT-002 writes traces; nothing reads them over HTTP. The remaining `NotImplementedYet` in `service.py` + stub in `cli.py` are visible blemishes that every demo has to caveat. FEAT-005 (lifecycle agent) and any future web UI both land faster with a working stream in place.

**Dependencies on this feature:** Any future UI / observability dashboard. Also a clean prerequisite for FEAT-005's self-hosted feature-delivery story — a lifecycle agent producing dozens of trace lines per run becomes exponentially more useful to watch than to re-read after the fact.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` — the solo tech lead who already runs `tail -f` on log files and wants `orchestrator runs trace --follow` to do the obvious thing. |
| **Stakeholder Scope Item** | "Observability hooks: per-node traces, policy call inputs/outputs, timing, and run inspection." FEAT-002 did the writing half; FEAT-004 does the reading half. |
| **Success Metric** | "Policy traceability" (each policy call's inputs, outputs, and selected next-node inspectable) — now inspectable *over HTTP*, not just on disk. |
| **Related Work Items** | Predecessor: FEAT-002 (runtime loop + JSONL trace writer — complete), FEAT-003 (Anthropic provider — complete; its `raw_response` flows unchanged through this endpoint). Successor: FEAT-005 (lifecycle agent + self-hosted feature delivery). |
