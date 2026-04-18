# Implementation Plan: T-076 — Documentation updates

## Task Reference
- **Task ID:** T-076
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-069, T-070, T-075

## Overview
Surgical doc updates reflecting the real Anthropic provider: an "LLM Providers" subsection in CLAUDE.md, a Runtime-Loop-Components bullet + changelog entry in ARCHITECTURE.md, a short "Using Anthropic" block in README.md, and the brief's Status flipped to `Completed`. `data-model.md` and `api-spec.md` are untouched (no contract changes).

## Steps

### 1. Modify `CLAUDE.md`
- Under the existing "## Runtime Loop" section, append:
  ```markdown
  ### LLM Providers

  - **stub** (default, CI). `StubLLMProvider` replays a scripted sequence of tool calls deterministically; no network.
  - **anthropic** (opt-in). `AnthropicLLMProvider` calls the Anthropic Messages API via the native tool-calling path. Requires `ANTHROPIC_API_KEY`. Model defaults to `claude-opus-4-7`; override with `LLM_MODEL`. Per-call token ceiling via `ANTHROPIC_MAX_TOKENS`, timeout via `ANTHROPIC_TIMEOUT_SECONDS`.

  Provider selection is a composition-root swap at `app.core.llm.get_llm_provider` — no other module touches the SDK. Adding a third provider follows the same pattern: new module under `src/app/core/llm_<name>.py`, new branch in the factory, extend the adapter-thin quarantine test.
  ```
- No new Pattern / Anti-pattern entries — the existing ones cover the provider seam implicitly.

### 2. Modify `docs/ARCHITECTURE.md`
- Under "### Runtime Loop Components (FEAT-002)", append a new bullet:
  - **`AnthropicLLMProvider`** (FEAT-003) — real LLM policy behind the `LLMProvider` protocol. Translates `ToolDefinition` → Anthropic tool schema, parses `tool_use` blocks back into `ToolCall`s, maps SDK errors to `ProviderError` / `PolicyError`, retries transient failures (5xx / 429 / connection / timeout) with capped exponential backoff + jitter. API key is scoped to the SDK's auth header; never leaks into `raw_response`, traces, or logs.
- Add a 2026-04-18 FEAT-003 changelog entry at the bottom:
  - `2026-04-18 — FEAT-003 — Added AnthropicLLMProvider to the Runtime Loop Components section; documented provider-swap pattern. No contract changes.`

### 3. Modify `README.md`
- Add a new subsection between "### Running" and "### First Run":
  ```markdown
  ### Using Anthropic

  The default provider is a deterministic stub — no network, no cost. To drive
  runs with a real Claude policy:

  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...
  export LLM_PROVIDER=anthropic
  uv run orchestrator doctor   # validates key shape; no API call
  ```

  Optional knobs: `LLM_MODEL` (default `claude-opus-4-7`), `ANTHROPIC_MAX_TOKENS`
  (default 4096), `ANTHROPIC_TIMEOUT_SECONDS` (default 60). A live contract test
  is available under `uv run pytest --run-live tests/contract/`.
  ```
- Verify `wc -l README.md` stays ≤ 150.

### 4. Modify `docs/work-items/FEAT-003-anthropic-provider.md`
- Change the Status field in the Identity table from `Not Started` to `Completed`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `CLAUDE.md` | Modify | Add "LLM Providers" subsection. |
| `docs/ARCHITECTURE.md` | Modify | Add AnthropicLLMProvider bullet + changelog entry. |
| `README.md` | Modify | Add "Using Anthropic" block. |
| `docs/work-items/FEAT-003-anthropic-provider.md` | Modify | Status → Completed. |

## Edge Cases & Risks
- Docs drift is inevitable unless verified against code. Before marking the task done, re-read each modified doc and cross-check every claim against the shipped code (e.g., default model name, env var names, retry ladder).
- Changelog entries are one line — don't editorialize.
- If the README crosses 150 lines, trim the Project Layout block or the Tests block (those have the most room to cut).

## Acceptance Verification
- [ ] CLAUDE.md contains an "LLM Providers" subsection listing `stub` and `anthropic`.
- [ ] ARCHITECTURE.md has a new bullet under Runtime Loop Components and a 2026-04-18 FEAT-003 changelog entry.
- [ ] README.md's "Using Anthropic" block exists; total file ≤ 150 lines.
- [ ] FEAT-003 brief's Status field is `Completed`.
- [ ] No doc claims a behavior the shipped code doesn't have (manual walk-through).
