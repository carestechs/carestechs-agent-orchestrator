# Stakeholder Definition

## Executive Summary

- **What:** An integration + reactor layer that sits between the `carestechs-flow-engine` (a private stateful backend) and the outside world (humans, UIs, bots, VCS webhooks, LLM agents). The orchestrator is the **sole gateway** to the engine and the **sole owner** of workflow-specific logic: which transitions to drive, which effectors to fire on each state change, which tools to call for task generation and review.
- **Value Proposition:** Teams get a concrete, opinionated feature-delivery workflow on top of a generic state engine. They don't need to build their own engine client, their own workflow rules, or their own tool-integration code — the orchestrator is that code, for this specific flow.
- **Success Criteria:**
  1. The orchestrator drives at least one real feature of its *own* codebase end-to-end (brief → closed work item) without a human routing between stages.
  2. Every stage transition is traceable: the agent's policy call, inputs, and outputs are inspectable per node.
  3. Removing the LLM policy degrades the system to a deterministic flow that still runs — proving the composition boundary holds.
  4. The engine is never reached from outside the orchestrator. Engine credentials stay private; every external caller (human, webhook, agent, UI) talks to the orchestrator.

## Core Business Problem

The ia-framework gives small teams a rigorous lifecycle (spec → tasks → plans → implementation → review → closure), but every transition is hand-driven. The `carestechs-flow-engine` can encode the *steps* deterministically, yet the *decisions between steps* — what loops back, what needs human input, what's done — still live in the operator's head. The result is a high-quality framework that decays under load exactly when its discipline matters most.

**Current Pain Points:**
- The lifecycle's value depends on consistent application; manual drive means steps get skipped under pressure.
- State (which task is at which stage, which plan needs review) lives in scattered markdown and the operator's memory.
- The flow engine alone can't make decisions — pure pipelines can't model "review failed, loop back to corrections."
- Baking agent semantics directly into the flow engine would churn its stable core and conflate two concerns that evolve at different speeds.

**Desired Outcome:**
A feature brief enters the orchestrator. The agent generates tasks per the framework, assigns each to the right executor, produces plans, runs implementations, reviews against the framework's standards, loops on corrections, and closes work items — keeping `docs/work-items/` current as it goes. The human's role shifts from *routing* to *deciding* and *unblocking*. The flow engine stays agent-agnostic; agent semantics live cleanly in this layer.

## Architectural Position

```
┌──────────────────────────┐        ┌───────────────┐        ┌────────────┐
│ External actors          │ HTTP   │               │ HTTP   │            │
│  · humans via CLI/UI     │───────▶│  Orchestrator │───────▶│   Engine   │
│  · GitHub webhooks       │        │               │        │  (private) │
│  · LLM agent runs        │◀───────│  (reactor +   │◀───────│            │
│  · Slack/Jira/etc.       │ effect │   effector    │ webhook│            │
│                          │   ors  │   router)     │   out  │            │
└──────────────────────────┘        └───────────────┘        └────────────┘
```

Three hard rules follow from this shape:

1. **The engine is a private backend.** External systems never reach it directly. Only the orchestrator holds engine credentials and issues transition calls. If another consumer ever needs engine state, they consume it via the orchestrator's read API — not the engine itself.
2. **The orchestrator is the only front door.** Every external trigger — a human approving a plan, a GitHub PR webhook, an agent reporting an implementation — enters through the orchestrator's HTTP surface. The orchestrator validates, translates, and forwards to the engine.
3. **State changes in the engine fan out to effectors.** When the engine emits an `item.transitioned` webhook, the orchestrator's reactor decides what to do next: post a GitHub check, notify an assignee, dispatch a task-generation agent, advance a derivation. **Effectors are first-class — the product value is as much in them as in the state transitions themselves.**

A rule of thumb: if a change would require another service to hit the engine directly, or would require the orchestrator to *not* react to a transition that affects external state, it's outside the architecture.

## Product Philosophy

1. **The engine is private; the orchestrator is the gateway.** Engine credentials never leave the orchestrator process. Every external caller (humans, webhooks, agents, integrations) goes through the orchestrator's API. This is the single most load-bearing design rule — it dictates what lives where, why effectors matter, and why there is no "direct engine access" escape hatch.
2. **Effectors are the product.** Moving state in the engine is trivial. The value lives in what the orchestrator *does* on each transition: request an assignment through Slack, post a GitHub check, dispatch a task-generation agent, notify a reviewer, update `docs/work-items/`. Any transition that doesn't trigger an effector somewhere is either a missed integration or proof that stage doesn't belong in the flow.
3. **Composition over extension.** The flow engine stays simple and generic. Workflow-specific behavior — policy, memory, effectors, decision-making — lives in this orchestrator. We do not modify the engine to make the orchestrator's job easier.
4. **An agent is flow + policy + memory.** If removing the LLM turns the system into a normal pipeline, it was an agent. If it still runs fine, it was just a flow. This test guides every design decision.
5. **Agents are data, not just code.** Agent definitions should be expressible as YAML/JSON so they can be versioned, diffed, and (later) authored by non-developers. Code-only agents are a v0 convenience, not the destination.
6. **Observability is non-negotiable.** Agentic flows are harder to debug than deterministic ones. Per-node traces, policy inputs/outputs, effector calls, and timing are required from day one — not a later phase.
7. **Eat our own dog food.** The first real consumer of the orchestrator is the orchestrator's own delivery loop. If it can't ship its own features through itself, it isn't ready to ship anyone else's.

## Scope Lock

### In Scope (v1)

- **Engine-as-private-backend integration.** Orchestrator owns the engine client, workflow registration, and item/transition mirroring. No other process talks to the engine.
- **Public ingress surface.** HTTP endpoints and webhook receivers that let humans, VCS, and other tools trigger engine transitions through the orchestrator.
- **Reactor + effector surface.** On every `item.transitioned` webhook, the orchestrator fires the right outbound actions: GitHub check-run, assignment notification, task-generation dispatch, derivation (W2, W5, etc.).
- An **Agent** primitive: a bundle of (flow, policy, memory scope) runnable on top of `carestechs-flow-engine`.
- A **policy interface** with a minimum viable contract: `(state, available_nodes) -> next_node`, with structured outputs and tool-call semantics.
- **Subflow-as-node** integration with the flow engine, including scoped child context (traces, token budget, cancellation) with explicit propagation.
- A **stop-condition** model covering budget limits, explicit "done" nodes, and policy-driven termination.
- An **end-to-end lifecycle agent** that drives the ia-framework loop: feature creation → task generation → task assignment → plan creation → implementation → review → corrections → closure.
- **Observability hooks**: per-node traces, policy call inputs/outputs, effector invocations, timing, and run inspection.
- A **serialization schema** for agent definitions (YAML/JSON), even if the v1 authoring path is still code-first.

### Explicitly Out of Scope

- **Direct external access to the engine.** No external system gets engine credentials. Any future "let the UI drive the engine" shortcut violates the architecture — the UI talks to the orchestrator, always.
- **A UI for authoring agents or driving work items.** CLI, config-as-data, and API only in v1. A UI assumes a non-technical user we are not yet serving. Consumers who need a UI build it against the orchestrator's API.
- A **hosted runtime or control plane.** Agents run locally, in the user's environment, against their own framework docs.
- **Specific domain agents** (code review bots, migration agents, scaffolding agents). These are *consumers* of the orchestrator, built later once the primitives prove out.
- **Multi-agent systems beyond the trivial case.** A flow whose nodes are agents falls out for free from composition, but explicit multi-agent coordination patterns (negotiation, delegation markets, shared blackboards) are not v1.
- **Production-grade dependencies on flow-engine churn.** v1 starts only after `carestechs-flow-engine` ships its stable public API, observability hooks, and `FlowNode`-as-subflow primitive.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Self-hosted feature delivery | ≥1 feature of this repo shipped end-to-end via the orchestrator | Work item closed; git history shows orchestrator-driven commits per stage |
| Lifecycle stage automation coverage | All 8 lifecycle stages (intake → closure) executable without manual routing | Stage transitions logged by the orchestrator, not by a human prompt |
| Policy traceability | 100% of policy calls have inspectable inputs, outputs, and selected next-node | Per-run trace export; spot-check on every shipped feature |
| Composition integrity | Removing the LLM policy degrades cleanly to a deterministic flow run | Test: same agent definition with a stub policy completes as a pipeline |
| Time-to-task-list from brief | <10 minutes from feature brief intake to generated task list | Timestamp delta between intake event and task-list artifact |

## User Flow Summary

1. **Entry:** The user has the ia-framework docs filled in and a feature brief ready (`docs/work-items/FEAT-*.md`).
2. **Onboarding:** The user defines or selects a lifecycle agent (initially code, eventually YAML) and points it at their repo.
3. **Core Action:** The user submits a feature brief. The orchestrator drives task generation, assignment, planning, implementation, review, and correction loops — pausing for human input only at explicitly marked decision points.
4. **Value Moment:** The user watches a feature move from brief to closed work item with every stage traceable, without re-running prompts by hand. The framework's discipline is preserved automatically.
5. **Return Trigger:** The next feature brief. Each successful run lowers the cost of the next one and increases trust that the framework's rigor will hold under load.

## AI Task Generation Notes

- **Always respect the Scope Lock.** Do not propose UIs, hosted runtimes, or domain-specific agents. Do not propose modifications to `carestechs-flow-engine` to make agent behavior easier — that violates the composition principle. Do not propose any path that gives external systems direct engine access — that violates the architectural position.
- **Honor the orchestrator-as-gateway rule.** Every new integration (Slack, Jira, a new VCS, a new tool) adds two things: (a) an ingress on the orchestrator for inbound events, (b) an effector on the orchestrator for outbound actions. It does **not** add a direct engine consumer.
- **Favor effectors on every transition.** If a stage transition has no effector, ask whether the stage is real. Transitions without outbound consequences are usually modeling mistakes.
- **Align with Product Philosophy.** Every task should preserve the flow/policy/memory separation. If a task blurs that boundary, flag it explicitly with the trade-off.
- **Target Success Metrics.** Prioritize work that unblocks self-hosted feature delivery and policy traceability before anything else.
- **Honor the dependency on `carestechs-flow-engine`.** Tasks that assume engine APIs not yet stabilized must call out the dependency and either stub or defer.
- **Reference this document** when making prioritization or scope decisions, especially around what *not* to build in v1.

## Changelog

- **2026-04-21** — Clarified architectural position: the engine is a private backend, the orchestrator is the sole gateway for external actors, and effectors on each transition are first-class product surface. Updated Executive Summary, added Architectural Position section, expanded Product Philosophy, tightened Scope Lock, added orchestrator-as-gateway rule to AI Task Generation Notes.
