# FEAT-016 — PR URL in Implementation Review Context

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-016 |
| **Name** | PR URL in implementation review context (`lifecycle-agent@0.4.0-manual`) |
| **Target Version** | Continuous |
| **Status** | Open |
| **Priority** | High |
| **Requested By** | Carlos — companion to DevHub FEAT-012 |
| **Date Created** | 2026-06-29 |

---

## 2. User Story

**As an** operator running a `lifecycle-agent@0.4.0-manual` work item, **I want** to submit the PR URL when I signal `implementation-complete`, **so that** the human reviewer at `human_review_implementation` receives the PR URL in their review context and can inspect the actual changes rather than relying on memory alone.

---

## 3. Goal

When the operator signals `implementation-complete` with a `prUrl` in the payload, the orchestrator:

1. Persists `prUrl` (and optional `commitSha`, `summary`) in `RunMemory` under an `implementation_refs` sidecar keyed by `current_task_id`.
2. Surfaces those fields in the `nodeInputs` delivered to `human_review_implementation` via an extended `intake_for_human_review` builder, so the human reviewer (and DevHub) has a direct link to the diff.

The flow itself does not change — `request_implementation → submit_implementation → human_review_implementation` is unchanged. This FEAT only adds data persistence and context surfacing around that transition.

---

## 4. Architectural Decisions

### 4.1 Where the `task_id` comes from in the patch builder

`apply_implementation_signal(payload, current_memory)` follows the same pattern as other patch builders: it reads `current_task_id` from `read_lifecycle_memory(current_memory)` rather than from the signal payload. At the point `implementation-complete` fires, `current_task_id` in memory always points to the task being implemented. No changes to `SignalCreateRequest` are needed.

### 4.2 Memory shape — top-level sidecar, not inside `lifecycle.v1`

`implementation_refs` lives at the top level of `RunMemory.data` (same as `plans`, `assignments`, `rejections`) rather than inside `lifecycle.v1`. This keeps `LifecycleMemory` schema stable and avoids a schema migration:

```json
{
  "implementation_refs": {
    "T-001": {
      "prUrl": "https://github.com/org/repo/pull/42",
      "commitSha": "abc123",
      "summary": "optional free-text"
    }
  }
}
```

Keyed by `task_id` so multi-task runs accumulate one entry per task.

### 4.3 Payload validation happens in the patch builder, not at the HTTP boundary

`SignalCreateRequest.payload` is `dict[str, Any]` for all signals — there is no per-signal-name HTTP-boundary discriminator. Validation of `ImplementationCompletePayload` happens inside `apply_implementation_signal` (same as `apply_tasks_correction` calls `TasksConfirmedPayload.model_validate(payload)`). No changes to `SignalCreateRequest` are required.

### 4.4 `human_review_implementation` is a `HumanExecutor` — no LLM prompt

In `@0.4.0-manual`, `human_review_implementation` is a human pause node. The review context is surfaced through `nodeInputs` (the `intake_builder`), which is what DevHub renders. There is no LLM prompt to update for this variant. If `@0.3.0`'s LLM `review_implementation` node is ever extended with a PR-fetch capability, that ships as a separate improvement.

---

## 5. Feature Scope

### 5.1 Included

#### `ImplementationCompletePayload` — new Pydantic model in `lifecycle_manual_patches.py`

```python
class ImplementationCompletePayload(BaseModel):
    model_config = _PayloadConfig   # extra="forbid"
    pr_url: str | None = None
    commit_sha: str | None = None
    summary: str | None = None
```

#### `apply_implementation_signal` — new memory patch builder

Registered as `memory_patch_builder` on the `request_implementation` `HumanExecutor` binding. Currently this binding has no builder — the signal advances the run without touching memory.

```python
def apply_implementation_signal(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = ImplementationCompletePayload.model_validate(payload)
    if not parsed.pr_url and not parsed.commit_sha and not parsed.summary:
        return {}   # empty signal — no-op, backward compat preserved
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id
    if not task_id:
        return {}
    existing: dict[str, Any] = dict(current_memory.get("implementation_refs") or {})
    existing[task_id] = {
        "prUrl": parsed.pr_url,
        "commitSha": parsed.commit_sha,
        "summary": parsed.summary,
    }
    return {"implementation_refs": existing}
```

#### `intake_for_human_review` — extend with `implementationRef`

`intake_for_human_review` currently returns `currentTask`, `planMarkdown`, `reviewHistory`. Extend it to include `implementationRef`:

```python
impl_refs = cast(dict, current_memory.get("implementation_refs") or {})
impl_ref = impl_refs.get(task_id)   # None if no prUrl was submitted
return {
    "currentTask": ...,
    "planMarkdown": ...,
    "reviewHistory": ...,
    "implementationRef": impl_ref,  # {prUrl, commitSha, summary} or None
}
```

#### Bootstrap wiring — `bootstrap.py`

Add `memory_patch_builder=apply_implementation_signal` to the existing `HumanExecutor` registration for `request_implementation` in `register_lifecycle_v04_manual`.

### 5.2 Excluded

- **Fetching the PR diff at signal time or review time** — the orchestrator passes the URL through; diff fetching is the reviewer's concern (DevHub or a future LLM executor).
- **Validating `prUrl` against the run's `codeSource.repo`** — any non-empty string is accepted.
- **Auto-signaling from CI** — out of scope for this FEAT.
- **`@0.3.0` LLM `review_implementation` node** — separate improvement if needed.

---

## 6. Acceptance Criteria

- **AC-1:** Signaling `implementation-complete` with `{ "prUrl": "https://..." }` persists `prUrl` in `RunMemory.data["implementation_refs"][current_task_id]`.
- **AC-2:** The `human_review_implementation` node's `nodeInputs.implementationRef.prUrl` contains the submitted URL.
- **AC-3:** Signaling without `prUrl` (empty payload `{}`) produces no error, leaves `implementation_refs` unchanged — backward compatible with existing runs.
- **AC-4:** Multi-task run: each task's `implementation_refs` entry is independent; submitting a second task's signal does not overwrite the first.
- **AC-5:** Unit tests for `apply_implementation_signal` (with and without `prUrl`) and for the extended `intake_for_human_review`.

---

## 7. Dependencies

| Dependency | Direction |
|------------|-----------|
| FEAT-015 — Manual lifecycle variant | Must be complete; `request_implementation` and `human_review_implementation` bindings exist |
| IMP-006 — Rejection support | Must be complete; `memory_patch_builder` pattern established |
| DevHub FEAT-012 — PR-based review UI | Parallel; orchestrator ships independently — `prUrl` in payload is a no-op until DevHub sends it |

---

## 8. Motivation

The `implementation-complete` gate currently advances the run with no data — the human reviewer has no reference to what was built beyond the plan and memory. With this feature, every task's review is anchored to a concrete PR URL, letting the reviewer open the diff directly from DevHub's checkpoint UI without hunting for the branch.
