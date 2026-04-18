# Task Implementation Plan Prompt (v1)

> **Purpose**: Generate a per-task implementation plan that bridges "what to do" (task definition) and "how to do it" (exact code changes). Plans are developer-facing artifacts that decompose a single task into ordered implementation steps with file-level specificity.
>
> **When to use**: After picking a task (T-XXX) from a generated task list and completing any workflow prerequisites (mockup approval, investigation). Use before starting implementation — for every task, regardless of complexity.
>
> **When to skip**: Only skip for trivial single-line fixes where the task definition already contains all necessary implementation detail (e.g., "change constant X from 5 to 10 in file Y").
>
> **v1 Note**: This is a developer workflow artifact — not a stakeholder-facing document. Plans live in `plans/` and are consumed by the implementing developer or AI agent.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the task definition (T-XXX) from the task list file
2. Read `CLAUDE.md` for project conventions and patterns
3. Read the files listed in the task's "Files to Modify/Create" to understand current code state
4. Read relevant specs based on task type (see Context Selection Guide below)
5. Use the **Output Format** section below for the plan structure
6. Apply the **Workflow** and **Post-Generation Checklist** to validate the plan

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, fill in the `<plan-scope>`, and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<plan-generation-request>

<context>

<task-definition>
<!-- REQUIRED: The full task block (T-XXX) from the task list file,
     including all fields: Title, Type, Complexity, Workflow, Description,
     Files to Modify/Create, Acceptance Criteria, Dependencies. -->
[Paste the complete task definition block]
</task-definition>

<code-conventions>
<!-- REQUIRED: Project conventions, patterns, and anti-patterns from CLAUDE.md.
     Ensures the plan follows established codebase patterns. -->
[Paste relevant sections from CLAUDE.md]
</code-conventions>

<current-code>
<!-- REQUIRED: The current content of files listed in the task's
     "Files to Modify/Create" section. Enables step-level specificity. -->
[Paste the current content of each file the task touches]
</current-code>

<data-model>
<!-- CONDITIONAL: Include for tasks that touch entities, database, or data flow.
     Provides entity shapes, relationships, and constraints. -->
[Paste relevant entity sections from docs/data-model.md]
</data-model>

<api-spec>
<!-- CONDITIONAL: Include for tasks that touch API endpoints or DTOs.
     Provides endpoint contracts and response shapes. -->
[Paste relevant endpoint sections from docs/api-spec.md]
</api-spec>

<ui-specification>
<!-- CONDITIONAL: Include for frontend tasks that touch screens or components.
     Provides component hierarchy, states, and layout. -->
[Paste relevant screen sections from docs/ui-specification.md]
</ui-specification>

<architecture>
<!-- CONDITIONAL: Include for tasks that add new components, services, or
     cross-cutting concerns. Provides system structure and module boundaries. -->
[Paste relevant sections from docs/ARCHITECTURE.md]
</architecture>

</context>

<plan-scope>
Task ID: [T-XXX]
Task Title: [from task definition]
Task Definition: [full task block including all fields]
</plan-scope>

<guidance>
## Plan Generation Rules

1. **One plan per task** — each plan addresses exactly one task (T-XXX)
2. **Steps are ordered** — implementation steps follow a logical dependency order (e.g., create types before services that use them)
3. **File-level specificity** — every step names the exact file and action (Create / Modify / Delete)
4. **Reference conventions** — cite patterns from CLAUDE.md when a step must follow a specific convention
5. **No ambiguity** — each step should be specific enough for a developer to implement without further questions
6. **Edge cases surfaced** — identify boundary conditions, error states, and integration risks
7. **Acceptance-linked verification** — map each acceptance criterion from the task to a concrete verification step
</guidance>

<output-format>
## Plan File Structure

Generate a markdown file with this structure:

```markdown
# Implementation Plan: T-XXX — [Task Title]

## Task Reference
- **Task ID:** T-XXX
- **Type:** [from task]
- **Workflow:** [from task]
- **Complexity:** [from task]
- **Rationale:** [from task]

## Overview
[2-3 sentences: what this task accomplishes and why, in plain language]

## Implementation Steps

### Step 1: [Action verb — e.g., "Create the delivery fee service"]
**File:** `path/to/file.ts`
**Action:** [Create | Modify | Delete]
[Detailed description of what to do — specific enough for a developer
to implement without ambiguity. Reference patterns from CLAUDE.md.]

### Step 2: [Action]
**File:** `path/to/file.ts`
**Action:** [Create | Modify | Delete]
[Detailed description]

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `path/to/file.ts` | Modify | [one-line description] |

## Edge Cases & Risks
- [Edge case and how to handle it]
- [Risk and mitigation]

## Acceptance Verification
- [ ] [How to verify AC-1 from the task]
- [ ] [How to verify AC-2]
```

**File naming:** `plans/plan-T-XXX-short-title.md` (lowercase, kebab-case for the short title)
- Example: `plans/plan-t-035-delivery-fee-service.md`
</output-format>

</plan-generation-request>
```

---

## Context Selection Guide

### What to Include

Select context based on the **task type** that generated the task:

| Task Origin | Required Context | Conditional Context |
|-------------|-----------------|---------------------|
| All tasks | `CLAUDE.md`, task definition, files listed in "Files to Modify/Create" | — |
| Feature tasks | — | `docs/data-model.md`, `docs/api-spec.md`, `docs/ui-specification.md` (based on which layers the task touches) |
| Bugfix tasks | — | `docs/ARCHITECTURE.md` (for understanding system structure) |
| Refactoring tasks | — | `docs/ARCHITECTURE.md` (for understanding module boundaries) |
| Frontend tasks | — | `docs/ui-specification.md` (target screen + Design System) |

### What NOT to Include

- Full documents — extract only the sections relevant to the task's files and domain
- Stakeholder definition — plans are implementation-level, not strategic
- Persona documents — plans deal with code, not user experience
- Other task definitions — each plan addresses one task only

---

## Workflow

### Step 1: Pick a Task

Select the next task from the task list, respecting dependency order. Verify all blocking tasks are complete.

### Step 2: Complete Workflow Prerequisites

Check the task's **Workflow** field and complete any prerequisites before planning:
- `standard` — proceed directly to Step 3
- `mockup-first` — ensure mockup is generated and approved
- `investigation-first` — ensure investigation is complete and findings are documented

### Step 3: Assemble Context

Read the task definition, `CLAUDE.md`, and the files listed in "Files to Modify/Create". Add conditional context based on which layers the task touches (see Context Selection Guide).

### Step 4: Generate Plan

Run the prompt above. The AI will produce a plan file.

### Step 5: Review the Plan

Verify the plan covers all acceptance criteria, steps are in dependency order, and file references are accurate.

### Step 6: Implement

Follow the plan's implementation steps in order. Mark each acceptance verification item as complete.

---

## Example: Feature Task Plan

```xml
<plan-generation-request>

<context>

<task-definition>
### T-035: Add delivery fee calculation to order summary
- **Type:** Feature (Backend + Frontend)
- **Complexity:** Medium
- **Workflow:** mockup-first
- **Description:** Calculate a delivery fee based on distance and display it in the order summary. Fee = base rate + per-km charge. Free delivery over $50.
- **Files to Modify/Create:**
  - Create: `src/services/delivery-fee.service.ts`
  - Modify: `src/components/OrderSummary.tsx`
  - Modify: `src/types/order.ts`
- **Acceptance Criteria:**
  - AC-1: Delivery fee appears as a line item in the order summary
  - AC-2: Fee is $0 when subtotal exceeds $50
  - AC-3: Fee calculation uses base rate + per-km charge from config
- **Dependencies:** T-034 (order summary component exists)
</task-definition>

<code-conventions>
- Use TypeScript strict mode
- Services follow the repository pattern
- All monetary values in cents (integer)
- Prefer named exports
</code-conventions>

<current-code>
// src/components/OrderSummary.tsx — current version
// src/types/order.ts — current version
[Paste current file contents]
</current-code>

</context>

<plan-scope>
Task ID: T-035
Task Title: Add delivery fee calculation to order summary
Task Definition: [full task block as shown above]
</plan-scope>

</plan-generation-request>
```

**Output:** `plans/plan-t-035-delivery-fee-service.md`

---

## Post-Generation Checklist

After the AI generates a plan file, verify:

- [ ] Plan file is saved to `plans/plan-T-XXX-short-title.md` with correct naming
- [ ] Task Reference section matches the task definition exactly
- [ ] Overview clearly explains what and why in plain language
- [ ] Every file in the task's "Files to Modify/Create" is covered by at least one step
- [ ] Steps are in dependency order (types before services, services before components)
- [ ] Each step names an exact file path and action (Create / Modify / Delete)
- [ ] Steps reference CLAUDE.md conventions where applicable
- [ ] Files Affected table is complete and consistent with the steps
- [ ] Every acceptance criterion from the task maps to at least one verification item
- [ ] Edge cases and risks are identified (not just a placeholder section)
