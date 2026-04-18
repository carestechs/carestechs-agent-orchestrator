# Improvement Proposal Template

> **Purpose**: Describe a refactoring, performance improvement, or technical improvement at a high level before generating implementation tasks. This document ensures improvements are motivated, scoped, and risk-assessed before work begins.
> **When to use**: Before running the refactoring task generation prompt. One Improvement Proposal per improvement initiative.

---

## 1. Identity

> *Unique identifier and classification for tracking and prioritization.*

| Field | Value |
|-------|-------|
| **ID** | IMP-[XXX] |
| **Name** | [Improvement name — short, descriptive] |
| **Type** | [Refactoring · Performance · Testability · Maintainability · Security · Accessibility · Developer Experience] |
| **Status** | [Proposed · Approved · In Progress · Completed · Deferred · Rejected] |
| **Priority** | [Critical · High · Medium · Low] |
| **Proposed By** | [Who proposed this — developer, tech lead, automated analysis, incident review] |
| **Date Created** | [YYYY-MM-DD] |

<!-- TODO: Fill in all identity fields. Use a sequential ID (IMP-001, IMP-002, etc.). Choose the Type that best describes the primary motivation. -->

---

## 2. Target Area

> *Which part of the system is being improved? Be specific about the component, module, or code area.*

**Component / Module:** [Specific component — e.g., "OrderService", "Authentication middleware", "Frontend state management"]

**Affected Files / Directories:**
- [file/path/or/directory/]
- [file/path/or/directory/]

<!-- TODO: List the primary files or directories that will be modified. This helps scope the effort and identify potential conflicts with other work items. -->

---

## 3. Current State

> *Describe the current implementation and its specific problems. Be factual — this is the "before" picture.*

### How It Works Today

[2-4 sentences describing the current implementation]

### Problems

1. **[Problem name]**: [Specific description — e.g., "God class with 1500 lines and 6 responsibilities"]
2. **[Problem name]**: [Specific description — e.g., "Tight coupling makes isolated testing impossible"]
3. **[Problem name]**: [Specific description — e.g., "Duplicated pricing logic in 3 files"]

<!-- TODO: Be specific and measurable where possible. "Slow" is worse than "P95 response time is 2.3s, target is 500ms". "Hard to maintain" is worse than "Last 3 PRs touching this file required 5+ review rounds". -->

### Evidence

- [Metric, incident, or observation that proves the problem — e.g., "Test suite for this module takes 45 minutes"]
- [Link to incident, PR, or discussion that motivated this proposal]

---

## 4. Desired State

> *Describe what the code should look like after the improvement. This is the "after" picture.*

### Target Implementation

[2-4 sentences describing the desired end state]

### Benefits

1. **[Benefit name]**: [Specific improvement — e.g., "Each service testable in isolation with <100ms test run"]
2. **[Benefit name]**: [Specific improvement — e.g., "New payment methods addable without modifying OrderService"]
3. **[Benefit name]**: [Specific improvement — e.g., "Single source of truth for pricing logic"]

---

## 5. Trigger and Motivation

> *Why this improvement, why now? What makes this the right time to invest in this work?*

**Trigger:** [What prompted this proposal — e.g., "Need to add Apple Pay, current structure makes this risky", "Tech debt sprint", "Incident post-mortem action item"]

**Impact if deferred:** [What happens if we don't do this now — e.g., "Apple Pay feature will take 3x longer and risk breaking existing payments"]

**Dependencies on this improvement:** [Other FEAT/BUG/IMP IDs that benefit from or are blocked by this]

---

## 6. Affected Entities and Components

> *Which data model entities, API endpoints, or UI components are affected? Reference specs for full definitions.*

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| [Entity or component] | [How it's affected — moved, split, renamed, restructured] | [Section in data-model/api-spec/architecture/ui-spec] |

<!-- TODO: Repeat for each affected entity or component. If the improvement changes component boundaries (e.g., extracting a service), note both the source and target. -->

---

## 7. Risk Assessment

> *What could go wrong? What's the blast radius? This shapes how tasks are phased.*

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| [Risk description] | [High · Medium · Low] | [High · Medium · Low] | [How to mitigate — e.g., "Feature flag", "Incremental migration", "Parallel implementation"] |
| [Risk description] | [Likelihood] | [Impact] | [Mitigation] |

### Rollback Strategy

[How to revert if the improvement causes problems — e.g., "Feature flag rollback", "Git revert safe because old and new coexist until cleanup phase"]

---

## 8. Constraints

> *Boundaries on how the improvement can be implemented.*

- [Constraint 1 — e.g., "Must maintain backward compatibility for public API"]
- [Constraint 2 — e.g., "Must be deployable incrementally, no big-bang release"]
- [Constraint 3 — e.g., "Cannot change database schema in this phase"]

---

## 9. Success Criteria

> *How do we know the improvement achieved its goals? Measurable where possible.*

- [Criterion 1 — e.g., "OrderService reduced to <200 lines, single responsibility"]
- [Criterion 2 — e.g., "Test suite for affected modules runs in <5 minutes"]
- [Criterion 3 — e.g., "New payment method can be added by creating one file"]

<!-- TODO: These should be verifiable after implementation. They feed into the final verification phase of refactoring tasks. -->

---

## 10. Current Test Coverage

> *What's the safety net before we start changing things?*

| Area | Coverage | Notes |
|------|----------|-------|
| [File or module] | [Approximate % or Good/Partial/None] | [Known gaps or concerns] |

<!-- TODO: Honest assessment of test coverage. Low coverage = more Phase 0 (safety net) tasks will be generated. -->

---

## 11. Traceability

> *Links this improvement back to strategic goals, incidents, or dependent work.*

| Reference | Link |
|-----------|------|
| **Triggered By** | [Incident, feature request, tech debt review — be specific] |
| **Stakeholder Alignment** | [Which guiding principle from the Stakeholder Definition this supports] |
| **Architecture Reference** | [Which section of ARCHITECTURE.md describes the affected area] |
| **Related Work Items** | [Other FEAT/BUG/IMP IDs that relate to this improvement] |
| **Blocked Features** | [FEAT-XXX IDs that are waiting for this improvement] |

---

## 12. Usage Notes for AI Task Generation

When generating refactoring/improvement tasks from this Improvement Proposal:

1. **Safety-first phasing**: Always generate Phase 0 (test coverage) tasks based on Section 10 before any refactoring tasks. Lower coverage = more safety-net tasks.
2. **Problem-driven**: Each generated task should address a specific problem from Section 3. Don't generate tasks that don't map to a stated problem.
3. **Incremental approach**: Use the risks in Section 7 to determine phasing. High-risk improvements should use parallel implementation (old + new coexist) before migration.
4. **Constraint respect**: All constraints in Section 8 must be respected — especially backward compatibility and deployment strategy.
5. **Success verification**: Generate a final verification task that checks all success criteria from Section 9.
6. **No feature creep**: This is an improvement, not a feature. Do not generate tasks that add new functionality. If new behavior is needed, it belongs in a separate Feature Brief.
7. **Rollback awareness**: Each phase should leave the system in a working state. Reference the rollback strategy from Section 7 in migration tasks.
8. **Traceability**: Include the Improvement Proposal ID (IMP-XXX) in the task generation output summary for cross-referencing.
