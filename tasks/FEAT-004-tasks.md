# Task Breakdown: FEAT-004 — Trace Streaming

> **Source:** `docs/work-items/FEAT-004-trace-streaming.md`
> **Generated:** 2026-04-18
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

Tasks are grouped in build order: Foundation (trace-store protocol + JSONL tail) → Backend (service + endpoint) → CLI → Testing → Polish. Every task's **Workflow** is `standard`; no new screens (v1 is CLI-only). Task IDs continue from FEAT-003's final `T-076`.

---

## Foundation

### T-077: Extend `TraceStore` protocol with `tail_run_stream`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add `async def tail_run_stream(run_id, *, follow=False, since=None, kinds=None)` to the `TraceStore` protocol in `src/app/modules/ai/trace.py`. `NoopTraceStore.tail_run_stream` yields nothing (regardless of `follow`). Leave `open_run_stream` intact — that method stays as a simple one-shot replay API; `tail_run_stream` is the richer reader that the streaming endpoint drives.

**Rationale:**
T-078 (`JsonlTraceStore.tail_run_stream`) needs the protocol to land first so the signature is pinned across impls. Shipping this as a tiny commit keeps the protocol change visible and reviewable.

**Acceptance Criteria:**
- [ ] `TraceStore.tail_run_stream` is declared with the exact signature from the feature brief (`follow: bool = False`, `since: datetime | None = None`, `kinds: frozenset[str] | None = None`).
- [ ] `NoopTraceStore.tail_run_stream` returns an empty async iterator — drivable with `async for` and yields nothing regardless of arguments.
- [ ] `isinstance(NoopTraceStore(), TraceStore)` stays True (runtime-checkable).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
- [ ] Existing tests stay green — no regression on `open_run_stream` or the runtime writers.

**Files to Modify/Create:**
- `src/app/modules/ai/trace.py` — add method to protocol + noop impl.
- `tests/modules/ai/test_trace_noop.py` — add one case asserting `tail_run_stream` yields nothing under all flag combinations.

**Technical Notes:**
Use `AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto]` — the same return type as `open_run_stream`. Don't narrow `kinds` to an enum; free-form strings keep the protocol simple and match the wire format (the writer already discriminates via the `"kind"` JSON key).

---

### T-078: `JsonlTraceStore.tail_run_stream` — polling tail + filters

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-077

**Description:**
Implement `tail_run_stream` on `JsonlTraceStore`. Non-follow mode: open the file, yield every line (filtered by `kinds` / `since`), close. Follow mode: after the current EOF, poll every 200 ms for new lines and yield them. Filename-await: if the file doesn't yet exist and `follow=True`, poll until it appears (caller cancellation stops the loop via `asyncio.CancelledError` propagating through the sleep).

**Rationale:**
The reader is the load-bearing piece of FEAT-004. Its correctness — interleave with the writer, filter purely, close cleanly on cancel — determines whether the endpoint and the CLI behave.

**Acceptance Criteria:**
- [ ] Non-follow: every committed line yielded exactly once, in order; iterator closes on EOF.
- [ ] Follow: same, then stays open yielding new lines as they appear in the file. Caller can stop the loop by breaking out of `async for` (the underlying aiofiles handle closes on scope exit).
- [ ] Filename-await under follow: file created *after* `tail_run_stream` is first called is still streamed from the start.
- [ ] `since` filter rejects records whose DTO timestamp is strictly earlier than the cutoff. Records without a natural timestamp field (i.e., a kind the writer hasn't stamped) pass through unchanged.
- [ ] `kinds` filter: `None` or empty set → "all"; otherwise only records whose `kind` is in the set are yielded.
- [ ] Malformed line mid-file → log `WARNING` with line number + continue (stream does not error).
- [ ] Two concurrent `tail_run_stream` coroutines against the same run see the same lines in the same order (no cross-contamination; no lock contention).
- [ ] `uv run pyright` + `uv run ruff check .` clean.

**Files to Modify/Create:**
- `src/app/modules/ai/trace_jsonl.py` — new `tail_run_stream` method + private `_tail` async generator.
- `tests/modules/ai/test_trace_jsonl.py` — 6 new cases: non-follow happy, follow across writes, filename-await, kinds filter, since filter, concurrent readers, malformed-line skip-with-warn.

**Technical Notes:**
Reuse the existing `_DTO_BY_KIND` discriminator map from `_replay` for non-follow — don't duplicate DTO hydration logic. For follow, use `await asyncio.sleep(0.2)` between reads; do NOT use `asyncio.wait_for` or any cancellation scope — caller's `asyncio.CancelledError` propagates naturally. Open the file read-only; never touch the `_locks` dict used by writers. `since` compare: use the DTO's `created_at` where present (policy_call + step), or `received_at` for webhook events; the DTO's own field names drive this.

---

## Backend

### T-079: `service.stream_trace` — terminal-state close detection

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-078

**Description:**
Replace the `NotImplementedYet` body in `src/app/modules/ai/service.py`'s `stream_trace`. New signature: `async def stream_trace(run_id, *, db, trace, follow, since, kinds) -> AsyncIterator[str]`. Yields one NDJSON line per record (ending in `\n`). In `follow=True` mode, closes cleanly once `Run.status` is terminal AND the underlying iterator has produced no lines for 2 consecutive 200 ms polls.

**Rationale:**
The service layer owns "when to stop" — the trace store doesn't know about `Run` status. AC-2 ("closes cleanly once the run reaches a terminal state and no more lines arrive for two consecutive 200 ms polls") is a service-layer concern.

**Acceptance Criteria:**
- [ ] Non-existent run id → `NotFoundError` raised BEFORE any bytes are yielded (endpoint returns 404 Problem Details, not a partial stream).
- [ ] Non-follow: yields every line, closes.
- [ ] Follow + completed run: yields every line then closes within ~400 ms (two 200 ms idle polls).
- [ ] Follow + cancelled run: same behavior — terminal-state check treats `cancelled` / `failed` / `completed` uniformly.
- [ ] Each yielded line is a valid single-object NDJSON: `json.dumps({"kind": ..., "data": dto.model_dump(mode="json", by_alias=True)}) + "\n"`.
- [ ] `kinds` + `since` filters forwarded to the trace store verbatim.

**Files to Modify/Create:**
- `src/app/modules/ai/service.py` — real `stream_trace` body.
- `tests/modules/ai/test_service_stream_trace.py` — unit tests with a `JsonlTraceStore` pointed at `tmp_path` and a `FakeTerminalRun` that toggles from running → completed between reads.

**Technical Notes:**
Use `repository.get_run_by_id` for the 404 check before touching the trace store. For the terminal-close detection, wrap the underlying async iterator in an async generator that tracks `(produced_any_this_poll, run_status)` and exits after two consecutive empty polls once `run_status` is terminal. Keep the 200 ms value as a module-level constant (`_TAIL_POLL_SECONDS = 0.2`) so tests can monkeypatch it to zero for speed.

---

### T-080: `GET /api/v1/runs/{id}/trace` endpoint

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-079

**Description:**
Rewrite the placeholder route in `src/app/modules/ai/router.py` to return a FastAPI `StreamingResponse(content=service.stream_trace(...), media_type="application/x-ndjson")` with headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Query params: `follow: bool = False`, `since: datetime | None = None`, `kind: list[str] | None = None` (repeatable, collected into a `frozenset[str]`).

**Rationale:**
Completes the control-plane surface. Everything the service layer owns is already done; this is the thin adapter.

**Acceptance Criteria:**
- [ ] `GET /api/v1/runs/{id}/trace` returns 200 with `Content-Type: application/x-ndjson` on a known run.
- [ ] Response has `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers.
- [ ] `?follow=true` is accepted; `?since=2026-04-18T00:00:00Z` is accepted; `?kind=step&kind=policy_call` is accepted and forwarded as a frozenset.
- [ ] Unknown run id → 404 Problem Details (RFC 7807).
- [ ] Adapter-thin check still passes (no forbidden imports in router.py).
- [ ] Endpoint returns an **empty body** (200) when `TRACE_BACKEND=noop`.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — replace the stub route.
- `tests/modules/ai/test_routes_control_plane.py` — remove `/runs/{id}/trace` from `_STUBBED_ENDPOINTS`.

**Technical Notes:**
Parameters: in FastAPI, `kind: list[str] | None = Query(default=None)` gives a repeatable query param. Convert to `frozenset(kind) if kind else None` before passing to the service. `since: datetime | None = None` — FastAPI parses ISO-8601 automatically. Bearer auth is already enforced by the `api_router`'s `dependencies=[Depends(require_api_key)]`, so don't re-declare auth on the route.

---

## CLI

### T-081: `orchestrator runs trace` real body

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-080

**Description:**
Replace `_not_implemented("runs trace")` in `src/app/cli.py` with a real body. Open `httpx.Client.stream("GET", "/api/v1/runs/{id}/trace", params=...)`, iterate `response.iter_lines()`, and render. Supports `--follow` (maps to `?follow=true`), `--since <ISO-8601>` (maps to `?since=`), `--kind <kind>` repeatable (maps to `?kind=`), and `--json` (forwards raw lines to stdout). Without `--json`, render each NDJSON record as a compact human line.

**Rationale:**
AC-6 + AC-7 — the CLI is the primary consumer. SIGINT during `--follow` exits 0 silently (operator-initiated cancellation).

**Acceptance Criteria:**
- [ ] Default (no `--json`): one human-readable line per record, e.g., `step #1 analyze_brief completed (eng-abc123)`.
- [ ] `--json`: each line from the server forwarded verbatim to stdout (no parse/re-serialize).
- [ ] Query string built correctly: `?follow=true`, `?since=<iso>`, `?kind=step&kind=policy_call`.
- [ ] Non-existent run id → stderr line "error: run not found: {id}" and exit code 1 (handled by `_handle_response`).
- [ ] Missing API key → stderr + exit 2 (reuses `_require_api_key`).
- [ ] SIGINT in `--follow` mode → exit 0 (no traceback, no error banner).
- [ ] Adapter-thin check still passes (CLI only imports `httpx`, not `anthropic`/`sqlalchemy`).

**Files to Modify/Create:**
- `src/app/cli.py` — real `runs_trace` body; add a `_render_trace_line` helper in `cli_output.py`.
- `src/app/cli_output.py` — new `render_trace_line(record: dict) -> str` for the human format.
- `tests/test_cli_stubs.py` — remove `runs trace` from `_STUB_INVOCATIONS` (the stub list is now empty → turn that into a regression guard or remove the class).

**Technical Notes:**
Use `with client.stream("GET", url, params=...) as response` so the connection is closed on exit. Handle 4xx/5xx BEFORE iterating (`response.raise_for_status()`) — check `response.status_code` and pass through `_handle_response(response)`. Wrap the `for line in response.iter_lines()` loop in `try/except KeyboardInterrupt: pass` so Ctrl-C exits cleanly. The `since` option's CLI type is `str` — forward as-is; the server parses.

---

## Testing

### T-082: `JsonlTraceStore.tail_run_stream` unit tests

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-078

**Description:**
Seven unit tests in `tests/modules/ai/test_trace_jsonl.py` covering the reader:
1. Non-follow returns every committed line once, in order, then closes.
2. Non-follow + missing file → empty iterator (no error).
3. Follow across writes: writer appends N more lines concurrently; reader yields all N.
4. Filename-await under follow: open reader before file exists, write lines after, reader still sees them.
5. `kinds={"step"}` filter narrows correctly.
6. `since` filter excludes records with earlier timestamps.
7. Two concurrent readers on the same run see the same lines in the same order.
8. Malformed line (e.g., half-written JSON) → WARNING log + continue, stream does not error.

**Rationale:**
The reader is the correctness-critical piece; each invariant from the brief needs a test it actually fails if broken.

**Acceptance Criteria:**
- [ ] 8 parameterized or separate test methods as listed.
- [ ] All pass under `uv run pytest tests/modules/ai/test_trace_jsonl.py`.
- [ ] Total runtime under 3 s (use `monkeypatch.setattr` to shrink the 200 ms poll interval inside follow tests).

**Files to Modify/Create:**
- `tests/modules/ai/test_trace_jsonl.py` — extend existing file.

**Technical Notes:**
For the concurrent-reader test, use `asyncio.gather(reader_a_task, reader_b_task, writer_task)` with a small fixture of 5 writes spaced across `asyncio.sleep(0.01)` so both readers race. For the malformed-line test, write a file directly (bypassing the store) with one valid line + one garbage line + one valid line, confirm yield count == 2 + one WARNING in caplog (or use the spy trick from T-068 if caplog is unreliable here).

---

### T-083: Route integration tests

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-080

**Description:**
Integration tests in a new `tests/integration/test_trace_stream_route.py`:
1. **Happy completed run**: use `integration_env` to complete a short stub-policy run, then `GET /runs/{id}/trace` — assert `Content-Type`, every known record kind appears, response closes.
2. **404 on unknown run**: assert Problem Details body + no partial stream.
3. **`?kind=step`** filter: only `step` records returned.
4. **`?kind=step&kind=policy_call`**: only those two kinds.
5. **`?since=<future>`**: empty stream.
6. **Noop backend**: with `TRACE_BACKEND=noop` override on `get_trace_store`, the endpoint returns 200 + empty body + closes.
7. **Follow mode against completed run**: `?follow=true` returns every line then closes within 1 s (poll constants monkeypatched).

**Rationale:**
ACs 1, 3, 4, 5 — covers every query-string permutation and the noop fallback.

**Acceptance Criteria:**
- [ ] 7 test methods, all green.
- [ ] Each test asserts on `response.headers["content-type"]` AND body content.
- [ ] Runtime under 5 s total.

**Files to Modify/Create:**
- `tests/integration/test_trace_stream_route.py` — new file, reuses `integration_env`.

**Technical Notes:**
Use `async with env.client.stream("GET", f"/api/v1/runs/{run_id}/trace", params=...) as resp: lines = [line async for line in resp.aiter_lines() if line]`. For the noop test, override `get_trace_store` to return `NoopTraceStore()` at the dep-injection layer.

---

### T-084: End-to-end follow-mode streaming test

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-080

**Description:**
One integration test in `tests/integration/test_trace_stream_follow.py`:
1. Start a 3-step stub-policy run with `engine_delay_seconds=0.3` so the run takes ~1 s.
2. Immediately open `GET /runs/{id}/trace?follow=true` against the ASGI client.
3. Collect every line from the stream as an async task.
4. Wait for the run to reach a terminal state.
5. Assert the collected set matches the full trace — every step / policy_call / webhook_event appears.
6. Assert the stream closes within 1 s of the run reaching terminal state.

**Rationale:**
AC-2 headliner — proves the live-tail path end-to-end. Mirrors FEAT-002's composition-integrity test in structure.

**Acceptance Criteria:**
- [ ] Test passes deterministically within 5 s wall-clock.
- [ ] Every record kind appears at least once in the collected lines.
- [ ] Stream closes (the async iterator completes) after the run is terminal.

**Files to Modify/Create:**
- `tests/integration/test_trace_stream_follow.py` — new file.

**Technical Notes:**
`monkeypatch` the service's `_TAIL_POLL_SECONDS` and JsonlTraceStore's internal poll interval to ~0.02 s for snappy test. Use `asyncio.gather(_collect_stream(), _drive_run_to_terminal())` so the stream and the run run concurrently. The stream iterator must be consumed on the same event loop as the run.

---

### T-085: CLI tests (`orchestrator runs trace`)

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-081

**Description:**
Extend `tests/test_cli_runs.py` (or a new `tests/test_cli_trace.py`) with:
1. Mock the endpoint with `respx` returning 3 NDJSON lines; run `orchestrator runs trace <id>`; assert 3 lines of human-formatted output, exit 0.
2. `--json` mode forwards lines verbatim (byte-identical to what respx returned).
3. `--kind step --kind policy_call` — assert outbound URL has both query params.
4. `--since 2026-04-17T12:00:00Z` — assert outbound URL carries it.
5. Missing API key → exit 2.
6. Server returns 404 Problem Details → exit 1, "run not found" on stderr.
7. `--follow` without any options — assert outbound URL has `?follow=true`.

**Rationale:**
AC-6 + AC-7 coverage at the CLI boundary.

**Acceptance Criteria:**
- [ ] 7 respx-mocked CLI tests, all green.
- [ ] `--json` test asserts byte-identical output (important: the CLI must not reformat when `--json`).
- [ ] Each test completes in < 200 ms.

**Files to Modify/Create:**
- `tests/test_cli_runs.py` — add `TestRunsTrace` class.

**Technical Notes:**
`respx` supports streaming responses: `mock.get(...).mock(return_value=httpx.Response(200, content=b"line1\nline2\nline3\n", headers={"content-type": "application/x-ndjson"}))`. Use `CliRunner.invoke` — its captured stdout is the CLI's full output. Strip a trailing newline if the CLI prints one.

---

## Polish

### T-086: Stub-list audit + documentation updates

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-080, T-081

**Description:**
Clean up remaining stub references and update the docs:

1. **`tests/modules/ai/test_routes_control_plane.py`** — `_STUBBED_ENDPOINTS` must be empty; either delete the `TestAuthenticatedStub501` class + the list entirely, or keep it as a structural guard asserting the list is `[]` (preferred — future stubs regress instantly).
2. **`tests/test_cli_stubs.py`** — `_STUB_INVOCATIONS` drops `runs trace`. Either empty the list (and keep the class asserting empty) or delete `TestStubsExit2` outright.
3. **`CLAUDE.md`** — Remove any "FEAT-004" forward-reference notes about `runs trace`; replace with a short entry under "Runtime Loop" → "Observability" naming the NDJSON stream. No new Pattern/Anti-pattern entries.
4. **`docs/ARCHITECTURE.md`** — Add a one-line entry under Runtime Loop Components for the streaming endpoint + a 2026-04-18 FEAT-004 changelog entry.
5. **`docs/ui-specification.md`** — Remove the "deferred to FEAT-004" note on `--follow` / `runs trace`; write the real behavior table. Add changelog entry.
6. **`docs/api-spec.md`** — Replace any "501 stub" language for `/runs/{id}/trace` with the real response shape. Changelog entry.
7. **`README.md`** — Extend the "First Run" section with `orchestrator runs trace <id> --follow` as a next step; total file stays ≤ 150 lines.
8. **`docs/work-items/FEAT-004-trace-streaming.md`** — flip Status to `Completed`.

**Rationale:**
CLAUDE.md's Documentation Maintenance Discipline. The brief said doc-first; now the docs catch up to the shipped behavior.

**Acceptance Criteria:**
- [ ] `_STUBBED_ENDPOINTS` and `_STUB_INVOCATIONS` are either empty (kept as regression guards) or deleted.
- [ ] `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/ui-specification.md`, `docs/api-spec.md` each have a 2026-04-18 FEAT-004 changelog entry.
- [ ] `README.md` stays ≤ 150 lines.
- [ ] FEAT-004 brief Status = Completed.
- [ ] No doc claims a behavior the shipped code doesn't have.

**Files to Modify/Create:**
- `tests/modules/ai/test_routes_control_plane.py`
- `tests/test_cli_stubs.py`
- `CLAUDE.md`
- `docs/ARCHITECTURE.md`
- `docs/ui-specification.md`
- `docs/api-spec.md`
- `README.md`
- `docs/work-items/FEAT-004-trace-streaming.md`

**Technical Notes:**
One-line changelog entries, dated, FEAT-referenced. Don't editorialize. `data-model.md` is NOT in this list — no entity changes.

---

## Summary

### Task Count by Type

| Type | Count |
|------|-------|
| Backend | 5 (T-077, T-078, T-079, T-080, T-081) |
| Testing | 4 (T-082, T-083, T-084, T-085) |
| Documentation | 1 (T-086) |
| **Total** | **10** (T-077 through T-086) |

### Complexity Distribution

| Complexity | Count | Tasks |
|------------|-------|-------|
| S | 4 | T-077, T-080, T-085, T-086 |
| M | 6 | T-078, T-079, T-081, T-082, T-083, T-084 |
| L | 0 | — |
| XL | 0 | — |

### Critical Path

`T-077 → T-078 → T-079 → T-080 → T-081 → T-086`

Six tasks in the longest chain; the remaining four (T-082, T-083, T-084, T-085) branch off each once its dependency lands and can be written in parallel. Smaller than FEAT-003 (13 tasks) because FEAT-004 is a pure reader — no new dependency, no retry semantics, no new auth surface, no error-mapping matrix.

### Risks / Open Questions

- **Test timing flakiness under follow.** `--follow` tests need the poll interval monkeypatched to near-zero to keep CI fast. Document the knob in `service.py` (`_TAIL_POLL_SECONDS`) and `trace_jsonl.py`'s tail sleep so both are trivially overridable. Any test that *doesn't* monkeypatch should stay short (< 2 s generous bound).
- **`StreamingResponse` and exception handling.** If `service.stream_trace` raises mid-stream (e.g., the file disappears), FastAPI closes the connection mid-body — the client sees a truncated response, not a Problem Details body. Acceptable for v1 (the `NotFoundError` raised *before* yield covers the 404 case; in-stream errors are operator-visible). Document in the endpoint docstring.
- **Cross-worker buffering.** Uvicorn's default worker + nginx's default buffering can accumulate output before flushing. `X-Accel-Buffering: no` handles nginx; uvicorn flushes per `yield`. If operators deploy behind a different proxy that buffers NDJSON, they'll need a separate tuning step — document in `README.md` later if it bites.
- **Anthropic `raw_response` size.** A single PolicyCall's `raw_response` can be several KB; streaming emits it verbatim. No size cap in v1 (per stakeholder scope — observability is non-negotiable). If bandwidth becomes a concern, a future IMP adds a compact projection.
- **JSONL-to-Postgres migration** (AD-5 v2). Whoever implements `PostgresTraceStore.tail_run_stream` must match the contract: same ordering, same filter semantics, same terminal-state close. A thin protocol-conformance test (run any `TraceStore` impl through the same assertions) lands alongside that future IMP, not this feature.
