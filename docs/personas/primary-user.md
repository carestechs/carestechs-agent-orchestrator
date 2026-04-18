# Persona: The Solo Tech Lead Driving Feature Delivery

> A technically strong builder who already uses the ia-framework manually and wants an agent to run the lifecycle loop for them — without losing the rigor the framework enforces.

## Who They Are

A senior engineer or founding tech lead who owns delivery end-to-end on a small team (often a team of one plus AI assistants). They've adopted the ia-framework because it gives them structure — specs, work items, tasks, plans — but every cycle through the loop is hand-driven, and the bookkeeping cost is starting to dominate the actual building.

- **Role/Title:** Solo founder, founding engineer, or tech lead on a 1–3 person team.
- **Key Characteristics:** High-context on their own product, rigorous about process when it pays off, allergic to ceremony that doesn't, comfortable trusting tools but not blindly.
- **Relationship with Technology:** Power user of Claude Code and similar agentic tooling. Already composes flows in `carestechs-flow-engine`. Reads code more than prose; prefers config-as-data over GUIs.

## Core Problem

- **The Problem:** They have a filled-in ia-framework (personas, stakeholders, architecture, data/API/UI specs, CLAUDE.md) and a backlog of feature briefs. Moving each brief through *task generation → assignment → planning → implementation → review → corrections → closure* is a sequence of prompts they re-run by hand, with state held in their head and in scattered markdown files. Context gets dropped, review steps get skipped under pressure, and parallel tasks collide.
- **Current Workaround:** They drive the loop manually in Claude Code — one prompt per stage, copy-pasting context, remembering which task is at which stage, occasionally forgetting to update work-item status.
- **Why That Fails:** It scales linearly with attention. The framework's value comes from *consistent* application of every step; manual drive means steps get skipped exactly when consistency matters most (crunch time). It also wastes the framework's biggest asset — that work is already structured as data — by keeping a human in the routing loop.
- **Consequences of Inaction:** The framework decays into an aspirational doc set. Tasks ship without plans, plans ship without review, work items never get marked closed, and the team reverts to ad-hoc prompting — losing the auditability and quality floor the framework was built to provide.

## Why This Persona First

- **Pain Acuity:** Acute and daily. They feel it every time they re-run the same prompt sequence.
- **Market Size:** Small but growing — every team that has adopted the ia-framework is a candidate, and the framework itself is the funnel.
- **Willingness to Pay/Adopt:** Very high. They already invested in the framework and the flow engine; an orchestrator that makes both pay off is an obvious next purchase of attention.
- **Strategic Fit:** This persona is also the *author* of the orchestrator's first real test cases. Eating our own dog food — using the orchestrator to deliver the orchestrator — validates the composition model end-to-end.

## Other Segments Considered

| Segment | Why Not First |
|---------|---------------|
| Non-technical PMs / founders | Need a UI for authoring agents and inspecting runs — explicitly out of scope for v1. |
| Larger engineering teams (5+) | Multi-actor coordination, permissions, and human-in-the-loop review queues add scope before the core loop is proven. |
| Domain-agent builders (code review, migration bots) | They are *consumers* of this orchestrator, not users of v1. They unblock once primitives are stable. |
| Teams not yet on the ia-framework | Onboarding cost is the framework itself, not the orchestrator. Wrong wedge. |

## AI Task Generation Notes

- **User Context:** Assume the user has the ia-framework docs filled in and a working `carestechs-flow-engine`. Assume CLI-first, config-as-data, terminal-native workflows. Do not assume a UI exists.
- **Peak Pain Moment:** Mid-feature, when three tasks are in flight at different stages and the user has to remember which one needs a plan, which needs review, and which is blocked. Tasks should reduce that mental load.
- **Success Looks Like:** A feature brief goes in; tasks, plans, implementations, and review outcomes come out — with work-item status kept current and every step traceable. The user's job becomes *deciding* and *unblocking*, not *routing*.
- **Anti-Patterns:** Don't propose GUIs, hosted control planes, or domain-specific agents. Don't bypass the framework's stages to "save steps" — the framework's discipline is the point. Don't hide LLM decisions behind opaque abstractions; this user wants to see the policy call.
