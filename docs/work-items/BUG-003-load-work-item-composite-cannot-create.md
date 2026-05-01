# Bug Report: BUG-003 — `load_work_item` Composite cannot perform W1 (engine item creation)

> **Purpose**: Capture the second issue uncovered by the live `lifecycle-agent@0.3.0` run, after the empty-tools fix unblocked the LLM call.
> **Template reference**: `.ai-framework/templates/bug-report.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-003 |
| **Summary** | `CompositeLLMEngineExecutor` requires `engineItemId` in dispatch intake, but `load_work_item` is the W1 entry point — there is no engine item yet. The composite dispatches `transition_item`; W1 needs `create_item`. |
| **Severity** | High (blocks v0.3.0 production runs end-to-end on the very first node) |
| **Status** | Open |
| **Reported By** | Live `lifecycle-agent@0.3.0` run via `real-run.sh` against Anthropic + flow-engine |
| **Date Reported** | 2026-05-01 |
| **Date First Observed** | 2026-05-01 (immediately after merging PR #51 which fixed empty-tools wiring) |
| **Related** | FEAT-011 (introduced the composite); fix PR #51 unblocked the LLM call that exposed this layer |

### Severity Justification

The bug fires on the very first engine-touching node of the deterministic lifecycle agent. Until it is fixed, no v0.3.0 run can advance past `load_work_item`. The legacy v0.1.0 LLM-policy agent is unaffected — it goes through `lifecycle/work_items.py:open_work_item` which calls `create_item` directly. So production lifecycles can still run on v0.1.0; v0.3.0 is unusable end-to-end.

---

## 2. Steps to Reproduce

**Preconditions:** flow engine running; orchestrator configured against a tenant; `LLM_PROVIDER=anthropic`; PR #51 merged (otherwise the run dies one layer earlier with `policy selected no tool`).

1. `uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=docs/work-items/FEAT-099.md --follow`
2. Step 1 (`load_work_item`) fires the inner `LLMContentExecutor` — succeeds, validated `LoadWorkItemResult` produced.
3. The composite then resolves `engineItemId` from the dispatch intake (`composite.py:138`). Intake at this point is `{"nodeName", "runId", "workItemPath"}` — no engine id, because no engine item exists yet.
4. **Observe:** composite returns a `failed` engine-mode envelope with detail:
   `composite executor requires 'engineItemId' in dispatch intake; got keys=['nodeName', 'runId', 'workItemPath']`
5. Runtime cleanly terminates the run with `stop_reason=error` (termination path itself is well-behaved — outbox + supervisor cancellation tidy).

**Reproducibility:** Always — the failure is deterministic given the v0.3.0 wiring.

---

## 3. Expected vs Actual Behavior

### Expected

A deterministic agent that needs to create the engine work item as its first step should be able to do so behind the executor seam, the same way the v0.1.0 LLM-policy path does via `lifecycle/work_items.py:open_work_item`. The composite (or a sibling executor) should call `engine_client.create_item(...)` for W1, capture the returned `engine_item_id`, persist it into `LifecycleMemory.work_item.engineItemId`, and return a `dispatched` engine-mode envelope so downstream nodes (`generate_tasks`, `assign_task`, `close_work_item`) can address the item.

### Actual

`CompositeLLMEngineExecutor` only knows the `transition_item` path — it requires an existing `engineItemId` in intake (`composite.py:138-149`). Nothing in the v0.3.0 wiring populates that key for the very first node, because the id does not exist until W1 runs. The composite fails; the run terminates.

`lifecycle/work_items.py:90-130` confirms W1 is a `create_item` call in the v0.1.0 path, not a `transition_item`. The composite was designed for transitions on existing items.

---

## 4. Environment

| Field | Value |
|-------|-------|
| **App Version** | main @ 56cbd7a (after FEAT-011 PR-5 merge) + PR #51 |
| **Agent ref** | `lifecycle-agent@0.3.0` |
| **LLM provider** | Anthropic (live) |
| **Flow engine** | live, tenant configured |
| **Trigger** | `real-run.sh` harness; manual `orchestrator run` reproduces equally |

---

## 5. Root Cause

`CompositeLLMEngineExecutor`'s contract is "produce LLM content + transition an existing engine item in one tx." `load_work_item` is the wrong shape for this contract — it is a **creation** step, not a transition. The wiring in `register_lifecycle_v03` registered `load_work_item` as a Composite anyway (transition_key `work_item.W1`, to_status `open`), assuming the engine would treat W1 as a transition from a synthetic `null` state. The engine's W1 is `create_item`, not a transition; the composite's `transition_item` call would fail with a 404 even if `engineItemId` were synthesized.

This is an architectural mismatch surfaced by the live run, not a coding mistake in the composite.

---

## 6. Proposed Fix

**Option 2 from triage discussion: split `load_work_item` into LLM-content + engine-creation nodes.**

Rationale (vs Option 1 — special-case W1 inside the composite):

- The composite's invariant ("transition existing item") stays clean. Special-casing W1 inside it couples a generic adapter to one transition's quirks; the next request (T1×N fanout creation, deferred-creation cases) compounds the coupling.
- The engine id has to land in `LifecycleMemory` either way. Option 2 makes that flow explicit: LLM-content node writes brief into memory; engine-creation node calls W1 and writes the returned id back into memory; downstream nodes read it.
- The "8-node target" was always a soft goal — the migration doc already records PR-3 simplifications (T1×N collapse, post-resume T9 gap). One more node here is consistent with how the YAML has evolved.

### Concrete shape

1. **New executor** `EngineCreateExecutor` (sibling of `EngineExecutor`):
   - Calls `engine_client.create_item(workflow_name=..., name=..., correlation_id=...)` inside the same one-tx outbox + memory-write pattern the existing `EngineExecutor` uses.
   - Captures the returned `engine_item_id`; emits it as `result.engineItemId` so the runtime's `__memory_patch` lifts it into `LifecycleMemory.work_item.engineItemId`.
   - `mode="engine"`; participates in the wake-on-correlation pipeline like its sibling.
2. **Bootstrap rewire** (`register_lifecycle_v03`):
   - `load_work_item` → `LLMContentExecutor` only (writes brief to `LifecycleMemory.work_item.brief`).
   - **New node** `register_work_item` → `EngineCreateExecutor` (calls W1, writes id to memory).
3. **YAML edit** (`agents/lifecycle-agent@0.3.0.yaml`):
   - Insert `register_work_item` between `load_work_item` and `generate_tasks`.
   - Update transitions: `load_work_item: [register_work_item]`, `register_work_item: [generate_tasks]`.
4. **Downstream wiring**: `generate_tasks` and the Composite chain stay as-is — they all run against an existing item and read `engineItemId` from memory the same way they do today.

### Verification

- New unit tests for `EngineCreateExecutor` (analogue of `test_engine_executor.py`).
- Updated AC-1 e2e — trace shows the new `register_work_item` step between `load_work_item` and `generate_tasks`, engine receives one `create_item` call, `LifecycleMemory.work_item.engineItemId` is populated.
- v0.1.0 regression bar (AC-7) stays green.
- Live re-run of `lifecycle-agent@0.3.0` advances past `load_work_item` → `register_work_item` → `generate_tasks`.

---

## 7. Cosmetic Side Item

`real-run.sh` greps `LLM_PROVIDER` from `.env` and trips on inline comments, surfacing a spurious `WARNING`. Docker passes the variable cleanly so the live run is unaffected. Trivial fix in the harness; bundle with the v0.3.0 fix or punt to its own chore.

---

## 8. Out of Scope

- T1×N task-creation fanout — already deferred per FEAT-011 PR-3 simplifications.
- Post-resume T9 transition — already deferred.
- Folding the rejection-path inline `Approval` write through the outbox — that is FEAT-012's scope.

---

## Changelog

- 2026-05-01 — Bug filed against the v0.3.0 wiring after live-run confirmation; recommended Option 2 (split node) as the fix.
