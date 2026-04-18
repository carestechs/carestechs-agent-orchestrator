# CLAUDE.md Template

> **Purpose**: This template is for creating a CLAUDE.md file - a code conventions and project context document that helps AI assistants understand how to work with your codebase. Place the generated file in your project root.

---

# CLAUDE.md

> This file provides guidance to Claude Code (or any AI assistant) when working with this codebase.

## Pre-Work Checklist

Before generating specs, tasks, mockups, or implementation plans, you MUST follow these steps:

1. **Identify the task type** using the routing table in the "AI-Assisted Development Framework" section below. **If working on a specific task (T-XXX), check its Workflow field** and follow the Workflow Enforcement rules before starting implementation.
2. **Read the required files** listed in the routing table for your task type — read them directly, do not ask the user to paste them.
3. **Read the prompt template** from `.ai-framework/prompts/` — this defines the required sections, structure, and quality criteria for the deliverable.
4. **Derive structure from the prompt template, NOT from existing output files.** Specs, tasks, and plans are *outputs* — they may reflect an older version of the framework. The prompt templates in `.ai-framework/prompts/` are the authoritative source for format and structure.

---

## Project Overview

[1-2 sentences describing what this project does and its primary purpose]

**Tech Stack**: [Primary technologies, e.g., "TypeScript, React, Node.js, PostgreSQL"]

**Repository Type**: [Monorepo / Single application / Library / etc.]

---

## Quick Reference

### Common Commands

```bash
# Development
[command to start dev server]

# Testing
[command to run tests]
[command to run tests with coverage]

# Building
[command to build]

# Linting/Formatting
[command to lint]
[command to format]

# Database (if applicable)
[command to run migrations]
[command to seed database]
```

### Key Directories

```
/
├── [dir1]/              # [Purpose]
├── [dir2]/              # [Purpose]
├── [dir3]/              # [Purpose]
│   ├── [subdir1]/       # [Purpose]
│   └── [subdir2]/       # [Purpose]
├── [dir4]/              # [Purpose]
└── [config files]       # [Purpose]
```

---

## Code Style & Conventions

### General Principles

- [Principle 1, e.g., "Prefer composition over inheritance"]
- [Principle 2, e.g., "Keep functions small and focused"]
- [Principle 3, e.g., "Write self-documenting code, minimize comments"]
- [Principle 4, e.g., "Fail fast, validate at boundaries"]

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Files (components) | [PascalCase/kebab-case/etc.] | `[Example.tsx]` |
| Files (utilities) | [Convention] | `[example-utils.ts]` |
| Variables | [camelCase/snake_case/etc.] | `[userName]` |
| Constants | [Convention] | `[MAX_RETRY_COUNT]` |
| Functions | [Convention] | `[getUserById]` |
| Classes | [Convention] | `[UserService]` |
| Interfaces/Types | [Convention] | `[UserProfile]` |

### File Organization

**Component files should follow this structure:**
```typescript
// 1. Imports (external, then internal, then styles)
// 2. Types/Interfaces
// 3. Constants
// 4. Component definition
// 5. Helper functions (if component-specific)
// 6. Export
```

**Service/utility files should follow this structure:**
```typescript
// 1. Imports
// 2. Types/Interfaces
// 3. Constants
// 4. Main exports
// 5. Helper functions (private)
```

---

## Patterns & Anti-Patterns

### Preferred Patterns

#### [Pattern Name 1]

```typescript
// DO: [Description of preferred approach]
[Code example showing the right way]
```

#### [Pattern Name 2]

```typescript
// DO: [Description]
[Code example]
```

### Anti-Patterns to Avoid

#### [Anti-Pattern 1]

```typescript
// DON'T: [Description of what to avoid and why]
[Code example showing the wrong way]

// DO: [The correct alternative]
[Code example showing the right way]
```

#### [Anti-Pattern 2]

```typescript
// DON'T: [Description]
[Bad example]

// DO: [Correct approach]
[Good example]
```

### Design Patterns

> *If DDRs were compiled using `compile-ddrs.md`, this section is pre-filled from component, interaction, accessibility, states, and responsive DDR constraints. Add project-specific design patterns below the compiled entries.*

#### Design Patterns to Follow

<!-- TODO: Add design patterns from DDR compilation or manually. -->
<!-- Format: - **[Short label]:** [constraint text] (from: [ddr-filename]) -->

```html
<!-- Example pattern from DDR compilation:
     Buttons must include focus-visible ring for keyboard accessibility -->
<button class="px-4 py-2 bg-primary text-on-primary rounded-md
               focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-primary">
  Save
</button>
```

#### Design Anti-Patterns to Avoid

<!-- TODO: Add design anti-patterns from DDR compilation or manually. -->
<!-- Format: - **[Short label]:** [constraint text] (from: [ddr-filename]) -->

```html
<!-- Example anti-pattern from DDR compilation:
     NEVER use raw hex values in markup — use design tokens -->

<!-- DON'T -->
<div class="bg-[#2563EB] text-[#ffffff]">Bad</div>

<!-- DO -->
<div class="bg-primary text-on-primary">Good</div>
```

---

## Error Handling

### Error Handling Strategy

[Describe overall approach: custom error classes, error boundaries, logging, etc.]

### Standard Error Pattern

```typescript
// [Show the standard way to handle errors in this codebase]
[Code example]
```

### Error Types

| Error Type | When to Use | Handling |
|------------|-------------|----------|
| [ErrorType1] | [When] | [How to handle] |
| [ErrorType2] | [When] | [Handling] |
| [ErrorType3] | [When] | [Handling] |

---

## Testing Conventions

### Test File Location

- Unit tests: `[location, e.g., "Adjacent to source: Component.test.tsx"]`
- Integration tests: `[location, e.g., "__tests__/integration/"]`
- E2E tests: `[location, e.g., "e2e/"]`

### Test Structure

```typescript
describe('[Component/Function name]', () => {
  // Setup (beforeEach/beforeAll if needed)

  describe('[method or scenario]', () => {
    it('should [expected behavior] when [condition]', () => {
      // Arrange
      // Act
      // Assert
    });
  });
});
```

### Testing Priorities

1. [Priority 1, e.g., "Business logic functions"]
2. [Priority 2, e.g., "API handlers"]
3. [Priority 3, e.g., "Complex UI components"]
4. [Priority 4, e.g., "Utility functions"]

### What NOT to Test

- [Thing 1, e.g., "Framework code"]
- [Thing 2, e.g., "Simple getters/setters"]
- [Thing 3, e.g., "Third-party libraries"]

---

## API Conventions (if applicable)

### Endpoint Naming

```
[HTTP Method] /[resource]/[action]

Examples:
GET    /users           # List users
GET    /users/:id       # Get single user
POST   /users           # Create user
PUT    /users/:id       # Update user
DELETE /users/:id       # Delete user
```

### Request/Response Format

```typescript
// Standard success response
{
  "data": [payload],
  "meta": { /* pagination, etc. */ }
}

// Standard error response
{
  "error": {
    "code": "[ERROR_CODE]",
    "message": "[Human readable message]",
    "details": [/* optional */]
  }
}
```

### Validation

[Describe validation approach: where validation happens, libraries used, etc.]

---

## Database Conventions (if applicable)

### Naming

- Tables: [Convention, e.g., "snake_case, plural (users, order_items)"]
- Columns: [Convention, e.g., "snake_case (created_at, user_id)"]
- Indexes: [Convention, e.g., "idx_table_column"]
- Foreign Keys: [Convention, e.g., "fk_table_reference"]

### Migration Guidelines

- [Guideline 1, e.g., "Migrations must be reversible"]
- [Guideline 2, e.g., "One concern per migration"]
- [Guideline 3, e.g., "Test migrations on copy of production data"]

---

## Environment & Configuration

### Environment Variables

| Variable | Purpose | Required | Default |
|----------|---------|----------|---------|
| [VAR_1] | [Purpose] | Yes/No | [Default] |
| [VAR_2] | [Purpose] | Yes/No | [Default] |
| [VAR_3] | [Purpose] | Yes/No | [Default] |

### Configuration Files

| File | Purpose |
|------|---------|
| [.env.example] | [Template for environment variables] |
| [config/default.js] | [Default configuration] |
| [tsconfig.json] | [TypeScript configuration] |

---

## Git Conventions

### Branch Naming

```
[type]/[ticket-id]-[short-description]

Examples:
feature/PROJ-123-user-authentication
bugfix/PROJ-456-fix-login-error
refactor/PROJ-789-extract-validation
```

### Commit Messages

```
[type]: [short description]

[optional body with more details]

[optional footer with ticket reference]
```

**Types**: feat, fix, refactor, test, docs, chore, style

---

## Performance Considerations

- [Consideration 1, e.g., "Memoize expensive computations"]
- [Consideration 2, e.g., "Use pagination for list endpoints"]
- [Consideration 3, e.g., "Lazy load heavy components"]
- [Consideration 4, e.g., "Index frequently queried fields"]

---

## Security Checklist

When writing code, ensure:

- [ ] [Security item 1, e.g., "User input is validated and sanitized"]
- [ ] [Security item 2, e.g., "SQL queries use parameterized statements"]
- [ ] [Security item 3, e.g., "Sensitive data is not logged"]
- [ ] [Security item 4, e.g., "Authentication is required for protected routes"]
- [ ] [Security item 5, e.g., "CORS is properly configured"]

---

## Common Gotchas

> Things that often trip people up in this codebase

1. **[Gotcha 1]**: [Explanation and how to avoid]
2. **[Gotcha 2]**: [Explanation and solution]
3. **[Gotcha 3]**: [Explanation and workaround]

---

## Related Documentation

- [Link to architectural documentation]
- [Link to API documentation]
- [Link to deployment guide]
- [Link to contributing guide]

---

## AI-Assisted Development Framework

This project includes a bundled AI framework (`.ai-framework/`) with prompt templates, context assembly guides, and documentation maintenance rules.

**If you are an AI agent (e.g., Claude Code):** Read the files listed in the routing table below directly — do not ask the user to paste them. Read the prompt template for your task type to determine the output format. For manual/chat workflows, see `.ai-framework/guides/context-compilation.md` for XML assembly instructions.

### Task Generation Routing

When asked to generate tasks, identify the task type, read the required files, then read the prompt template for output format.

| Task Type | Prompt Template | Files to Read |
|-----------|----------------|---------------|
| New feature | `.ai-framework/prompts/feature-tasks.md` | `docs/work-items/FEAT-*.md` (target feature), `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/data-model.md`, `docs/api-spec.md`, `docs/ui-specification.md` |
| Bug fix | `.ai-framework/prompts/bugfix-tasks.md` | `docs/work-items/BUG-*.md` (target bug), `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| Refactoring | `.ai-framework/prompts/refactor-tasks.md` | `docs/work-items/IMP-*.md` (target improvement), `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| Spec generation | `.ai-framework/prompts/spec-generation.md` | `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| UI spec generation | `.ai-framework/prompts/ui-spec-generation.md` | `docs/stakeholder-definition.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/api-spec.md` |
| UI mockup | `.ai-framework/prompts/mockup-generation.md` | `docs/ui-specification.md` (target screen + Design System), `CLAUDE.md` |
| ADR compilation | `.ai-framework/prompts/compile-adrs.md` | ADR files (from shared ADR repo), `.ai-framework/templates/` |
| DDR compilation | `.ai-framework/prompts/compile-ddrs.md` | DDR files (from shared DDR repo), `.ai-framework/templates/` |
| Release transition | `.ai-framework/guides/release-lifecycle.md` | `docs/stakeholder-definition.md`, `CLAUDE.md` |
| Task implementation plan | `.ai-framework/prompts/plan-generation.md` | `CLAUDE.md`, task definition, files listed in task's "Files to Modify/Create" |

**Optional context** (read only when relevant to the specific task):

| Task Type | Optional Files | When to Include |
|-----------|---------------|-----------------|
| New feature | `docs/ARCHITECTURE.md`, `docs/personas/primary-user.md` | Multi-component features, user-facing features |
| Bug fix | `docs/data-model.md`, `docs/api-spec.md`, `docs/ui-specification.md` | Data/API/UI bugs respectively |
| Refactoring | `docs/data-model.md`, `docs/stakeholder-definition.md` | Data refactors, scope questions |
| Spec generation | `docs/personas/primary-user.md` | User-facing entity/endpoint decisions |
| UI mockup | `docs/api-spec.md`, `docs/personas/primary-user.md` | Data-driven screens, content tone |
| Prioritization | `docs/work-items/FEAT-*.md`, `docs/work-items/BUG-*.md`, `docs/work-items/IMP-*.md`, `docs/stakeholder-definition.md`, `docs/personas/` | Comparing and prioritizing work items |

**Work Items** (`docs/work-items/`): Feature Briefs, Bug Reports, and Improvement Proposals are the preferred input for task generation. If no work item document exists for a task, the prompts support inline fallbacks — but structured work items produce higher-quality task breakdowns.

### Workflow Enforcement

Each task definition includes a **Workflow** field. Before starting any task, check its Workflow value and follow the required steps:

| Workflow | Required Steps Before Implementation |
|----------|--------------------------------------|
| `standard` | 1. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 2. Implement following the plan. |
| `mockup-first` | 1. Generate an HTML mockup using `.ai-framework/prompts/mockup-generation.md`. Get stakeholder approval. See `.ai-framework/guides/getting-started.md` Step 7.5. 2. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 3. Implement following the plan. |
| `investigation-first` | 1. Complete all investigation steps in the task. Document findings (root cause, affected areas). 2. Generate an implementation plan using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`. 3. Implement following the plan. |

**If a task has no Workflow field** (legacy tasks), classify it yourself:
- Type is Frontend + adds/changes a screen → treat as `mockup-first`
- Task requires root cause analysis → treat as `investigation-first`
- Otherwise → treat as `standard`

### Development Pipeline

When implementing tasks from a generated task list, follow this sequence for **each task**:

1. **Pick a task** from the task list (respect dependency order).
2. **Check its Workflow field** and complete any prerequisites (see Workflow Enforcement above).
3. **Generate an implementation plan** using `.ai-framework/prompts/plan-generation.md`. Output: `plans/plan-T-XXX-short-title.md`.
4. **Implement** following the steps in the plan.
5. **Verify** the acceptance criteria from the task definition are met.

This sequence applies to every task. The plan file is a developer-facing artifact — it bridges "what to do" (task definition) and "how to do it" (exact code changes).

### Context Assembly Rules

Read files in **Cone of Context** order — broad (strategic) to narrow (tactical):

| Layer | Files | Purpose |
|-------|-------|---------|
| Strategic | `docs/stakeholder-definition.md`, `docs/personas/primary-user.md` | Why? For whom? What's in scope? |
| Architectural | `docs/ARCHITECTURE.md` | What is the system? How is it structured? |
| Specification | `docs/data-model.md`, `docs/api-spec.md` | What are the entities and API contracts? |
| UI | `docs/ui-specification.md` | What do screens look like? What are the components? |
| Work Items | `docs/work-items/FEAT-*.md`, `docs/work-items/BUG-*.md`, `docs/work-items/IMP-*.md` | What specific work to do? Features, bugs, improvements |
| Implementation | `CLAUDE.md` | How do we build things? What are the conventions? |

**For large documents:** Read only the sections relevant to the task (e.g., for a task about labels, read only the Label entity from `data-model.md` and label endpoints from `api-spec.md`). Quality over quantity.

For the full context selection matrix and XML assembly examples, see `.ai-framework/guides/context-compilation.md`.

### Documentation Maintenance Discipline

When code changes happen, check which docs need updating per `.ai-framework/guides/maintenance.md`. Include doc updates in the same PR as the code change.

| Code Change | Document to Update |
|-------------|-------------------|
| New entity or field | `docs/data-model.md` |
| New/changed endpoint or DTO | `docs/api-spec.md` |
| New/changed screen or component | `docs/ui-specification.md` |
| New component or service | `docs/ARCHITECTURE.md` |
| New pattern or convention | `CLAUDE.md` |
| Scope or strategy change | `docs/stakeholder-definition.md` |
| Design token or screen layout change | `mockups/` (affected screens) |
| DDR updated in shared repo | Re-run DDR compilation, update Component Examples + CLAUDE.md Design Patterns |
| Feature tasks completed | `docs/work-items/FEAT-*.md` — update Status to "Completed" |
| Bug resolved | `docs/work-items/BUG-*.md` — update Status to "Resolved" |
| Improvement completed | `docs/work-items/IMP-*.md` — update Status to "Completed" |

**Changelog rule:** Every update to `data-model.md`, `api-spec.md`, `ARCHITECTURE.md`, or `ui-specification.md` must include a changelog entry at the bottom of the document. See `.ai-framework/guides/maintenance.md` for format.

### Framework Reference

For deeper reading on the full workflow and rules:

- `.ai-framework/guides/getting-started.md` — full workflow from docs to task generation
- `.ai-framework/guides/context-compilation.md` — context assembly details and task-type matrix
- `.ai-framework/guides/maintenance.md` — doc update triggers and review checklists
