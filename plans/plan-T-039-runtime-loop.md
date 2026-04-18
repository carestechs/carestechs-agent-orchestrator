# Implementation Plan: T-039 — Runtime loop implementation

## Task Reference
- **Task ID:** T-039
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-032, T-033, T-034, T-035, T-036, T-037, T-038

## Overview
Replace the FEAT-001 stub with the real loop. Each iteration opens its own session, evaluates stop conditions, calls the policy, persists `PolicyCall`, dispatches a `Step`, awaits webhook-driven wake-up, then loops. This is the AD-3 "remove the LLM → pipeline still runs" seam made real.

## Steps

### 1. Create `src/app/modules/ai/runtime_helpers.py`
Small pure helpers — kept separate so `runtime.py` reads in one screen:
- `def merge_memory(current: dict, node_result: dict | None) -> dict`: deep-merge that overwrites on leaves (documented in CLAUDE.md as the memory-update rule).
- `def build_prompt_context(run: Run, memory: RunMemory, last_step: Step | None) -> dict`: assembles the structured context the policy receives.
- `def tool_call_to_node(tool_call: ToolCall, agent: AgentDefinition) -> str | None`: returns the node name if `tool_call.name` is a real node; `None` if `terminate` or unknown.
- `def validate_tool_arguments(tool_call: ToolCall, agent: AgentDefinition) -> None`: raises `PolicyError` if the node's `input_schema` rejects the arguments (use `jsonschema` — already a transitive dep via Pydantic; add explicitly if not).

### 2. Rewrite `src/app/modules/ai/runtime.py`
Full replacement. Structure:
```python
async def run_loop(
    *,
    run_id: uuid.UUID,
    agent: AgentDefinition,
    policy: LLMProvider,
    engine: FlowEngineClient,
    trace: TraceStore,
    supervisor: RunSupervisor,
    session_factory: async_sessionmaker[AsyncSession],
    cancel_event: asyncio.Event,
) -> None:
```
Body outline:
1. Transition `Run.status: pending → running`, persist `started_at` — one short-lived session.
2. Load `RunMemory` (created by `start_run`).
3. State accumulators: `step_count`, `token_count`, `last_policy_error`, `last_engine_error`, `last_tool`.
4. **Loop**:
   a. Build `RuntimeState` from accumulators + `cancel_event.is_set()` + `agent.terminal_nodes`.
   b. `reason = stop_conditions.evaluate(state)`. If non-None → break.
   c. Under a fresh session: build `build_tools(agent, available_nodes=[n.name for n in agent.nodes])`, build `prompt_context`, call `policy.chat_with_tools(messages=..., tools=...)`. Log at DEBUG with `run_id`/`step_id` bound.
   d. Persist `PolicyCall` (all fields from `ToolCall.usage` + prompt + tools). Write trace line.
   e. `last_tool = tool_call`. If `tool_call.name == TERMINATE_TOOL_NAME`: continue (next `evaluate` returns `POLICY_TERMINATED`).
   f. `next_node_name = tool_call_to_node(tool_call, agent)`. If None → raise `PolicyError`.
   g. `validate_tool_arguments(tool_call, agent)`.
   h. Create `Step(run_id, step_number=step_count+1, node_name=next_node_name, node_inputs=tool_call.arguments, status=pending)`; commit; write trace line.
   i. `engine_run_id = await engine.dispatch_node(...)`; update step `engine_run_id`, `status=dispatched`, `dispatched_at=now`; commit.
   j. `try: await asyncio.wait_for(supervisor.await_wake(run_id), timeout=node.timeout_seconds); finally: event.clear()`. Timeout → mark step `failed`, set `last_engine_error`, continue loop (`evaluate` returns `ERROR`).
   k. Reload step (webhook has mutated it). If `status=failed` → set `last_engine_error` from `step.error`.
   l. Merge `step.node_result` into `RunMemory.data`; commit.
   m. `step_count += 1`; `token_count += tool_call.usage.input_tokens + tool_call.usage.output_tokens`.
5. **Termination**: open final session; set `Run.stop_reason=reason`, `status=COMPLETED|FAILED|CANCELLED` (mapping), `ended_at=now`, `final_state = {...}`; commit; write a final trace line.

Error handling:
- `CancelledError` → set `reason=CANCELLED` and go to termination block.
- Any other unexpected exception caught at the top-level `try`: set `reason=ERROR`, log with traceback, proceed to termination.

### 3. Modify `src/app/modules/ai/service.py`
- `start_run` (T-040) will wire this. For now, `run_loop` just needs to be importable.

### 4. Create `tests/modules/ai/test_runtime_iterations.py`
Use fakes (not the real DB integration — that's T-054). Each test exercises one loop control-flow branch:
- Single-step happy: script `("analyze_brief", {...})` → dispatch → webhook COMPLETED → terminate → run COMPLETED with `done_node` (fixture agent has it terminal).
- Terminate tool: script `("terminate", {})` → 0 steps, 1 policy call, `stop_reason=POLICY_TERMINATED`.
- Unknown tool: script `("unknown_node", {})` → `PolicyError`, `stop_reason=ERROR`.
- Invalid args: args don't match schema → `PolicyError`, `stop_reason=ERROR`.
- Step timeout: supervisor never wakes → `asyncio.TimeoutError` caught → `stop_reason=ERROR`.
- Cancel mid-flight: set `cancel_event` → loop exits at next `evaluate`, `stop_reason=CANCELLED`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/runtime_helpers.py` | Create | Memory merge, prompt context, tool validation. |
| `src/app/modules/ai/runtime.py` | Modify | Full loop implementation replacing FEAT-001 stub. |
| `tests/modules/ai/test_runtime_iterations.py` | Create | Branch coverage via fakes. |

## Edge Cases & Risks
- Each iteration opens a new session. Never share the request-scoped session with the loop (CLAUDE.md anti-pattern; document in T-061).
- Webhook arrival during `.clear()` → use the `asyncio.Event` guard pattern or (safer) hold a small buffer of "woken since last clear" flags. Document the pattern in `supervisor.py` per T-037 risks.
- Jsonschema validation may be slow on large arg payloads — skip schema check when the node's `input_schema` is `{}` as an explicit opt-out.
- The composition-integrity test in T-054 is the real acceptance gate for this task.

## Acceptance Verification
- [ ] `run_loop` importable, typed, passes `pyright`.
- [ ] Unit-test matrix (6 branches above) passes with fakes.
- [ ] Each iteration commits on its own session (no cross-iteration session reuse).
- [ ] All terminal paths set `ended_at`, `stop_reason`, `final_state` in one commit.
- [ ] Final trace line written after termination.
