# Improvement Proposal: IMP-002 — Lifecycle Self-Hosted Delivery Proof

> First concrete demonstration of AD-6 (eat our own dog food): drive one real
> repo change end-to-end through `lifecycle-agent@0.1.0` to close the
> Stakeholder Success Metric #1 loop.

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-002 |
| **Name** | Lifecycle self-hosted delivery proof |
| **Status** | Completed |
| **Completed** | 2026-04-18T22:24:24+00:00 |
| **Priority** | Medium |
| **Requested By** | Tech Lead (`carlos.escalona@carestechs.com.br`) |
| **Date Created** | 2026-04-18 |

## 2. Problem

Every FEAT-005 artifact — agent YAML, local-tool registry, pause/signal
endpoint, correction bound — exists but has only been exercised through
automated tests.  The stakeholder's primary success metric ("≥1 feature of
this repo shipped end-to-end via the orchestrator") stays at 0 until one
real work item lands through the lifecycle agent on the operator's real
machine with real Anthropic decisions.

## 3. Scope

Exactly one line added to `README.md`'s *Self-Hosted Feature Delivery*
section: a smoke-check invocation that newcomers can run before their first
real lifecycle run.

```
uv run orchestrator agents show lifecycle-agent@0.1.0
```

Expected placement: immediately after the heading's opening paragraph, as
step 0.

### Out of scope

- Any other README edits, formatting changes, or prose rewrites.
- Adding the command anywhere other than the self-hosted section.

## 4. Acceptance Criteria

- **AC-1**: `README.md` contains the new line verbatim.
- **AC-2**: The agent-produced task list (`tasks/IMP-002-lifecycle-proof-tasks.md`)
  declares at least one task and the agent writes a plan for it.
- **AC-3**: The review stage emits `verdict=pass`.
- **AC-4**: `close_work_item` flips this file's Status to `Completed` and
  adds a `Completed` timestamp row.
- **AC-5**: The run's trace file is committed under
  `docs/work-items/evidence/IMP-002-trace.jsonl` (redacted of secrets).

## 5. Constraints

- The diff stays under 5 lines.
- The operator-implementation stage (landing the README edit + signalling)
  is the only human-in-the-loop part of the run.

## 6. Traceability

| Reference | Link |
|-----------|------|
| **Closes metric** | `docs/stakeholder-definition.md` → Success Metrics → "Self-hosted feature delivery" |
| **Closes feature** | `docs/work-items/FEAT-005-lifecycle-agent.md` |
