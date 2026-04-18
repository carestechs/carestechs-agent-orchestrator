# Feature Task Generation Prompt (v2)

> **Purpose**: Generate implementation tasks for a new feature. Use this prompt when you have a Feature Brief (or feature idea) and need to break it down into actionable development tasks.
>
> **v2 Note**: This version uses 10 core templates (7 system templates + 3 work item templates). Features should be described in a **Feature Brief** (`docs/work-items/FEAT-*.md`) before task generation. The inline `<feature-summary>` is still supported as a fallback for quick/ad-hoc usage.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "New feature"
2. Read the Feature Brief from `docs/work-items/FEAT-*.md` for the target feature. If no Feature Brief exists, gather the feature details from the user and use the inline `<feature-summary>` fallback
3. Use the **Output Format** section below as your deliverable structure
4. Apply the **Constraints** and **Post-Generation Checklist** to shape your output

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<task-generation-request>

<context>

<stakeholder-definition>
<!-- REQUIRED: Include product philosophy, guiding principles, and scope lock -->
[Paste relevant sections from stakeholder definition]
</stakeholder-definition>

<code-conventions>
<!-- REQUIRED: Include CLAUDE.md for coding standards -->
[Paste CLAUDE.md content]
</code-conventions>

<architecture>
<!-- RECOMMENDED: Include if feature touches multiple components -->
[Paste relevant architecture sections]
</architecture>

<data-model>
<!-- RECOMMENDED: Include relevant entity definitions for the feature -->
[Paste entity definitions, fields, relationships from data-model.md]
</data-model>

<api-spec>
<!-- RECOMMENDED: Include relevant endpoint definitions for the feature -->
[Paste endpoint definitions, DTOs, status codes from api-spec.md]
</api-spec>

<ui-specification>
<!-- RECOMMENDED: Include for user-facing features — screen specs, component hierarchy, interactions, states -->
[Paste relevant screen specifications, component hierarchy, interactions from ui-specification.md]
</ui-specification>

<persona>
<!-- OPTIONAL: Include if feature is user-facing -->
[Paste persona details]
</persona>

</context>

<task-type>New Feature</task-type>

<feature-brief>
<!-- PREFERRED: Paste the full Feature Brief document (from docs/work-items/FEAT-XXX-name.md) -->
<!-- The Feature Brief template provides structured fields for scope, acceptance criteria,
     entity/API/UI impact, edge cases, constraints, and traceability.
     See .ai-framework/templates/feature-brief.md for the full template. -->
[Paste full Feature Brief content]
</feature-brief>

<!-- FALLBACK: If no Feature Brief exists, use this inline summary instead.
     Remove the <feature-brief> block above and uncomment this block. -->
<!--
<feature-summary>
Feature Name: [Name]
User Story: As a [persona], I want to [action], so that [benefit]
Primary Goal: [One sentence describing success]

Acceptance Criteria:
- [AC-1: Testable criterion]
- [AC-2: Testable criterion]
- [AC-3: Testable criterion]

Key Entities Involved:
- [Entity 1]: [Brief description of attributes and rules]
- [Entity 2]: [Brief description]

Edge Cases:
- [Edge case 1]
- [Edge case 2]
</feature-summary>
-->

<request>
Generate a complete task breakdown for implementing this feature.

Tasks should:
1. Cover all acceptance criteria
2. Include necessary database/data model changes
3. Include API endpoints if applicable
4. Include frontend/UI implementation
5. Include error handling and edge cases
6. Include testing at all levels (unit, integration, e2e)
7. Consider monitoring/logging requirements

Do not include:
- Tasks outside the defined scope (see stakeholder scope lock)
- Over-engineered solutions beyond the requirements
</request>

<constraints>
<!-- Add project-specific constraints -->
- Technology stack: [List technologies that must be used]
- Timeline constraint: [If any]
- Dependency constraints: [External factors]
- Security requirements: [Specific security needs]
</constraints>

<output-format>
## Task Output Format

For each task, provide:

```
### T-[XXX]: [Task Title]

**Type:** [Backend | Frontend | Database | Testing | DevOps | Documentation]
**Workflow:** [standard | mockup-first]
**Complexity:** [S | M | L | XL]
**Dependencies:** [T-XXX, T-YYY or "None"]

**Description:**
[2-3 sentences describing what needs to be done]

**Rationale:**
[1-2 sentences: why this task exists — which requirement, business rule, or acceptance criterion it addresses]

**Acceptance Criteria:**
- [ ] [Testable criterion 1]
- [ ] [Testable criterion 2]
- [ ] [Testable criterion 3]

**Files to Modify/Create:**
- [file/path/example.ts] - [what changes]
- [file/path/other.ts] - [what changes]

**Technical Notes:**
[Any implementation guidance, patterns to follow, or gotchas to avoid]
```

## Workflow Classification

Set the **Workflow** field on each task using these rules:

- **`mockup-first`** — Type is Frontend AND the task adds a new user-facing screen or significantly changes an existing screen layout. **Exception:** standard CRUD screens (list/detail/form) or screens that follow an already-approved mockup pattern.
- **`standard`** — all other tasks.

When a task is marked `mockup-first`, its description should note which screen needs a mockup and reference `.ai-framework/prompts/mockup-generation.md`.

## Task Grouping

Group tasks in this order:
1. **Foundation** - Database, models, types
2. **Backend** - Services, API endpoints
3. **Frontend** - Components, pages, state
4. **Integration** - Connecting pieces
5. **Testing** - Unit, integration, e2e tests
6. **Polish** - Error handling, edge cases, logging

## Summary Section

After all tasks, provide:
- Total task count by type
- Estimated complexity distribution
- Critical path (longest dependency chain)
- Risks or open questions discovered during analysis
</output-format>

</task-generation-request>
```

---

## Context Selection Guide (v2)

### What to Include

| Document | When to Include | What to Include |
|----------|-----------------|-----------------|
| Feature Brief | Always (preferred) | Full `docs/work-items/FEAT-*.md` for target feature |
| Stakeholder Definition | Always | Philosophy, principles, scope lock, success metrics |
| CLAUDE.md | Always | Full document |
| Data Model | Features involving entities | Relevant entity definitions, fields, relationships |
| API Specification | Features with API endpoints | Relevant endpoint definitions, DTOs, status codes |
| UI Specification | User-facing features | Relevant screen specs, component hierarchy, interactions, states |
| Architecture | Multi-component features | Affected components, data flow |
| Persona | User-facing features | Pain points, behavior, success criteria |

### Feature Brief vs Inline Summary

- **Feature Brief** (preferred): Use `docs/work-items/FEAT-*.md`. Provides structured scope, traceability, entity/API/UI impact assessment, and constraint documentation. Generates higher-quality tasks.
- **Inline `<feature-summary>`** (fallback): Use for quick/ad-hoc features when a full Feature Brief hasn't been written yet. Faster but less structured.

---

## Example: Pizza Ordering Feature

> **Note:** This example uses the inline `<feature-summary>` fallback for brevity. For higher-quality task generation, use a full Feature Brief document via `<feature-brief>` as described in the Prompt Template above.

```xml
<task-generation-request>

<context>

<stakeholder-definition>
## Product Philosophy
- Conversation-first: Feels like chat, not a form
- Keyboard-last: Typing only when unavoidable
- Progressive disclosure: Show only what's needed next

## Scope Lock (V1)
Included: Pizza ordering, Multi-pizza cart, Visual guidance
Excluded: Discounts, User accounts, Loyalty programs
</stakeholder-definition>

<code-conventions>
## File Structure
- Components in /src/components/{FeatureName}/
- Services in /src/services/
- Types in /src/types/

## Patterns
- Use Zod for validation
- Custom hooks for business logic
- Error boundaries for component errors
</code-conventions>

<architecture>
## Relevant Components
- Order Service: Handles order lifecycle
- Menu Service: Provides available items and pricing
- WhatsApp Flows: UI layer for all interactions

## Data Flow
User → WhatsApp → Flow Engine → Order Service → Database
</architecture>

</context>

<task-type>New Feature</task-type>

<feature-summary>
Feature Name: Half-and-Half Pizza Selection
User Story: As a pizza customer, I want to select two different flavors for my pizza (half-and-half), so that I can enjoy variety in one order.
Primary Goal: Enable two-flavor pizza orders without keyboard input

Acceptance Criteria:
- AC-1: User can select "Half & Half" option after choosing size
- AC-2: System shows two flavor selection steps sequentially
- AC-3: Both flavors displayed in order review
- AC-4: Price is average of both flavors + half-half surcharge
- AC-5: Option only available for Medium and Large sizes

Key Entities Involved:
- Pizza: size (enum), flavors (Flavor[], 1-3 based on size), base (enum)
- Business Rules: Personal=1 flavor, Standard=1-2, Party=1-3

Edge Cases:
- Same flavor selected for both halves (allow, no surcharge)
- User changes size after selecting flavors (reset selections)
</feature-summary>

<request>
Generate implementation tasks for the half-and-half pizza feature.
Ensure all acceptance criteria are covered and follow the existing
WhatsApp Flows pattern used for single-flavor selection.
</request>

<constraints>
- Must work within WhatsApp Flows JSON structure
- Cannot add new external dependencies
- Must maintain existing cart functionality
- Pricing calculation must be server-side
</constraints>

<output-format>
[Standard format as specified above]
</output-format>

</task-generation-request>
```

---

## Post-Generation Checklist

After Claude generates tasks, verify:

- [ ] All acceptance criteria have corresponding tasks
- [ ] Database/model changes come before code that uses them
- [ ] API endpoints are defined before frontend integration
- [ ] Error handling tasks exist for important error scenarios
- [ ] Testing tasks cover happy path and edge cases
- [ ] No tasks violate stakeholder scope lock
- [ ] Complexity estimates seem reasonable
- [ ] Dependencies form a valid DAG (no cycles)
- [ ] Critical path is identified and sensible
