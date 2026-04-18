# Base Prompt Template for Claude (v2)

> **Purpose**: This is the foundational structure for all Claude task generation prompts. Use this as a starting point and customize based on task type.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for your task type
2. Use the **Output Format** section below as your deliverable structure
3. Refer to the task-specific prompt template (feature-tasks.md, bugfix-tasks.md, etc.) for specialized output formats

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, and submit to Claude.

---

## Prompt Structure (Chat Workflow)

```xml
<task-generation-request>

<context>
<!-- Include relevant documentation here using XML tags -->
<!-- v2 framework uses 10 core templates: 7 system (Persona, Stakeholder, Architecture, CLAUDE.md, Data Model, API Specification, UI Specification) + 3 work items (Feature Brief, Bug Report, Improvement Proposal) -->

<feature-brief>
<!-- PREFERRED for features: Paste full Feature Brief from docs/work-items/FEAT-XXX-name.md -->
[Paste Feature Brief content — or use <bug-report-doc> for bugs, <improvement-proposal> for improvements]
</feature-brief>

<stakeholder-definition>
[Paste stakeholder definition content - philosophy, scope lock, success metrics]
</stakeholder-definition>

<architecture>
[Paste architecture sections relevant to this task]
</architecture>

<code-conventions>
[Paste CLAUDE.md content]
</code-conventions>

<persona>
[Paste persona details if task is user-facing or needs prioritization context]
</persona>

<data-model>
[Paste relevant entity definitions from data-model.md]
</data-model>

<api-spec>
[Paste relevant endpoint definitions from api-spec.md]
</api-spec>

<ui-specification>
[Paste relevant screen specs from ui-specification.md]
</ui-specification>

</context>

<task-type>[New Feature | Bug Fix | Refactoring | Testing | Integration]</task-type>

<request>
[Describe what you need - be specific about the desired outcome]
</request>

<constraints>
- [Constraint 1: e.g., "Must be completed within existing architecture"]
- [Constraint 2: e.g., "No new dependencies allowed"]
- [Constraint 3: e.g., "Must maintain backward compatibility"]
</constraints>

<output-format>
[Specify exactly how you want the output formatted]

## Expected Output Structure

### Task List
For each task, provide:
1. **Task ID**: Sequential identifier (T-001, T-002, etc.)
2. **Title**: Short, action-oriented title
3. **Workflow**: `standard`, `mockup-first`, or `investigation-first`
4. **Description**: What needs to be done
5. **Rationale**: Why this task exists (1-2 sentences linking to requirement, business rule, or architectural need)
6. **Acceptance Criteria**: How to verify completion
7. **Dependencies**: Other task IDs this depends on
8. **Estimated Complexity**: S/M/L/XL
9. **Files to Modify**: List of likely files

### Task Order
Group tasks by:
1. Foundation/Setup tasks
2. Core implementation tasks
3. Integration tasks
4. Testing tasks
5. Documentation tasks

### Workflow Classification

- **`standard`** — implement directly, no prerequisite artifacts.
- **`mockup-first`** — generate an HTML mockup for stakeholder approval before implementing. Use for tasks that add or significantly change a user-facing screen.
- **`investigation-first`** — investigate and document root cause before proposing a fix. Use for bug investigation tasks or any task with ambiguous requirements.
</output-format>

</task-generation-request>
```

---

## Best Practices for Using This Template

### 1. Context Selection (v2 — 10 Templates)

With 10 templates (7 system + 3 work items), select based on task type:

| Task Type | Always Include | Include If Relevant |
|-----------|---------------|---------------------|
| New Feature | Feature Brief + Stakeholder + CLAUDE.md | Data Model, API Spec, UI Spec, Persona, Architecture |
| Bug Fix | Bug Report + CLAUDE.md | Architecture, Data Model, API Spec |
| Refactoring | Improvement Proposal + CLAUDE.md + Architecture | Data Model, Stakeholder |
| Testing | CLAUDE.md | Architecture, API Spec, UI Spec |
| Integration | Architecture + CLAUDE.md + Data Model + API Spec | Stakeholder |
| Prioritization | Work Items + Stakeholder + Persona | Architecture |
| UI Mockup | UI Spec + CLAUDE.md | API Spec, Persona |

**Rule of thumb**: Work item documents (Feature Brief, Bug Report, Improvement Proposal) are the primary input for task generation — they describe *what* to do. CLAUDE.md is almost always required for *how* to do it. Add Data Model + API Spec for features involving entities/endpoints. Add UI Spec for user-facing features. Add Architecture for structural tasks, Stakeholder for scope questions, Persona for user-facing decisions.

### 2. Request Clarity

**Be specific about:**
- The exact outcome you want
- What "done" looks like
- Any decisions already made
- What you DON'T want

**Example - Vague Request:**
```
Add user authentication to the app
```

**Example - Clear Request:**
```
Implement email/password authentication with the following:
- Registration endpoint with email verification
- Login endpoint returning JWT tokens
- Password reset flow via email
- Middleware for protecting routes
- Following patterns in CLAUDE.md for error handling
```

### 3. Constraints

**Always specify:**
- Technology constraints (must use X, cannot use Y)
- Time/complexity constraints (MVP only, full implementation)
- Compatibility requirements (must work with existing X)
- Security requirements (must validate X, must not expose Y)

### 4. Output Format

**Be explicit about:**
- Task granularity (how detailed?)
- Grouping preferences
- Priority indicators needed
- Dependency mapping requirements

---

## Context Loading Patterns

### Pattern 1: Full Document Inclusion
For small documents (<2000 tokens), include the full content.

```xml
<stakeholder-definition>
# Product Stakeholder Definition
[Full document content...]
</stakeholder-definition>
```

### Pattern 2: Section Extraction
For large documents, extract only relevant sections.

```xml
<architecture-excerpt>
## Order Service
[Order service section only]

## Integration Points
[Only relevant integration section]
</architecture-excerpt>
```

### Pattern 3: Summary + Reference
For very large documents, provide summary and offer to read more.

```xml
<architecture-summary>
The system uses a microservices architecture with:
- API Gateway (Express.js)
- Order Service (handles all ordering logic)
- Notification Service (WhatsApp integration)
- PostgreSQL for persistence

For this task, the Order Service is most relevant.
</architecture-summary>
```

---

## Prompting Tips for Claude

### 1. Leverage Claude's Reasoning
```
Before generating tasks, first:
1. Analyze the requirements
2. Identify potential edge cases
3. Consider dependencies between components
4. Then generate the task list
```

### 2. Ask for Clarification
```
If any requirements are ambiguous or you need more information
to generate accurate tasks, list your questions before proceeding.
```

### 3. Request Alternatives
```
For any task where multiple approaches are viable, briefly note
the alternatives and which you recommend.
```

### 4. Include Rationale
```
For each major task, include a brief rationale explaining why
this task is necessary and how it contributes to the goal.
```

---

## Example Usage

```xml
<task-generation-request>

<context>
<stakeholder-definition>
## Product Philosophy
- Conversation-first: Feels like chat, not a form
- Keyboard-last: Typing only when unavoidable

## Scope Lock (V1)
Included: Pizza ordering, Multi-pizza cart, Visual guidance
Excluded: Discounts, User accounts, Loyalty programs
</stakeholder-definition>

<code-conventions>
## File Structure
- Components in /src/components/
- Services in /src/services/
- Types in /src/types/

## Patterns
- Zod for validation
- Custom hooks for business logic
</code-conventions>
</context>

<task-type>New Feature</task-type>

<request>
Generate implementation tasks for adding a "reorder previous order"
feature. Users should be able to see their last 3 orders and
reorder any of them with a single tap.
</request>

<constraints>
- Must work within existing WhatsApp Flows framework
- Cannot require user authentication (use phone number from WhatsApp)
- Must respect current menu/pricing (no historical prices)
- Order history limited to last 30 days
</constraints>

<output-format>
Provide tasks in the following format:
- Group by implementation phase
- Include acceptance criteria for each task
- Mark dependencies between tasks
- Estimate complexity (S/M/L)
</output-format>

</task-generation-request>
```
