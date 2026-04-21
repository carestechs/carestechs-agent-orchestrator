# FEAT-008 — Engine-as-Authority + Effector Registry

**Status:** Accepted · **Date:** 2026-04-21 · **Supersedes:** [`feat-006-rc2-architectural-position.md`](feat-006-rc2-architectural-position.md)

## Context

The FEAT-006 rc2 closeout ADR concluded that **rc2-phase-2 as merged was the end state** — aux-row writes stay inline at the signal adapter, local `status` / `locked_from` / `deferred_from` columns stay authoritative, the engine is a passive mirror. That conclusion was reasoned under an unstated premise: cross-tool consumers would never need the orchestrator's rich audit data, and "reliability is free when the aux row and idempotency key land in the same transaction."

FEAT-007 shipped the first real outbound effector (GitHub Checks merge-gating). Reviewing where the inline call lives — buried in `lifecycle/service.py`, alongside state writes, wired past the reactor entirely — surfaced the drift. The stakeholder definition's vision (§"Architectural Position") calls for the engine as a **private authoritative backend** and the orchestrator as a **reactor + effector router** where "effectors are the product." The rc2 conclusion contradicts that vision:

- Inline aux writes mean the orchestrator *is* an authority for audit data, not a projection of the engine.
- No effector seam means every new integration (FEAT-007-style) hardcodes into a signal adapter, rebuilding the coupling the reactor was meant to break.
- "Effectors are the product" cannot hold if there is no registry and no enforced "every transition has a named outbound action or an explicit `@no_effector` exemption."

FEAT-008 inverts the rc2-phase-2 premise to match the vision.

## Decision

Three hard rules, reproduced from `docs/stakeholder-definition.md` §"Architectural Position":

1. **The engine is a private backend.** External systems never reach it directly. Only the orchestrator holds engine credentials and issues transition calls. If another consumer needs engine state, they consume it via the orchestrator's read API — not the engine itself.
2. **The orchestrator is the only front door.** Every external trigger — a human approving a plan, a GitHub PR webhook, an agent reporting an implementation — enters through the orchestrator's HTTP surface. The orchestrator validates, translates, and forwards to the engine.
3. **State changes in the engine fan out to effectors.** When the engine emits an `item.transitioned` webhook, the orchestrator's reactor decides what to do next: post a GitHub check, notify an assignee, dispatch a task-generation agent, advance a derivation. Effectors are first-class — the product value is as much in them as in the state transitions themselves.

Operationally, this means:

- **Engine owns state.** `work_items.status` / `tasks.status` become read-through caches written only by the reactor on correlation-matched `item.transitioned` webhook. `locked_from` / `deferred_from` are dropped; engine transition history is authoritative.
- **Aux rows are reactor-written.** `Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation` are materialized in the reactor when the engine confirms the transition, not inline at signal time. Correlation is threaded via `PendingSignalContext` (already merged in rc2-phase-1).
- **Effector registry is the seam.** A pluggable registry keyed on `(entity_type, transition | entry_state | exit_state)` the reactor dispatches on every webhook. `Effector` protocol with `async def fire(context) -> EffectorResult`. Every transition has either a registered effector or an explicit `@no_effector(reason)` exemption with a ≥10-char reason — enforced at lifespan startup.
- **Outbox for webhook loss.** After the signal adapter forwards to the engine, a `pending_aux_writes` row is enqueued keyed on correlation id. An opt-in reconciler (`uv run orchestrator reconcile-aux`) matches orphans against recent engine state on a schedule.
- **FEAT-007 behavior preserved.** GitHub Checks create/update move into effectors; no test changes. The seam moves; the behavior does not.

## Consequences

**What changes:**

- Aux-row writes move from `lifecycle/service.py` signal adapters to reactor-driven effectors.
- `work_items.locked_from`, `tasks.deferred_from` columns dropped (destructive migration with pre-flight on currently-locked/deferred rows).
- `status` columns demoted to cache; signal adapters never touch them.
- New modules: `lifecycle/effectors/` (registry, protocol, built-in effectors), `lifecycle/outbox.py` (outbox model + reconciler), `lifecycle/await_reactor.py` (test helper).
- New `trace_kind="effector_call"` entries with `effector_name`, `entity_id`, `duration_ms`, `status`, `error_code`.
- Inline `_post_create_check` / `_post_update_check` calls deleted after relocation.

**What stays:**

- Signal endpoints (`POST /api/v1/tasks/{id}/approve`, etc.) — they are the ingress surface per rule 2, not the layer being changed.
- Engine-absent fallback — when no engine is configured, the pre-FEAT-008 inline-write path remains so solo-dev flows work.
- FEAT-007 Checks client (PAT, App auth, noop) behavior. Only the call site moves.
- `PendingSignalContext` correlation threading from rc2-phase-1.
- Rejection-edge asymmetry: rejections don't hit the engine (no state change); aux-row materialization for rejections is unconditional in the reconciler.

**Non-obvious footguns:**

- Aux-table unique constraints must be audited before the reactor starts writing (duplicate webhook delivery → duplicate rows otherwise). Flagged in T-167.
- Status cache has a brief stale-read window between signal-202 and webhook arrival. Documented; integration tests use `await_reactor` to wait past it.
- W2/W5 derivations currently fire from the reactor but read local status; they get the new cache on arrival of their triggering webhook. Relocation of the derivation logic itself is explicitly deferred.

## What would flip this decision

We would revisit if:

- **Second consumer needs engine write access.** If a sibling service legitimately needs to issue transitions without going through the orchestrator (e.g., a dedicated ingestion pipeline at scale), the "orchestrator as sole gateway" rule becomes a bottleneck and we rethink whether effector-style integrations cover the need.
- **Effector throughput exceeds single-process capacity.** If a real effector adapter (Slack, Jira) drives enough volume that a single orchestrator worker can't keep up, the registry needs a queue in front of it — at that point the architecture grows a worker tier, and the in-process dispatch here is an interim.
- **The engine can't deliver sub-second webhook latency reliably.** If the stale-read window on the status cache becomes user-visible (e.g., list endpoints consistently show stale state), we either put reads in front of the engine (N+1 cost we rejected in rc2) or introduce an inline write-through for the cache only. Would need measurement, not theory.
- **Compliance forces single-source-of-truth for aux data.** If auditors require the engine to own Approval/Plan/Implementation rows, we migrate those tables to the engine and keep the orchestrator purely transient. Not the direction of this ADR, but the one that would cleanly invalidate it.
