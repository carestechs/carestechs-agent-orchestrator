# Stakeholder Definition

## Executive Summary

- **What:** A coordination layer built on top of `carestechs-flow-engine` that turns the ia-framework's feature lifecycle into an agent-driven loop — composing flows, an LLM policy, and scoped memory so deterministic pipelines become decision-making units.
- **Value Proposition:** Teams that have adopted the ia-framework can hand a feature brief to the orchestrator and get tasks, plans, implementations, reviews, and closure events back — without manually re-running each stage of the loop.
- **Success Criteria:**
  1. The orchestrator drives at least one real feature of its *own* codebase end-to-end (brief → closed work item) without a human routing between stages. **Met** — see [`docs/work-items/IMP-002-lifecycle-proof.md`](work-items/IMP-002-lifecycle-proof.md) (2026-04-18).
  2. Every stage transition is traceable: the agent's policy call, inputs, and outputs are inspectable per node.
  3. Removing the LLM policy degrades the system to a deterministic flow that still runs — proving the composition boundary holds.

## Core Business Problem

The ia-framework gives small teams a rigorous lifecycle (spec → tasks → plans → implementation → review → closure), but every transition is hand-driven. The `carestechs-flow-engine` can encode the *steps* deterministically, yet the *decisions between steps* — what loops back, what needs human input, what's done — still live in the operator's head. The result is a high-quality framework that decays under load exactly when its discipline matters most.

**Current Pain Points:**
- The lifecycle's value depends on consistent application; manual drive means steps get skipped under pressure.
- State (which task is at which stage, which plan needs review) lives in scattered markdown and the operator's memory.
- The flow engine alone can't make decisions — pure pipelines can't model "review failed, loop back to corrections."
- Baking agent semantics directly into the flow engine would churn its stable core and conflate two concerns that evolve at different speeds.

**Desired Outcome:**
A feature brief enters the orchestrator. The agent generates tasks per the framework, assigns each to the right executor, produces plans, runs implementations, reviews against the framework's standards, loops on corrections, and closes work items — keeping `docs/work-items/` current as it goes. The human's role shifts from *routing* to *deciding* and *unblocking*. The flow engine stays agent-agnostic; agent semantics live cleanly in this layer.

## Product Philosophy

1. **Composition over extension.** The flow engine stays simple and agent-agnostic. Agent behavior — policy, memory, decision-making — lives in this orchestrator. The seam is `FlowNode`-as-subflow; we do not modify the engine to make agents work.
2. **An agent is flow + policy + memory.** If removing the LLM turns the system into a normal pipeline, it was an agent. If it still runs fine, it was just a flow. This test guides every design decision.
3. **Agents are data, not just code.** Agent definitions should be expressible as YAML/JSON so they can be versioned, diffed, and (later) authored by non-developers. Code-only agents are a v0 convenience, not the destination.
4. **Observability is non-negotiable.** Agentic flows are harder to debug than deterministic ones. Per-node traces, policy inputs/outputs, and timing are required from day one — not a later phase.
5. **Eat our own dog food.** The first real consumer of the orchestrator is the orchestrator's own delivery loop. If it can't ship its own features through itself, it isn't ready to ship anyone else's.

## Scope Lock

### In Scope (v1)

- An **Agent** primitive: a bundle of (flow, policy, memory scope) runnable on top of `carestechs-flow-engine`.
- A **policy interface** with a minimum viable contract: `(state, available_nodes) -> next_node`, with structured outputs and tool-call semantics.
- **Subflow-as-node** integration with the flow engine, including scoped child context (traces, token budget, cancellation) with explicit propagation.
- A **stop-condition** model covering budget limits, explicit "done" nodes, and policy-driven termination.
- An **end-to-end lifecycle agent** that drives the ia-framework loop: feature creation → task generation → task assignment → plan creation → implementation → review → corrections → closure.
- **Observability hooks**: per-node traces, policy call inputs/outputs, timing, and run inspection.
- A **serialization schema** for agent definitions (YAML/JSON), even if the v1 authoring path is still code-first.

### Explicitly Out of Scope

- A **UI for authoring agents.** CLI and config-as-data only in v1. A UI assumes a non-technical user we are not yet serving.
- A **hosted runtime or control plane.** Agents run locally, in the user's environment, against their own framework docs.
- **Specific domain agents** (code review bots, migration agents, scaffolding agents). These are *consumers* of the orchestrator, built later once the primitives prove out.
- **Multi-agent systems beyond the trivial case.** A flow whose nodes are agents falls out for free from composition, but explicit multi-agent coordination patterns (negotiation, delegation markets, shared blackboards) are not v1.
- **Production-grade dependencies on flow-engine churn.** v1 starts only after `carestechs-flow-engine` ships its stable public API, observability hooks, and `FlowNode`-as-subflow primitive.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Self-hosted feature delivery | **≥1 — met (IMP-002, 2026-04-18)** | Work item closed; git history shows orchestrator-driven commits per stage |
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

- **Always respect the Scope Lock.** Do not propose UIs, hosted runtimes, or domain-specific agents. Do not propose modifications to `carestechs-flow-engine` to make agent behavior easier — that violates the composition principle.
- **Align with Product Philosophy.** Every task should preserve the flow/policy/memory separation. If a task blurs that boundary, flag it explicitly with the trade-off.
- **Target Success Metrics.** Prioritize work that unblocks self-hosted feature delivery and policy traceability before anything else.
- **Honor the dependency on `carestechs-flow-engine`.** Tasks that assume engine APIs not yet stabilized must call out the dependency and either stub or defer.
- **Reference this document** when making prioritization or scope decisions, especially around what *not* to build in v1.
