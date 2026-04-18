# Implementation Plan: T-007 — LLM abstraction + stub provider

## Task Reference
- **Task ID:** T-007
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Foundation for AD-3 composition-integrity ("remove the LLM → deterministic pipeline still runs"). Keeps provider SDKs out of service code per `adrs/ai/llm-abstraction-python.md`.

## Overview
Define the minimum `LLMProvider` Protocol needed by policy calls (tool-calling shape), implement a deterministic `StubLLMProvider`, and expose a factory that dispatches on `Settings.llm_provider`. v1 only wires `"stub"`; `"anthropic"` is a declared branch that raises `NotImplementedYet`.

## Implementation Steps

### Step 1: Public types
**File:** `src/app/core/llm.py`
**Action:** Modify

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema

@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    latency_ms: int

@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    usage: Usage
    raw_response: dict[str, Any] | None   # may be omitted by stubs
```

These are the only types service code touches — provider-specific types never leak.

### Step 2: Protocol
**File:** `src/app/core/llm.py`
**Action:** Modify

```python
class LLMProvider(Protocol):
    name: str
    model: str

    async def chat_with_tools(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolDefinition],
    ) -> ToolCall: ...
```

One method for v1. The runtime feature (FEAT-002) may add `chat_text` for agent definitions that need free-form prompts outside policy decisions. Keep v1 surface minimal.

### Step 3: `StubLLMProvider`
**File:** `src/app/core/llm.py`
**Action:** Modify

Constructor accepts a `script: Sequence[ScriptedCall]` where `ScriptedCall = tuple[str, dict[str, Any]] | Callable[[Sequence[ToolDefinition]], tuple[str, dict[str, Any]]]`. On each `chat_with_tools` call:
- If the script has a tuple at the current index, return `ToolCall(name=tuple[0], arguments=tuple[1], usage=Usage(0,0,0), raw_response=None)`.
- If it's a callable, invoke it with the available tools — lets tests write "pick the first tool" policies without hardcoding tool names.
- If the script is exhausted, raise `ProviderError("stub-policy-exhausted")`.

Validate in `chat_with_tools` that the scripted tool name is in the `tools` list. If not, raise `ProviderError("stub-tool-not-available")` — this catches test-bug miswrites early and mirrors the production invariant from `policy-via-tool-calling.md`.

Default script helper: `StubLLMProvider.pick_first_available()` returning a `StubLLMProvider([lambda tools: (tools[0].name, {})] * MANY)` for AD-3's degradation test.

### Step 4: Factory
**File:** `src/app/core/llm.py`
**Action:** Modify

```python
def get_llm_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "stub":
            return StubLLMProvider(script=[])  # tests override via DI
        case "anthropic":
            raise NotImplementedYet("anthropic-provider-wiring")  # lands in FEAT-003 or similar
```

Do **not** `import anthropic` at module top. When the anthropic branch is implemented later, import inside the `case` block or via a submodule.

### Step 5: FastAPI dependency
**File:** `src/app/core/dependencies.py`
**Action:** Modify

Add `def get_llm_provider_dep(settings: Annotated[Settings, Depends(get_settings_dep)]) -> LLMProvider: return get_llm_provider(settings)`. Tests override this to inject a scripted stub.

### Step 6: Tests
**File:** `tests/core/test_llm_stub.py`
**Action:** Create

- **Scripted tuple:** stub configured with `[("do_x", {"k": 1})]`; one call returns that ToolCall; second call raises `ProviderError("stub-policy-exhausted")`.
- **Callable entry:** stub configured with a lambda that inspects `tools` and returns the first; assert the returned `name` matches the first provided tool.
- **Tool-not-in-list:** stub scripted to return `"missing_tool"` with a tool list that doesn't contain it → `ProviderError("stub-tool-not-available")`.
- **No SDK imported:** `import sys; assert "anthropic" not in sys.modules` after importing `app.core.llm` from a fresh interpreter.
- **`pick_first_available` helper:** invoked against a 3-tool list, returns the first on each of 3 calls.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/core/llm.py` | Modify | Types, Protocol, stub, factory |
| `src/app/core/dependencies.py` | Modify | `get_llm_provider_dep` |
| `tests/core/test_llm_stub.py` | Create | Scripted + callable + failure paths + import check |

## Edge Cases & Risks

- **Async Protocol methods.** Pyright strict is picky about Protocol method bodies — use `...` (ellipsis), not `pass`. Any implementation must be `async def`.
- **Scripted entries drift during test evolution.** When a new node/tool is added later, stub scripts in older tests may still reference removed tool names. The `stub-tool-not-available` check surfaces this as a real test failure rather than a silent wrong-path.
- **No raw response in stub.** `PolicyCall.raw_response` is nullable per the data model — the stub sets it to `None`, production paths populate it. Leave the field in the `ToolCall` dataclass so service code doesn't branch on provider.
- **Thread safety.** `StubLLMProvider` uses a mutable counter. Tests that share a single instance across tasks will race. Document: one stub instance per run in tests; the DI override builds a fresh one each time.

## Acceptance Verification

- [ ] **Protocol exists:** `isinstance(StubLLMProvider([]), LLMProvider)` (structural check via `runtime_checkable` decorator OR `typing.cast` smoke).
- [ ] **Scripted path:** test green.
- [ ] **Exhaustion raises ProviderError:** test green.
- [ ] **Tool-gating enforced:** test green — mirrors prod invariant.
- [ ] **No provider SDK imported at module load:** `"anthropic" not in sys.modules` test green.
- [ ] **`get_llm_provider` dispatches:** `"stub"` → `StubLLMProvider`; `"anthropic"` → `NotImplementedYet`.
