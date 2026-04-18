# Task Breakdown: FEAT-003 — Real LLM Provider (Anthropic)

> **Source:** `docs/work-items/FEAT-003-anthropic-provider.md`
> **Generated:** 2026-04-18
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

Tasks are grouped in build order: Foundation (settings + adapter guardrail) → Backend (provider implementation in layers) → Integration (factory wiring + doctor) → Testing (unit, e2e, contract, security) → Polish (docs). Every task's **Workflow** is `standard`; v1 is CLI-only, and this feature adds no screens. Task IDs continue from FEAT-002's final `T-062`.

---

## Foundation

### T-063: Settings — required Anthropic fields + validation

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add a `model_validator` on `Settings` that enforces `anthropic_api_key` presence when `llm_provider == "anthropic"`. Introduce `anthropic_max_tokens: int = 4096` and `anthropic_timeout_seconds: int = 60`. Default `llm_model` to `"claude-opus-4-7"` when provider is anthropic (leave `None` when stub). Validation MUST fail at `Settings()` construction — not on first API call — so a misconfigured process never starts.

**Rationale:**
AC-2 requires a clear `ValidationError` at settings-load time with `llm_provider=anthropic` and no key. Landing the settings surface first unblocks every provider-facing task to reference the concrete field names.

**Acceptance Criteria:**
- [ ] `Settings(llm_provider="anthropic")` without `ANTHROPIC_API_KEY` in env raises `pydantic.ValidationError` with a clear message naming the missing field.
- [ ] `Settings(llm_provider="stub")` without `ANTHROPIC_API_KEY` continues to succeed (unchanged behavior).
- [ ] `Settings(llm_provider="anthropic", llm_model=None)` resolves to `llm_model == "claude-opus-4-7"` via the validator.
- [ ] `anthropic_max_tokens` and `anthropic_timeout_seconds` have sane defaults and are strictly positive.
- [ ] `test_config.py::test_all_fields_present` updated to include the new fields.
- [ ] Pyright + ruff clean.

**Files to Modify/Create:**
- `src/app/config.py` — new fields, `model_validator(mode="after")`.
- `tests/test_config.py` — new/existing assertions.

**Technical Notes:**
Keep the validator strict: reject `anthropic_api_key` strings that are empty/whitespace-only. Do NOT attempt shape validation here (`sk-ant-…` prefix) — that belongs in `doctor` (T-069) so settings stays a pure data-coercion layer.

---

### T-064: Adapter-thin check — quarantine the `anthropic` import

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Extend `tests/test_adapters_are_thin.py` with a second static check that walks `src/app/` (excluding `src/app/core/llm.py` and `src/app/core/llm_anthropic.py`) and fails on any `import anthropic` or `from anthropic import …`. Ships BEFORE the provider implementation so accidental leakage fails CI from day one.

**Rationale:**
AC-9 requires the `anthropic` SDK to live only behind the `LLMProvider` seam. Landing the guardrail first catches future drift automatically.

**Acceptance Criteria:**
- [ ] New `TestAnthropicImportQuarantine` class asserts zero `anthropic` imports anywhere under `src/app/` except `core/llm.py` + `core/llm_anthropic.py`.
- [ ] Sanity check asserts the walker flags an injected offending import (mirror `TestSanityCheck` pattern).
- [ ] Existing thin-adapter tests continue to pass.

**Files to Modify/Create:**
- `tests/test_adapters_are_thin.py` — new test class + allow-list.

**Technical Notes:**
Reuse the existing AST walker — add a dedicated scanner that walks a directory tree with a tiny allow-list, separate from the `router.py`/`cli.py` allow-list which already uses a different shape.

---

## Backend

### T-065: Add `anthropic` to runtime deps + `AnthropicLLMProvider` skeleton

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-063, T-064

**Description:**
Move `anthropic>=0.40,<1` from `[project.optional-dependencies].anthropic` to runtime `dependencies` (it's no longer optional once provider support ships). Create `src/app/core/llm_anthropic.py` with an `AnthropicLLMProvider(settings: Settings)` class: wires the async client, stores `model`, `max_tokens`, `timeout`; sets `name = "anthropic"`. `chat_with_tools` raises `NotImplementedYet` until T-066.

**Rationale:**
Small commit that sets up the module + import footprint so the next three backend tasks each add one focused layer (happy path, threading, retry) against a stable scaffold.

**Acceptance Criteria:**
- [ ] `anthropic` appears under `[project].dependencies` (and is removed from `optional-dependencies`).
- [ ] `uv.lock` regenerated.
- [ ] `AnthropicLLMProvider` is `isinstance`-compatible with the `LLMProvider` runtime-checkable protocol (`name`, `model`, `chat_with_tools`).
- [ ] Constructor does not make network calls.
- [ ] Thin-adapter check from T-064 passes (import is allowed in `llm_anthropic.py`).

**Files to Modify/Create:**
- `pyproject.toml` — deps.
- `src/app/core/llm_anthropic.py` — class scaffold.
- `tests/modules/core/test_llm_anthropic_construction.py` (or extend `tests/test_config.py` if simpler) — "constructor doesn't hit the network", "protocol match".

**Technical Notes:**
Use `anthropic.AsyncAnthropic`, not the sync client. Pass `api_key=settings.anthropic_api_key.get_secret_value()`, `timeout=settings.anthropic_timeout_seconds`. Keep the client on the instance; share across runs.

---

### T-066: `AnthropicLLMProvider.chat_with_tools` — one-shot happy path

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-065

**Description:**
Implement the single-turn happy path:
1. Translate `Sequence[ToolDefinition]` into Anthropic's `tools=[{name, description, input_schema}]` shape.
2. Forward `system` + `messages` verbatim.
3. `messages.create(model=…, max_tokens=…, system=…, messages=…, tools=…, tool_choice={"type": "auto"})`.
4. Parse the response's first `tool_use` content block → `ToolCall(name, arguments, usage=Usage(input_tokens, output_tokens, latency_ms), raw_response=…)`.
5. `latency_ms` measured via `time.perf_counter()` around the API call.
6. `raw_response` is the response `.model_dump()` (SDK → dict) minus any auth-header echoes.

**Rationale:**
AC-1 + AC-6 — prove the round-trip produces a valid `ToolCall` with populated telemetry for the simple case. Error mapping + retries layer on top in T-067/T-068.

**Acceptance Criteria:**
- [ ] `respx`-mocked happy response returns a `ToolCall` with correct `name`, `arguments`, `usage.input_tokens == response.usage.input_tokens`, `usage.output_tokens == response.usage.output_tokens`, `usage.latency_ms > 0`.
- [ ] Tool translation: an orchestrator `ToolDefinition(name="x", description="y", parameters={…})` appears in the outbound payload as `{"name": "x", "description": "y", "input_schema": {…}}` (not `"parameters"`).
- [ ] `system` prompt and `messages` are forwarded byte-identically.
- [ ] `raw_response` is a dict containing `id`, `stop_reason`, `model`, `usage`, `content`.
- [ ] Works for both `tool_choice={"type": "auto"}` path (don't hardcode `tool_choice={"type": "tool", …}`).

**Files to Modify/Create:**
- `src/app/core/llm_anthropic.py` — `chat_with_tools` body.
- `tests/modules/core/test_llm_anthropic_happy.py` — respx-mocked happy paths + tool-translation shape.

**Technical Notes:**
Anthropic returns `content` as a list of typed blocks; iterate and pick the first `type == "tool_use"`. If the SDK's Pydantic models don't serialize cleanly via `model_dump()`, fall back to `response.model_dump(mode="json")`. Use `respx.mock(base_url="https://api.anthropic.com")` — the SDK's default base.

---

### T-067: Error mapping at the provider boundary

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-066

**Description:**
Wrap `chat_with_tools`'s API call in a single `try/except` that maps SDK exceptions to orchestrator exceptions:
- `anthropic.APIStatusError` (4xx/5xx) → `ProviderError(detail, http_status, request_id, body)`.
- `anthropic.APIConnectionError` / `anthropic.APITimeoutError` → `ProviderError(detail="upstream transport failure", http_status=None, …)`.
- Model response with zero `tool_use` blocks → `PolicyError("policy selected no tool")`.
- Model response with >1 `tool_use` blocks → `PolicyError("policy selected multiple tools: […]")`.
- `anthropic.BadRequestError` with a message matching "invalid_tool_use" / "no tools were available" → `PolicyError(…)` (since the runtime terminates the run as `stop_reason=error` on either).

Extend `ProviderError` in `src/app/core/exceptions.py` with optional `http_status`, `request_id`, and `original_body` fields (parallel to `EngineError`) if not already present.

**Rationale:**
AC-4 + AC-5 — distinguish transient transport failures (retryable) from permanent policy errors (terminal), and surface Anthropic's `request-id` header for forensics in every failed trace entry.

**Acceptance Criteria:**
- [ ] 500 from Anthropic → `ProviderError(http_status=500)` with `request_id` populated.
- [ ] 401 from Anthropic → `ProviderError(http_status=401)` (not retried by T-068).
- [ ] Connection error → `ProviderError(http_status=None)`.
- [ ] Response with empty `content` → `PolicyError("policy selected no tool")`.
- [ ] Response with two `tool_use` blocks → `PolicyError` naming both tools.
- [ ] `stop_reason="max_tokens"` without a `tool_use` block → treated as "no tool selected" → `PolicyError` with an actionable hint ("raise `anthropic_max_tokens` or tighten the prompt").
- [ ] `ProviderError` gains `http_status`, `request_id`, `original_body` fields (nullable); existing code unaffected.

**Files to Modify/Create:**
- `src/app/core/llm_anthropic.py` — error mapping.
- `src/app/core/exceptions.py` — extend `ProviderError` signature.
- `tests/modules/core/test_llm_anthropic_errors.py` — respx-mocked error matrix.

**Technical Notes:**
Use `anthropic.APIStatusError` (the base class) — specific subclasses (`BadRequestError`, `AuthenticationError`, `RateLimitError`, `APIStatusError`) all inherit from it; catch broad then branch on `exc.status_code`. `request-id` lives on `exc.response.headers["request-id"]` (also `x-request-id` on some edges; check both).

---

### T-068: Bounded retry with backoff + jitter

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-067

**Description:**
Wrap the API call in a retry loop: up to 3 attempts total, exponential backoff 500 ms → 1 s → 4 s (capped), ±50 ms jitter. Retry ONLY:
- `anthropic.APIConnectionError`
- `anthropic.APITimeoutError`
- `APIStatusError` with `status_code == 429`
- `APIStatusError` with `status_code >= 500`

Do NOT retry 400 / 401 / 403. The final `Usage.latency_ms` is the cumulative wall-clock of all attempts so a retried call reflects its real cost. Every attempt logs at `WARNING` with `run_id` + `step_id` + `request_id` + `attempt_number`.

**Rationale:**
AC-4 — transient failures should not terminate runs. The retry ladder is deliberately conservative so one stuck provider never delays cancellation / shutdown for long.

**Acceptance Criteria:**
- [ ] Three failed attempts (respx: 3× 500) → `ProviderError` propagates; total backoff ≤ 6 s in the worst case.
- [ ] One failed attempt then success → `ToolCall` returned; `latency_ms` covers both attempts.
- [ ] 400 response → 1 attempt only, no retry.
- [ ] Jitter bounded to [-50ms, +50ms]; deterministic under a seeded random for tests.
- [ ] `WARNING` logs written on every retry with the expected fields bound.

**Files to Modify/Create:**
- `src/app/core/llm_anthropic.py` — retry wrapper.
- `tests/modules/core/test_llm_anthropic_retries.py` — respx invocation counting + timing bounds.

**Technical Notes:**
Keep retry purely in-process — do not reach for `tenacity` or any new dependency. A tiny `for attempt in range(3)` + `asyncio.sleep(...)` is enough. Jitter via `random.uniform(-0.05, 0.05)`; seed `random.Random(...)` inside tests for determinism.

---

### T-069: Factory wiring + API-key redaction

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-066

**Description:**
Flip `get_llm_provider(settings)` in `src/app/core/llm.py`: remove the `NotImplementedYet("anthropic-provider-wiring")` branch and return `AnthropicLLMProvider(settings)` when `settings.llm_provider == "anthropic"`. Ensure the API key NEVER appears in `raw_response`, logs, or errors — the `AsyncAnthropic` client handles auth via header; `raw_response` comes from parsing the response body only (no headers roundtripped).

**Rationale:**
AC-7 — once the factory branches to the real provider, the FEAT-002 runtime loop is using it. The stub path stays untouched for all current tests.

**Acceptance Criteria:**
- [ ] `get_llm_provider(Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-test"))` returns an `AnthropicLLMProvider` instance.
- [ ] `get_llm_provider(Settings(llm_provider="stub"))` continues to return a `StubLLMProvider`.
- [ ] Unknown provider still raises `ProviderError`.
- [ ] `raw_response` dicts do not contain the string `"sk-ant-"` — asserted by a redaction test.

**Files to Modify/Create:**
- `src/app/core/llm.py` — factory branch.
- `tests/modules/core/test_llm_anthropic_factory.py` — factory dispatch + redaction assertion.

**Technical Notes:**
If the SDK's response carries any header-derived fields in its serialized form, strip them in `_redact(response_dict)` before assigning to `raw_response`. Safer to whitelist keys (`{"id", "type", "role", "model", "stop_reason", "stop_sequence", "usage", "content"}`) than to blacklist.

---

## Integration

### T-070: Doctor — validate `ANTHROPIC_API_KEY` shape

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-063

**Description:**
Extend `app.doctor._check_llm_config` so when `llm_provider == "anthropic"` the check verifies the key is non-empty, is a reasonable length (>=20 chars), and starts with `sk-ant-`. Missing or malformed → `fail` with a clear message; present and well-formed → `ok`. Stub provider path unchanged.

**Rationale:**
AC-8 — operators need a fast, offline way to catch misconfiguration before starting a real run. Real 401 detection still requires a live call; that's a deliberate gap.

**Acceptance Criteria:**
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + no `ANTHROPIC_API_KEY` → exit 2, fail message names `ANTHROPIC_API_KEY`.
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + short/malformed key → exit 2, fail message says "does not look like an Anthropic key".
- [ ] `doctor` with `LLM_PROVIDER=anthropic` + `sk-ant-…`-prefixed key ≥ 20 chars → exit 0.
- [ ] `doctor` with `LLM_PROVIDER=stub` (no key) → exit 0 (unchanged).
- [ ] `tests/test_cli_doctor.py` extended with 3 parameterized cases covering the above.

**Files to Modify/Create:**
- `src/app/doctor.py` — tighten `_check_llm_config`.
- `tests/test_cli_doctor.py` — new parameterized cases.

**Technical Notes:**
Do NOT call the API from `doctor`. This is a shape check only. Document in the `fail` detail: "A live check would require a network call and is skipped; run `orchestrator run` to catch 401s."

---

## Testing

### T-071: Multi-turn tool-result threading test

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-066

**Description:**
Write a test that exercises the provider across two turns. Turn 1: provider sees a user message → returns `tool_use`. Turn 2: the caller constructs a `tool_result` message (mirroring what the runtime assembles) → provider sees the prior `tool_use` + new `tool_result` in `messages` → returns the next `tool_use`. Assert the outbound `messages` array in turn 2 contains the assistant's `tool_use` block AND the user's `tool_result` block in order.

**Rationale:**
AC-1 + the 4.1 "Tool-result message threading" item. This test is the safety net on the message-assembly contract — the runtime loop already passes messages through, but the shape of the `tool_use`/`tool_result` pair is provider-specific and easy to get wrong.

**Acceptance Criteria:**
- [ ] Two `respx` responses, each returning a different `tool_use` block.
- [ ] Test calls `chat_with_tools` twice, passing the prior `ToolCall` forward via a `messages` list assembled in the test (this mirrors what the runtime does).
- [ ] The 2nd outbound request body, parsed from respx call history, contains `[{"role": "user", "content": …}, {"role": "assistant", "content": [{"type": "tool_use", …}]}, {"role": "user", "content": [{"type": "tool_result", …}]}]` in that order.
- [ ] `tool_use.id` in turn 1's response matches `tool_result.tool_use_id` in turn 2's request — forensic correlation.

**Files to Modify/Create:**
- `tests/modules/core/test_llm_anthropic_threading.py`.

**Technical Notes:**
The runtime loop in FEAT-002 currently passes a single `{"role": "user", "content": prompt_context}` message per iteration (see `runtime._iterate`). This task's test does NOT change the runtime contract; it proves the provider threads whatever message list the caller gives it.

---

### T-072: End-to-end runtime driven by `AnthropicLLMProvider` (respx-mocked)

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-069, T-071

**Description:**
New `tests/integration/test_run_end_to_end_anthropic.py`: mirrors `test_run_end_to_end::test_linear_agent_completes_with_done_node` but with `get_llm_provider_dep` overridden to return a real `AnthropicLLMProvider` backed by `respx` fixtures that script a 3-tool-call sequence (`analyze_brief` → `draft_plan` → `review_plan` terminal). Asserts the same downstream shape as the stub e2e: 3 steps, 3 policy calls, merged memory, completed run, done_node stop reason, JSONL trace with ≥ 9 lines.

**Rationale:**
AC-3 — the runtime, webhook reconciliation, supervisor, trace store, and control plane are behavior-equivalent under either provider. This is the symmetric half of FEAT-002's composition-integrity test.

**Acceptance Criteria:**
- [ ] Test passes under the existing `integration_env` fixture with the Anthropic provider plugged in.
- [ ] Downstream row counts match the stub path: 1 Run, 3 Steps (all `completed`), 3 PolicyCalls.
- [ ] Every `PolicyCall.provider == "anthropic"`; every `PolicyCall.input_tokens > 0` (from the respx-provided usage blocks).
- [ ] `run.stop_reason == done_node` and `run.status == completed`.
- [ ] JSONL trace reads back via `JsonlTraceStore.open_run_stream` with no errors.

**Files to Modify/Create:**
- `tests/integration/test_run_end_to_end_anthropic.py`.
- Possibly extend `tests/integration/env.py` to accept a `policy` override (currently it constructs `StubLLMProvider(policy_script)` internally; the override lets this test inject the real provider).

**Technical Notes:**
The runtime adds a `terminate` tool to every tool list. The respx fixture for the 3rd turn should return the `review_plan` tool-use (the sample agent's terminal node) — `done_node` fires before `policy_terminated`. If the scripted Anthropic responses need to thread `tool_result` blocks, they don't — the runtime currently sends one user-content message per iteration, not a conversation. Keep the test focused on round-trip, not on conversation semantics (which T-071 covers).

---

### T-073: Settings validation tests

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-063

**Description:**
Parameterized tests in `tests/test_config.py` for the new validator. Cases:
1. `llm_provider=anthropic` + no key → `ValidationError`.
2. `llm_provider=anthropic` + empty-string key → `ValidationError`.
3. `llm_provider=anthropic` + valid key → succeeds.
4. `llm_provider=stub` + no key → succeeds.
5. `llm_provider=anthropic` with no explicit `llm_model` → defaults to `claude-opus-4-7`.
6. `anthropic_max_tokens=0` → `ValidationError`.
7. `anthropic_timeout_seconds=-1` → `ValidationError`.

**Rationale:**
AC-2 — catches configuration regressions before they surface as 500s at runtime.

**Acceptance Criteria:**
- [ ] 7 parameterized cases as listed.
- [ ] Each asserts a specific error message substring for the `ValidationError` cases.
- [ ] No reliance on `monkeypatch` for env vars within a single test — construct `Settings(...)` directly to keep cases isolated.

**Files to Modify/Create:**
- `tests/test_config.py` — new `TestAnthropicValidation` class.

**Technical Notes:**
`Settings()` reads from `os.environ` by default; pass explicit kwargs in tests to avoid env-var leakage from the session-scoped fixtures. Use `pydantic.ValidationError`, not `pytest.raises(Exception)`.

---

### T-074: Secret-never-leaks test

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-069

**Description:**
A single test that exercises the full provider → PolicyCall → JSONL trace chain with a synthetic API key (`sk-ant-SECRET_MARKER_…`), then reads back the trace file AND `PolicyCall.raw_response` AND the caplog output, and asserts the string `SECRET_MARKER` never appears in any of them.

**Rationale:**
Constraint from §10 of the brief — API keys MUST NOT leak to traces or logs. This is a guardrail, not a behavior test; easy to write once and cheap to keep forever.

**Acceptance Criteria:**
- [ ] Test sets `ANTHROPIC_API_KEY=sk-ant-SECRET_MARKER_test_only`.
- [ ] Uses `respx` to mock the API response (no real call).
- [ ] Captures the `PolicyCall` row from DB.
- [ ] Reads the resulting JSONL trace file.
- [ ] Captures logs via `caplog.at_level(logging.DEBUG)` around the provider call.
- [ ] Asserts `"SECRET_MARKER"` is not a substring of any of those three artifacts.

**Files to Modify/Create:**
- `tests/integration/test_anthropic_secret_redaction.py`.

**Technical Notes:**
Use the existing `integration_env` fixture but with a tiny custom `policy_script=[("analyze_brief", {"brief": "hi"})]` and a respx fixture that returns one `tool_use`. One iteration is enough to produce one `PolicyCall` + one JSONL line per kind.

---

### T-075: Live contract test

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-069

**Description:**
Create `tests/contract/test_anthropic_provider_contract.py` marked `@pytest.mark.live`. Skipped unless `--run-live` is passed AND `ANTHROPIC_API_KEY` is set. Builds an `AnthropicLLMProvider` with a single `echo` tool (takes `{"text": "hello"}` and returns it), calls `chat_with_tools` with a prompt asking the model to echo "hello", and asserts the returned `ToolCall` has `name == "echo"`, `arguments == {"text": "hello"}`, `usage.input_tokens > 0`, `raw_response` non-null.

**Rationale:**
AC-1 — a real API round-trip that proves the whole path works against the actual model. Off by default, cheap to run manually, ready to wire into a scheduled CI job later.

**Acceptance Criteria:**
- [ ] Test is skipped when `ANTHROPIC_API_KEY` is absent OR `--run-live` is not passed.
- [ ] With both conditions met, hits `https://api.anthropic.com/v1/messages` exactly once.
- [ ] Asserts tool name, arguments, and token counts.
- [ ] Test completes in under 10 s on a healthy connection.

**Files to Modify/Create:**
- `tests/contract/test_anthropic_provider_contract.py`.

**Technical Notes:**
Use `pytest.skipif` on the env var absence; `@pytest.mark.live` already handles the `--run-live` gate via `tests/conftest.py`'s existing collection-modify hook. Keep the prompt deterministic ("You must call the echo tool with text='hello'. Do not call any other tool.") — the assertion is on the tool-call shape, not on model cleverness.

---

## Polish

### T-076: Documentation updates

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-069, T-070, T-075

**Description:**
Update the four docs listed below to reflect the real provider:

1. **`CLAUDE.md`** — extend the "Runtime Loop" section with a short "LLM Providers" subsection listing supported providers (stub, anthropic) and the composition-root swap pattern. Note: no new pattern / anti-pattern entries; the existing ones cover it.
2. **`docs/ARCHITECTURE.md`** — in the "Runtime Loop Components" section, add a bullet for `AnthropicLLMProvider` (tool translation, retry policy, error mapping). Update the AI Task Generation Notes to mention the provider seam. Changelog entry dated 2026-04-18 FEAT-003.
3. **`README.md`** — add a short "Using Anthropic" subsection under "Getting Started" right before "First Run": `export ANTHROPIC_API_KEY=sk-ant-...` + `export LLM_PROVIDER=anthropic` + run `orchestrator doctor`. Keep under the 150-line cap.
4. **`docs/work-items/FEAT-003-anthropic-provider.md`** — flip Status to `Completed` once the feature ships.

`docs/data-model.md` and `docs/api-spec.md` are untouched (no contract changes) — no changelog entries needed on those.

**Rationale:**
Documentation Maintenance Discipline in CLAUDE.md. Every behavior-changing PR updates the relevant docs in the same PR.

**Acceptance Criteria:**
- [ ] CLAUDE.md has an "LLM Providers" subsection naming `stub` and `anthropic`.
- [ ] ARCHITECTURE.md has a new bullet under Runtime Loop Components and a 2026-04-18 FEAT-003 changelog entry.
- [ ] README.md's "Using Anthropic" block is ≤ 10 lines; total file ≤ 150 lines.
- [ ] FEAT-003 brief's Status field updated.
- [ ] No contradictions between docs and shipped code.

**Files to Modify/Create:**
- `CLAUDE.md`.
- `docs/ARCHITECTURE.md`.
- `README.md`.
- `docs/work-items/FEAT-003-anthropic-provider.md`.

**Technical Notes:**
Mirror FEAT-002's style: one-line changelog entries dated and FEAT-referenced; no marketing prose. Readers scan these for deltas.

---

## Summary

### Task Count by Type

| Type | Count |
|------|-------|
| Backend | 5 (T-063, T-065, T-066, T-067, T-068, T-069) — *note T-063 is Foundation-flavored but Backend-typed* |
| Testing | 6 (T-064, T-071, T-072, T-073, T-074, T-075) |
| Documentation | 1 (T-076) |
| **Total** | **13** (T-063 through T-076) |

### Complexity Distribution

| Complexity | Count | Tasks |
|------------|-------|-------|
| S | 8 | T-063, T-064, T-065, T-069, T-070, T-073, T-074, T-075 |
| M | 4 | T-066, T-067, T-068, T-071 |
| L | 1 | T-072 |
| XL | 0 | — |

### Critical Path

`T-063 → T-065 → T-066 → T-067 → T-068 → T-069 → T-072 → T-076`

Eight tasks in the longest chain; total wall-clock well under FEAT-002's 32-task feature. This reflects the narrow surface area: provider implementation in layers, one e2e test to prove it plugs in, docs. Everything else (T-064 guardrail, T-070 doctor, T-071/T-073/T-074/T-075 test coverage) branches off the main chain and can land in parallel PRs once their individual dependencies are met.

### Risks / Open Questions

- **Anthropic SDK version drift.** `anthropic>=0.40,<1` pins a single major. When the SDK hits 1.0 the tool-calling response shape may shift. Mitigation: T-066's tool-translation + T-067's error-mapping tests are the canaries. A 1.x upgrade would be a dedicated IMP, not silent.
- **`tool_use.id` correlation.** The runtime today does not thread `tool_use.id`/`tool_result.tool_use_id` across turns because it sends one message per iteration (no conversation). T-071 tests the provider's ability to thread, but if a future feature wants real multi-turn conversations the runtime will need to plumb this through — not in scope here, flagged for future.
- **`stop_reason="max_tokens"` without `tool_use`.** We treat it as `PolicyError` (no tool selected). A sophisticated policy might want to retry with a larger `max_tokens` automatically; we explicitly excluded that (§4.2). Operators raise `ANTHROPIC_MAX_TOKENS` and restart the run.
- **Retry storms under sustained 429.** Three attempts with 500 ms → 4 s backoff means a single stuck provider holds one run-loop coroutine for up to ~6 s before failing. Acceptable for v1; if this becomes a pain, a circuit breaker is a later IMP, not part of this feature.
- **Model default (`claude-opus-4-7`).** Chosen because it's the latest Opus at spec time and matches the `most capable Claude models` direction from the environment context. Downstream features (lifecycle agent, FEAT-004) may want Sonnet for cost reasons and will set `LLM_MODEL` per-deployment; the default is opinionated but overridable.
