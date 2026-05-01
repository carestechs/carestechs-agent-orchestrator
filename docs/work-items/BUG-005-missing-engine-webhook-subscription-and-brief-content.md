# Bug Report: BUG-005 — Engine webhook subscription never registered + load_work_item only passes path

> **Purpose**: Capture two distinct wiring gaps surfaced by the live `lifecycle-agent@0.3.0` run after BUG-004 fixed the task-lifecycle state machines. Filed and resolved in the same PR (diagnosis came from the operator).

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-005 |
| **Summary** | (a) `ensure_workflows` registers the workflows on the engine but never POSTs to `/api/webhook-subscriptions`, so engine `item.transitioned` events have no live subscription and every engine-mode dispatch parks on its supervisor future and times out. (b) `load_work_item`'s user prompt template only carries `{workItemPath}` — the LLM has no way to read the file, so it invents a title from the external ref. |
| **Severity** | High (a) blocks every engine-mode dispatch in v0.3.0 — assign_task, generate_plan, approve_plan, approve_review, close_work_item all hang for the full timeout. (b) ships a degraded brief that misrepresents the work item. |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run + flow-engine logs (operator diagnosis) |
| **Date Reported** | 2026-05-01 |
| **Date First Observed** | 2026-05-01 (after BUG-004 PR #55 merged and unblocked the prior layer) |
| **Related** | FEAT-006 (engine workflows + webhook ingress), BUG-004 (prior layer) |

---

## 2. Root Cause

### (a) Webhook subscription

`src/app/lifespan.py:_bootstrap_lifecycle_workflows` calls `ensure_workflows(...)` to register the two workflows on the engine and stash their ids in `app.state.lifecycle_workflow_ids`. It does **not** call `engine_client.ensure_webhook_subscription(...)` — the helper exists in `engine_client.py:304` but no caller invokes it. From the engine's side: transitions fire successfully, the engine queries `webhook_subscriptions` for active subscriptions matching `(workflow_id, event_type='item.transitioned')`, finds zero, and silently moves on. The orchestrator's `EngineExecutor` waits on `supervisor.await_dispatch(dispatch_id)` until the configured `dispatch_timeout_seconds` (default 600s) elapses, then synthesises a `failed` envelope.

### (b) load_work_item brief content

`register_lifecycle_v03` registers `load_work_item` with `user_prompt_template="Synthesize the work-item brief at: {workItemPath}"`. `LLMContentExecutor._render_prompt` substitutes `{workItemPath}` from `DispatchContext.intake` but does not read the file at that path. The LLM sees only the path string; with no contents, it produces a generic title derived from the external ref (e.g. "Smoke Test Feature Implementation" from `FEAT-SMOKE-001`).

---

## 3. Fix

### (a) `ensure_engine_subscriptions(...)` in `lifecycle/bootstrap.py`

New helper, called from lifespan immediately after `ensure_workflows`. Iterates the resolved `workflow_ids` dict; for each workflow, GETs `/api/webhook-subscriptions?workflowId=<id>` first to check whether a row already matches `(url, event_type)`. Skips the POST when a match is found. Otherwise POSTs to create the subscription. Engine `409` is also tolerated as a defensive belt-and-braces.

The callback URL is derived from `Settings.public_base_url` (env var `PUBLIC_BASE_URL`). **Container deploys must point this at the orchestrator's container DNS name on the shared network** (e.g. `http://orchestrator-api:8000`) — `localhost` resolves to the engine container itself, and the engine's HTTP delivery would 404.

Wrapped in `try/except` at the lifespan call site so a failure logs loudly but doesn't crash boot — the rest of the orchestrator still works for non-engine paths during diagnosis.

### (b) `prompt_context_loader` extension on `LLMContentExecutor`

New optional async callable parameter: `prompt_context_loader: Callable[[DispatchContext], Awaitable[Mapping[str, Any]]]`. When set, the loader runs before `format_map`; its return dict is merged on top of intake-derived bindings. `load_work_item`'s binding installs a loader that reads the file at `{workItemPath}` (resolved relative to `Settings.repo_root` when the path is relative) and exposes its contents as `{workItemBrief}`. The user prompt template is rewritten to embed the body between sentinel comments. File-not-found and OSError degrade gracefully — a placeholder body is emitted with a comment explaining the failure, and the LLM can still produce a best-effort brief from the external ref alone.

---

## 4. Verification

- New unit tests:
  - `test_bootstrap.py::TestEnsureEngineSubscriptions` (3 cases: subscribes one per workflow with correct url/event_type/secret; 409 swallowed and iteration continues; non-409 EngineError surfaces).
  - `test_llm_content.py::TestPromptContextLoader` (2 cases: loader bindings merged into format_map; loader exception yields failed envelope before LLM call).
- Existing v0.3.0 e2e + rejection tests unaffected (they don't run `_bootstrap_lifecycle_workflows`; respx mocks the engine surface).
- Live re-run of `lifecycle-agent@0.3.0` will exercise both paths.

---

## 5. Out of Scope

- Per-tenant subscription cleanup (orphan rows after tenant rotation) — separate concern, parallel to BUG-002's tenant-scoped cache work.
- Richer prompt templating (Jinja, partials) — currently `str.format_map`; richer templating is a future FEAT.
- A generic "load file" pre-step for any LLM-content node — for now the loader is per-binding; if a second consumer needs it, a shared helper lands then.

---

## Changelog

- 2026-05-01 — Filed and resolved in the same PR; diagnosis from the operator.
- 2026-05-01 — Followup after operator review: the engine's POST `/api/webhook-subscriptions` is *not* idempotent server-side (creates a fresh row per call); a re-boot would accumulate duplicates. Added pre-flight GET via new `list_webhook_subscriptions(workflow_id=...)` engine-client method; `_subscription_matches` skips the POST when an existing row matches `(url, event_type)`. Operator also flagged that `localhost:8000` is the wrong callback URL inside container networks — clarified in the helper docstring; `PUBLIC_BASE_URL` must be set to the orchestrator's container DNS name (e.g. `http://orchestrator-api:8000`) for umbrella mode.
