# FEAT-006 rc2 — Architectural Position (post-merge)

**Status:** Accepted · **Date:** 2026-04-19 · **Supersedes:** the aux-write-flip plan sketched in `plans/plan-T-131-reduce-state-columns.md` + `plans/plan-T-133-pending-signal-context.md`.

## The question

Phase-2 of the rc2 realignment was originally scoped to:

1. Move `Approval` / `TaskAssignment` / `TaskPlan` / `TaskImplementation` writes out of the signal adapters and into the engine-webhook reactor.
2. Drop `status` / `locked_from` / `deferred_from` columns from `work_items` + `tasks`.
3. Make the flow engine the sole writer of state; orchestrator becomes a read-through cache.

After landing phase-1 (engine-as-mirror) and phase-2 plumbing (correlation context end-to-end), the cost/benefit of that further migration shifted enough that it's worth re-stating the destination.

## The decision

**rc2-phase-2 as currently merged is the end state.** We will not flip aux-row writes to the reactor, nor drop the local status columns.

### What the engine owns

- **Current state** of every work item and task (`items.currentStatus` in engine-speak).
- **Transition history** (engine audit log).
- **Cross-tool change notification** via `item.transitioned` webhooks — any tool that subscribes sees the same event stream in the same order.

### What the orchestrator owns

- **Rich audit data**: `Approval` (with rejection feedback), `TaskAssignment` history, `TaskPlan`, `TaskImplementation`. These are orchestrator-specific — other tools don't need them, and writing them inline at signal time keeps the full row committed in the same transaction as the idempotency key.
- **Local `status` columns on `work_items` + `tasks`** as a denormalized cache of the engine's state. Writes mirror the engine; reads use the cache to avoid an HTTP round-trip per DTO.
- **`lifecycle_signals` + `pending_signal_context`** tables — orchestrator-scoped bookkeeping.

## Why this beats the original phase-2 plan

1. **Reliability is free.** Inline aux writes land in the same PostgreSQL transaction as the signal idempotency key. If the engine is down when a signal fires, the orchestrator still has a complete audit trail — the engine mirror eventually re-syncs. Moving writes to the reactor meant designing around webhook-delivery gaps (outbox pattern, dual-write + reconciliation, or best-effort-with-alert). None of those buy us anything a cross-tool consumer cares about.
2. **No new failure modes.** With inline writes, "engine webhook didn't arrive" → cross-tool view is stale but orchestrator state is whole. With reactor writes, the same failure → audit trail is missing. The inline path degrades strictly better.
3. **Cross-tool consumers don't need aux rows.** Other subscribers to the engine want to know "task X transitioned to done" so they can reflect it in their own UI. They don't need the Approval row with its feedback text; that's orchestrator-internal detail.
4. **Local cache columns avoid an N+1.** List endpoints (`GET /api/v1/runs/{id}` analogs for work items, once they land) would fan out to the engine once per row if status lived there only. The cache turns that into a single JOIN.
5. **Simpler test surface.** Inline writes mean a signal test can assert the aux row exists synchronously after the POST returns. Reactor writes would require synthetic-webhook helpers in every test that reads aux data.

## What the existing reactor does, then

`lifecycle/reactor.py` remains valuable for:

- **Derivations (W2, W5).** These are state-dependent on child entities; they're naturally driven by the engine's state-change events.
- **Observability.** The reactor consumes `pending_signal_context` rows (deletes them on correlation match) so the table doesn't accumulate orphans.
- **Future extensions.** Cross-tool actions triggered by state changes (notifications, metrics, etc.) hook in here without touching signal adapters.

## Consequences

- The plans for T-131b ("drop state columns") and T-133b-final ("flip aux writes") are superseded by this doc.  They remain in `plans/` for historical context.
- Phase-2 remaining work reduces to: **FEAT-007 GitHub Checks merge-gating** (operational prerequisite: PAT + branch protection).  That's it for the architectural ambition FEAT-006 originally set.
- `WorkItemStatus` / `TaskStatus` enums stay in Python alongside their DB check constraints — they describe the cache and the engine's state vocabulary simultaneously.
- Rejection-edge asymmetry stays: rejections don't hit the engine (status unchanged), only the `Approval` row records them.  Cross-tool subscribers see only the happy-path transitions — acceptable because rejections aren't meaningful to other tools.

## What would flip this decision

We would revisit if:

- A second tool needs write access to the audit data we currently keep orchestrator-local.  (Unlikely — the rich audit is specific to delivery workflows.)
- The orchestrator's status cache demonstrably drifts from the engine enough that an outbox pattern is cheaper than a sync job.  (Would need evidence, not just theoretical risk.)
- A new compliance requirement forces single-source-of-truth for audit.  (Would argue for moving the aux tables to the engine, not for moving the writes.)
