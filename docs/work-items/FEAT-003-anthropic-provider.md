# Feature Brief: FEAT-003 — Real LLM Provider (Anthropic)

> **Purpose**: Swap the `StubLLMProvider` for a real Anthropic-backed policy at the `core/llm.py` seam FEAT-002 already proved out. The runtime loop, tool-calling contract, stop conditions, traces, and control plane stay exactly as they are — only the composition root changes. This is the feature that makes the orchestrator useful for real decisions without touching any other part of the architecture.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-003 |
| **Name** | Real LLM Provider (Anthropic) |
| **Target Version** | v0.3.0 |
| **Status** | Completed |
| **Priority** | High |
| **Requested By** | Tech Lead (`ai@techer.com.br`) |
| **Date Created** | 2026-04-18 |

---

## 2. User Story

**As a** solo tech lead driving feature delivery (see `docs/personas/primary-user.md`), **I want to** set `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=...` in `.env`, point an agent at a non-trivial task, and watch the orchestrator drive it through real Claude tool calls — with token counts, latencies, and raw responses captured in every `PolicyCall` — **so that** the composition-integrity claim (AD-3) is demonstrated in both directions: the stub degrades to a deterministic pipeline *and* the real LLM plugs into the same contract without any other code path changing.

---

## 3. Goal

`Settings(llm_provider="anthropic")` yields a working `AnthropicLLMProvider` implementing `LLMProvider.chat_with_tools(...)` — one Anthropic Messages API call per policy invocation, native tool-calling (no free-form JSON parsing), typed errors wrapped in `ProviderError`, bounded retries on transient failures, and real token/latency telemetry on every returned `Usage`. Every existing FEAT-002 test stays green; a new opt-in `@pytest.mark.live` contract test proves the round-trip against the real API when a key is present.

---

## 4. Feature Scope

### 4.1 Included

- **`AnthropicLLMProvider`** (`src/app/core/llm_anthropic.py`): implements the `LLMProvider` Protocol. One `chat_with_tools` call maps to one `messages.create(...)` call on the Anthropic SDK (async client). Translates the orchestrator's `ToolDefinition` list into Anthropic's `tools=[...]` shape, sets `tool_choice="auto"`, reads back the model's `tool_use` block, and returns a `ToolCall` with `name`, `arguments`, populated `Usage(input_tokens, output_tokens, latency_ms)`, and the raw response dict.
- **Factory wiring** in `src/app/core/llm.py`: `get_llm_provider(settings)` switches on `settings.llm_provider == "anthropic"` and returns an `AnthropicLLMProvider(settings)` instance instead of `raise NotImplementedYet`. The stub path is unchanged.
- **Settings surface**:
  - `anthropic_api_key: SecretStr` becomes **required** when `llm_provider == "anthropic"` — enforced by a `model_validator` on `Settings` (fail-fast at process start, surfaced by `orchestrator doctor`).
  - `llm_model: str` default `"claude-opus-4-7"` when `llm_provider == "anthropic"`; validated to be non-empty. Overridable per-run only via future `--model` CLI flag (**not** in this feature's scope — documented stub only).
  - New `anthropic_max_tokens: int = 4096` — token ceiling per Anthropic call (separate from the run-level `max_tokens` budget; one is a per-call cap, the other is a cumulative run budget).
  - New `anthropic_timeout_seconds: int = 60` — per-request HTTP timeout.
- **Tool-result message threading**: multi-turn tool calling. After a node dispatch + webhook, the next `chat_with_tools` call includes prior turns as `assistant` (with `tool_use` block) and `user` (with `tool_result` block) messages so Claude sees the node's output as context for the next decision. Message assembly lives in the provider (not the runtime loop) behind the `messages: Sequence[Mapping[str, Any]]` parameter the runtime already passes.
- **System prompt pass-through**: the existing `system` parameter on `chat_with_tools` is forwarded as Anthropic's top-level `system` argument. No prompt templating in this feature — prompts come from the runtime/agent contract FEAT-002 already uses.
- **Error mapping** at the provider boundary:
  - `anthropic.APIStatusError` (4xx/5xx) → `ProviderError` carrying the status code, response body, and request id.
  - `anthropic.APIConnectionError` / `anthropic.APITimeoutError` → `ProviderError` with `engine_http_status=None` semantics (by analogy to `EngineError`).
  - `anthropic.BadRequestError` with "invalid_tool_use" or "no tools were available" → `PolicyError` (the runtime terminates the run with `stop_reason=error`, per existing CLAUDE.md policy).
  - Model returns zero `tool_use` blocks or >1 `tool_use` blocks → `PolicyError` with a clear message naming the offense (per the existing service-layer contract for `PolicyCall`).
- **Bounded retry** for genuinely transient failures (`APIConnectionError`, `APITimeoutError`, 429, 5xx): max 3 attempts, exponential backoff starting at 500 ms (+jitter), capped at 4 s. Non-transient errors (400, 401, 403) do not retry. Retries are observable: every attempt's `Usage.latency_ms` accumulates into the final `Usage` so a retried call reflects its real wall-clock cost.
- **Live contract test** (`tests/contract/test_anthropic_provider_contract.py`, `@pytest.mark.live`): skipped unless `ANTHROPIC_API_KEY` is set AND `--run-live` is passed. Uses a minimal 1-node YAML fixture and asserts a real Claude call returns a `ToolCall` with `name == "echo"`, `Usage.input_tokens > 0`, and `raw_response is not None`. One test only — this is a contract check, not a quality benchmark.
- **Doctor check**: extend `app.doctor` so the existing LLM check actually verifies `ANTHROPIC_API_KEY` is present *and* well-formed (`sk-ant-...` prefix, non-empty) when `llm_provider=anthropic`. Today it only checks presence.
- **Documentation updates** (data-model no-op; api-spec no-op; CLAUDE.md + ARCHITECTURE.md + README updated for provider wiring + env var story). Changelog entries per Documentation Maintenance Discipline.

### 4.2 Excluded

- **OpenAI, Bedrock, Vertex, Ollama, or any non-Anthropic provider.** The factory stays dispatch-by-string and can grow to multi-provider later; shipping one is enough to prove the seam works.
- **Streaming responses.** Tool-calling does not need streaming; all callers (runtime loop + contract test) want the final `tool_use` block. Streaming lands only if/when `runs trace --follow` (FEAT-004) benefits from inline token streaming.
- **Anthropic-specific features beyond tool calling**: prompt caching, extended thinking, vision, file uploads, MCP tools, computer-use, batch API. All interesting; none required to drive tool-calling agents.
- **Provider-specific model configuration** (temperature, top_p, custom stop sequences, tool_choice overrides). The defaults Anthropic picks are fine for v1; we ship the knobs when a real use case demands them.
- **Prompt templating / context window management / dynamic message pruning.** FEAT-002 already defines the prompt-context shape; this feature forwards it verbatim. Context window management lands only when a real agent hits the ceiling.
- **Retry policy as a user-configurable option.** The 3-attempt/500 ms-to-4 s ladder is hardcoded until operational data says otherwise.
- **Cost tracking / budget-in-USD / rate-limit detection beyond what Anthropic's error codes already surface.** The `max_tokens` budget already exists at the run level; financial guards are a separate feature.
- **Hot-swapping providers mid-run.** A run's provider is fixed at start. Switching providers means a new run.
- **Any change to the runtime loop, engine client, trace store, supervisor, webhook receiver, or control-plane endpoints.** If this feature forces one, the design is wrong. The seam is `core/llm.py` only.

---

## 5. Acceptance Criteria

- **AC-1**: `Settings(llm_provider="anthropic")` + valid `ANTHROPIC_API_KEY` produces a usable provider: a direct `chat_with_tools` call with one tool and a trivial message returns a `ToolCall` whose `name` is the tool's name and whose `usage.input_tokens > 0`. Verified by the live contract test.
- **AC-2**: `Settings(llm_provider="anthropic")` without `ANTHROPIC_API_KEY` raises a clear `ValidationError` at settings-load time (not on first API call) — asserted by a unit test and surfaced by `orchestrator doctor`.
- **AC-3**: The FEAT-002 runtime loop, driven by `AnthropicLLMProvider` (mocked with `respx` against `https://api.anthropic.com/v1/messages`), completes a 2-step run with a real tool-use response shape and produces identical downstream rows (`Run`, `Step`, `PolicyCall`) as the stub path — same schema, same envelopes, same stop-reason mapping.
- **AC-4**: Anthropic 5xx / `APIConnectionError` / `APITimeoutError` / 429 responses are retried up to 3 times with backoff; persistent failure surfaces as `ProviderError` and terminates the run with `stop_reason=error`. Non-transient 4xx (400, 401, 403) does not retry. Asserted by `respx` tests that count invocations.
- **AC-5**: A model response with zero `tool_use` blocks raises `PolicyError("policy selected no tool")` and terminates the run with `stop_reason=error` — no fabrication, no "best guess", matching the existing CLAUDE.md rule. Two `tool_use` blocks raises the same error with a different message.
- **AC-6**: `PolicyCall.raw_response` contains the Anthropic response dict (redacted of sensitive fields if any surface; at minimum includes `id`, `stop_reason`, `model`, `usage`, and the `content` blocks). `PolicyCall.input_tokens` and `output_tokens` match `raw_response.usage`.
- **AC-7**: The stub provider path is unchanged — every existing FEAT-002 unit and integration test stays green with no modification. Proven by running `uv run pytest` unchanged after this feature lands.
- **AC-8**: `orchestrator doctor` distinguishes three states for the LLM check: stub (`ok`), anthropic + valid-looking key (`ok`), anthropic + missing-or-malformed key (`fail`). Asserted by `tests/test_cli_doctor.py` parameterized cases.
- **AC-9**: Adapter-thin rule is preserved: the `anthropic` SDK is imported **only** in `src/app/core/llm_anthropic.py` and `src/app/core/llm.py`'s factory branch. Enforced by extending `tests/test_adapters_are_thin.py` to add `anthropic` to the forbidden-import list for the whole `src/app/modules/` tree.
- **AC-10**: `uv run pyright` and `uv run ruff check .` stay clean. Full test suite green; no test skipped except the `live`-marked suite (which remains off by default).

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|-----------------|--------------------|
| `PolicyCall` | Populated with real Anthropic telemetry each iteration. | `provider="anthropic"`, `model=<settings.llm_model>`, `input_tokens`/`output_tokens`/`latency_ms` from the API response. `raw_response` is the Anthropic JSON dict (minus the API key header). Append-only, unchanged from FEAT-002. |
| `Run.final_state` | On `stop_reason=error`, includes the provider error type + HTTP status + Anthropic request id when available — aids forensics when a run dies mid-conversation. | Additive to FEAT-002's `final_state` shape; not a schema change. |

**New entities required:** None. `AgentDefinition`, `Run`, `Step`, `PolicyCall`, `WebhookEvent`, `RunMemory` are all unchanged. The feature is a composition-root swap.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| *(none)* | — | — | No endpoint contract changes. Every control-plane endpoint returns the same shapes; only the provider behind the scenes differs. |

**New endpoints required:** None.

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| CLI (`orchestrator`) | Unchanged in surface | Output shapes identical. `orchestrator doctor` gets a tighter `ANTHROPIC_API_KEY` validation (still one bullet in the output). |

**New screens required:** None.

---

## 9. Edge Cases

- **API key absent at process start with `llm_provider=anthropic`**: fail at `Settings()` construction, not at first request. The CLI and the FastAPI app should both refuse to start with a clear error message naming the missing var.
- **API key invalid (Anthropic returns 401)**: `ProviderError(http_status=401)`, run terminates `error`, no retry. The `doctor` check cannot catch this without a live API call — `doctor` only validates shape; a real `orchestrator run` surfaces the 401.
- **Anthropic returns `stop_reason="max_tokens"`**: the model hit `anthropic_max_tokens` before emitting a `tool_use` block. Treat as `PolicyError` (no tool selected). Document that raising `anthropic_max_tokens` is the operator's fix.
- **Rate limit (429) mid-run**: retried up to 3 times per policy call. If the run exhausts retries on a single call, the run ends `error`. Retries do not reset the run-level `max_tokens` budget (tokens consumed by retries count — intentional; prevents retry storms from silently inflating the budget).
- **Network partition during a tool-result turn**: the `messages` list always includes the prior `tool_use` + `tool_result` pair, so a retried call resumes from the same turn boundary. The provider never discards history in-flight.
- **Model returns a tool not in the current available-tools list**: this is already a `PolicyError` in the runtime (see FEAT-002). Nothing new here, but the provider MUST pass the tools exactly as received from the runtime — no silent re-ordering, no "helpful" additions like the built-in `terminate` tool (that's already appended by `tools.build_tools`).
- **`raw_response` size**: Anthropic responses with large `content` can be several KB. Persist as JSONB without truncation; trace-file writers get the same. If this becomes a cost problem later, a separate feature adds retention policy.
- **Concurrent runs against the same provider instance**: Anthropic's async client is safe for concurrent `create(...)` calls; one `AnthropicLLMProvider` instance is shared across all runs in the process. Verified by a concurrency smoke test in the live-contract suite (skipped by default).
- **Token counts missing from response** (some model variants or error paths omit `usage`): default to `0` for `input_tokens` / `output_tokens` and log a `WARNING` — the `PolicyCall` row still persists, never dropped for missing telemetry.

---

## 10. Constraints

- MUST NOT introduce any non-Anthropic SDK. The abstraction remains `LLMProvider`; a second provider ships only in a later feature.
- MUST NOT change the signature of `LLMProvider.chat_with_tools` or any of `ToolDefinition` / `Usage` / `ToolCall`. If Anthropic's shape requires a new field, the contract grows — propose it in a separate IMP before this feature lands.
- MUST respect the thin-adapter rule: the `anthropic` import is confined to `core/llm_anthropic.py` (+ a single branch in `core/llm.py`). Enforced by the adapter check extended per AC-9.
- MUST NOT leak the API key into logs, traces, or `raw_response`. A unit test reads a trace file and asserts `ANTHROPIC_API_KEY`'s value never appears.
- MUST respect CLAUDE.md's "never swallow silently" rule: every retry, every error, every unexpected response shape logs at `WARNING`+ with `run_id` + `step_id` bound.
- MUST NOT add a real-provider dependency to the core test path. The default `llm_provider` in CI stays `stub`; contract tests are opt-in via `--run-live`.
- MUST update `CLAUDE.md`, `docs/ARCHITECTURE.md`, and `README.md` in the same PR (or sibling PRs in the stack) when provider behavior is documented. `docs/data-model.md` and `docs/api-spec.md` are untouched (no entity / contract changes).
- Work lands as a small stack of PRs: provider class → settings validation → factory wiring → doctor extension → docs. Each PR green on `pyright`, `ruff`, and the full pytest suite. The live contract test ships last, guarded by the existing `--run-live` flag.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-002 built and proved the `LLMProvider` seam without a real provider. FEAT-003 is the symmetric half of the composition-integrity claim: stub in → deterministic pipeline out; Anthropic in → agent-driven decisions out. Until this feature ships, every claim that the orchestrator "drives feature delivery with an LLM policy" is aspirational — the stub-only loop can't make novel decisions against unseen state. Shipping this unblocks the lifecycle agent (FEAT-004) and the self-hosted delivery proof (FEAT-005), which are the stakeholder's primary success metric.

**Impact if delayed:** FEAT-004 (concrete ia-framework lifecycle agent) cannot meaningfully run without a real policy — a lifecycle agent scripted with a stub policy is a demo, not a product. The self-hosted delivery metric ("orchestrator ships its own features") is blocked by both FEAT-003 and FEAT-004 together, but FEAT-003 is the shorter path and the lower-risk one (the surface area is narrow and well-defined).

**Dependencies on this feature:** FEAT-004 (lifecycle agent nodes + real policy-driven routing), FEAT-005 (self-hosted feature delivery proof). Any future observability work that depends on real token-count and latency data also rests on this feature.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` — the tech lead who already uses Claude Code manually and wants the loop to run the same way autonomously. |
| **Stakeholder Scope Item** | "A **policy interface** with a minimum viable contract" — FEAT-002 built the interface; FEAT-003 plugs in the first real implementation. Also directly supports "Observability hooks" via populated token/latency/raw-response fields on every `PolicyCall`. |
| **Success Metric** | "Composition integrity" (full-circle: stub degrades to pipeline, real provider plugs into the same contract). "Policy traceability" becomes actionable (real token counts + latencies to inspect). Unblocks "Self-hosted feature delivery" and "Time-to-task-list from brief". |
| **Related Work Items** | Predecessor: FEAT-002 (runtime loop — complete). Successors: FEAT-004 (lifecycle agent + concrete nodes), FEAT-005 (self-hosted feature delivery proof). Adjacent: FEAT-006-ish (additional providers — OpenAI, Bedrock — not yet scoped). |
