# Refactoring Task Generation Prompt (v2)

> **Purpose**: Generate tasks for refactoring code while maintaining functionality. Use this when improving code quality, architecture, or preparing for new features.
>
> **v2 Note**: This version uses 10 core templates (7 system templates + 3 work item templates). Improvements should be described in an **Improvement Proposal** (`docs/work-items/IMP-*.md`) before task generation. The inline `<refactoring-scope>` is still supported as a fallback for quick/ad-hoc usage.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "Refactoring"
2. Read the Improvement Proposal from `docs/work-items/IMP-*.md` for the target improvement. If no Improvement Proposal exists, gather the refactoring scope from the user and use the inline `<refactoring-scope>` fallback
3. Use the **Output Format** section below (four-phase structure: Preparation, Parallel Implementation, Migration, Cleanup, Verification) as your deliverable structure
4. Apply the **Constraints** and **Safety Checklist** to shape your output

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, fill in the `<improvement-proposal>` (or `<refactoring-scope>` fallback), and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<task-generation-request>

<context>

<code-conventions>
<!-- REQUIRED: Include CLAUDE.md for target patterns -->
[Paste CLAUDE.md content]
</code-conventions>

<architecture>
<!-- REQUIRED: Include current and target architecture -->
[Paste architecture documentation]
</architecture>

<stakeholder-definition>
<!-- OPTIONAL: Include for large-scale refactoring that affects product scope -->
[Paste principles and scope lock if relevant]
</stakeholder-definition>

</context>

<task-type>Refactoring</task-type>

<improvement-proposal>
<!-- PREFERRED: Paste the full Improvement Proposal document (from docs/work-items/IMP-XXX-name.md) -->
<!-- The Improvement Proposal template provides structured fields for current/desired state,
     risk assessment, success criteria, test coverage, and traceability.
     See .ai-framework/templates/improvement-proposal.md for the full template. -->
[Paste full Improvement Proposal content]
</improvement-proposal>

<!-- FALLBACK: If no Improvement Proposal exists, use this inline scope instead.
     Remove the <improvement-proposal> block above and uncomment this block. -->
<!--
<refactoring-scope>
**Refactoring Type:** [Code Quality | Architecture | Performance | Testability | Maintainability]

**Target Area:**
[Specific component, module, or system being refactored]

**Current State:**
[Description of current implementation and its problems]

**Problems with Current State:**
1. [Problem 1 - e.g., "God class with too many responsibilities"]
2. [Problem 2 - e.g., "Difficult to test due to tight coupling"]
3. [Problem 3 - e.g., "Duplicated logic across multiple files"]

**Desired State:**
[Description of what the code should look like after refactoring]

**Benefits of Refactoring:**
1. [Benefit 1 - e.g., "Easier to add new payment methods"]
2. [Benefit 2 - e.g., "Reduced test complexity"]
3. [Benefit 3 - e.g., "Better separation of concerns"]

**Trigger/Motivation:**
[Why now? e.g., "Preparing for new feature X" or "Tech debt sprint"]

**Affected Entities/Data:**
- [Entity 1]: [What changes about how this entity is handled]
- [Entity 2]: [What changes]

**Current Test Coverage:**
- [Current coverage status for affected area]
- [Known gaps or concerns]
</refactoring-scope>
-->

<request>
Generate tasks to refactor the identified area.

Tasks should:
1. Start with test coverage verification/improvement
2. Proceed in small, safe increments
3. Maintain functionality at every step (no broken intermediate states)
4. Include migration steps if data/API changes needed
5. End with cleanup of old code
6. Verify no regression after each major change

Do not:
- Change functionality (this is refactoring, not enhancement)
- Introduce new features during refactoring
- Leave dead code or commented-out old code
- Skip test updates for changed code
</request>

<constraints>
<!-- Add project-specific constraints -->
- Must maintain backward compatibility: [Yes/No, details]
- Can break internal APIs: [Yes/No]
- Deployment strategy: [All at once | Incremental | Feature flagged]
- Rollback plan required: [Yes/No]
- [Additional constraints]
</constraints>

<output-format>
## Task Output Format

Generate tasks in phases that ensure safety:

### Phase 0: Preparation (Safety Net)

```
### T-[XXX]: Verify/Improve Test Coverage

**Type:** Testing
**Workflow:** standard
**Complexity:** [S | M | L]
**Dependencies:** None

**Rationale:**
[1-2 sentences: why this coverage task is needed before refactoring begins]

**Current Coverage:**
[Known test coverage for affected code]

**Coverage Gaps:**
- [Gap 1 - untested scenario]
- [Gap 2 - missing edge case]

**Tasks:**
1. [Add test for scenario X]
2. [Add test for scenario Y]

**Acceptance Criteria:**
- [ ] All current functionality has test coverage
- [ ] Tests pass before any refactoring begins
- [ ] Test coverage baseline documented
```

### Phase 1: Safe Parallel Implementation

```
### T-[XXX]: [Create New Structure]

**Type:** [Backend | Frontend]
**Workflow:** standard
**Complexity:** [S | M | L | XL]
**Dependencies:** [T-XXX (test coverage)]

**Description:**
[Create new implementation alongside old, without removing old]

**Rationale:**
[1-2 sentences: why this new structure is needed — which problem from the current state it resolves]

**Approach:**
[How to build new structure without breaking old]

**Files Created:**
- [new/file/path.ts] - [purpose]

**Coexistence Strategy:**
[How old and new will coexist temporarily]

**Acceptance Criteria:**
- [ ] New structure is complete
- [ ] Old code still works unchanged
- [ ] New structure passes all intended tests
```

### Phase 2: Migration

```
### T-[XXX]: [Migrate Component/Consumer X]

**Type:** [Backend | Frontend]
**Workflow:** standard
**Complexity:** [S | M | L]
**Dependencies:** [T-XXX (new structure)]

**Description:**
[Switch specific consumer from old to new implementation]

**Rationale:**
[1-2 sentences: why this consumer needs to migrate — what benefit the new implementation provides]

**Migration Steps:**
1. [Step 1]
2. [Step 2]
3. [Verify functionality]

**Rollback Plan:**
[How to quickly revert if problems found]

**Acceptance Criteria:**
- [ ] Component uses new implementation
- [ ] All tests still pass
- [ ] No regression in functionality
```

### Phase 3: Cleanup

```
### T-[XXX]: Remove Old Implementation

**Type:** Cleanup
**Workflow:** standard
**Complexity:** [S | M]
**Dependencies:** [All migration tasks]

**Description:**
[Remove old code now that migration is complete]

**Rationale:**
[1-2 sentences: why cleanup is safe now — what migration milestones confirm the old code is unused]

**Files to Remove/Modify:**
- [old/file/path.ts] - DELETE
- [file/with/imports.ts] - remove old imports

**Acceptance Criteria:**
- [ ] No references to old implementation remain
- [ ] No dead code left behind
- [ ] Build succeeds
- [ ] All tests pass
```

### Phase 4: Verification

```
### T-[XXX]: Final Verification

**Type:** Testing
**Workflow:** standard
**Complexity:** [S | M]
**Dependencies:** [Cleanup tasks]

**Rationale:**
[1-2 sentences: why final verification is needed — what confidence it provides that the refactoring preserved behavior]

**Verification Checklist:**
- [ ] All original tests pass
- [ ] New tests pass
- [ ] Performance benchmarks met (if applicable)
- [ ] No TypeScript/lint errors
- [ ] Documentation updated
- [ ] Team walkthrough completed (if significant change)
```

## Workflow Classification

All refactoring tasks use **`standard`** workflow. Refactoring maintains existing functionality — it does not introduce new screens (no `mockup-first`) or investigate unknowns (no `investigation-first`).

## Summary Section

After all tasks, provide:
- Total tasks by phase
- Critical path and estimated sequence
- Risk assessment
- Recommended review points (where to pause and verify)
- Rollback strategy summary
</output-format>

</task-generation-request>
```

---

## Common Refactoring Patterns

When generating refactoring tasks, Claude should consider these safe patterns:

### 1. Strangler Fig Pattern
- Build new alongside old
- Gradually migrate consumers
- Delete old when unused

### 2. Branch by Abstraction
- Introduce abstraction layer
- Implement old behavior behind abstraction
- Implement new behavior behind abstraction
- Switch via configuration/flag
- Remove old implementation

### 3. Parallel Change
- Add new field/method alongside old
- Migrate consumers one by one
- Remove old field/method

### 4. Extract and Delegate
- Extract subset of functionality to new module
- Original delegates to new module
- Gradually move more logic to new module

---

## Context Selection Guide (v2)

### What to Include

| Document | When to Include | Why |
|----------|-----------------|-----|
| Improvement Proposal | Always (preferred) | Full `docs/work-items/IMP-*.md` for target improvement |
| CLAUDE.md | Always | Target patterns and conventions |
| Architecture | Always | Understand component relationships, current vs target |
| Data Model | Data layer refactoring | Understand entity definitions and relationships |
| Stakeholder Definition | Large-scale refactoring | Ensure refactoring aligns with product direction |
| Persona | Rarely | Only if refactoring affects user-visible behavior |

### Improvement Proposal vs Inline Scope

- **Improvement Proposal** (preferred): Use `docs/work-items/IMP-*.md`. Provides structured risk assessment, success criteria, test coverage baseline, and traceability. Generates safer, better-phased refactoring tasks.
- **Inline `<refactoring-scope>`** (fallback): Use for quick/ad-hoc refactoring when a full Improvement Proposal hasn't been written yet. Faster but less structured.

---

## Example: Service Extraction

> **Note:** This example uses the inline `<refactoring-scope>` fallback for brevity. For safer, better-phased refactoring tasks, use a full Improvement Proposal document via `<improvement-proposal>` as described in the Prompt Template above.

```xml
<task-generation-request>

<context>

<code-conventions>
## Service Pattern
- One responsibility per service
- Services injected via dependency injection
- All external calls wrapped in services

## Testing
- Services must have unit tests with mocked dependencies
- Integration tests for service interactions
</code-conventions>

<architecture>
## Current Structure
OrderController → OrderService (handles orders, payments, notifications)

## Target Structure
OrderController → OrderService → PaymentService
                              → NotificationService

## Component Details
- OrderService: Currently 1500 lines, handles order CRUD, payments, notifications
- PaymentService (new): Will handle Stripe integration
- NotificationService (new): Will handle WhatsApp and Email
</architecture>

</context>

<task-type>Refactoring</task-type>

<refactoring-scope>
**Refactoring Type:** Architecture

**Target Area:** OrderService

**Current State:**
OrderService is a 1500-line file that handles:
- Order CRUD operations
- Payment processing (Stripe integration)
- Notification sending (WhatsApp, Email)
- Pricing calculations

**Problems with Current State:**
1. God class - too many responsibilities
2. Hard to test - many dependencies
3. Hard to modify - changes risk breaking unrelated features
4. Payment and notification logic duplicated in other services

**Desired State:**
- OrderService: Only order lifecycle management
- PaymentService: All payment logic
- NotificationService: All notification logic

**Benefits:**
1. Easier to test each service in isolation
2. Payment method changes isolated to PaymentService
3. Notification channel changes isolated to NotificationService

**Trigger:** Need to add Apple Pay support, current structure makes this risky

**Affected Entities/Data:**
- Order: processPayment() and sendNotification() methods to be moved
- Payment: Will become its own service boundary
- Notification: Will become its own service boundary

**Current Test Coverage:**
- OrderService has ~60% coverage
- Payment paths well-tested, notification paths have gaps
- No integration tests between payment and notification flows
</refactoring-scope>

<request>
Generate tasks to extract PaymentService and NotificationService from OrderService.
</request>

<constraints>
- Must maintain API compatibility (OrderService public methods unchanged)
- Can be deployed incrementally
- Must have feature flag to rollback payment service extraction
</constraints>

<output-format>
[Standard format as specified above]
</output-format>

</task-generation-request>
```

---

## Safety Checklist

Before approving generated refactoring tasks:

- [ ] Tests are written/verified BEFORE refactoring begins
- [ ] Each phase leaves system in working state
- [ ] No "big bang" changes that can't be incrementally deployed
- [ ] Rollback strategy exists for each significant change
- [ ] Old code removed only after verification
- [ ] Documentation updates included
- [ ] Team review points identified
- [ ] Performance implications considered
- [ ] No functionality changes hidden in refactoring
