# Context Compilation Guide (v2)

> **Purpose**: This guide explains how to assemble the right documentation context for AI task generation using the 10 core templates: 7 system templates (Persona, Stakeholder Definition, Architecture, CLAUDE.md, Data Model, API Specification, UI Specification) and 3 work item templates (Feature Brief, Bug Report, Improvement Proposal).

---

## The "Cone of Context" Principle

Context should be provided in layers, from broad (strategic) to narrow (tactical). The AI needs just enough context to understand the "why" and "how" without being overwhelmed.

```
┌─────────────────────────────────────────────────────────────────┐
│                    STRATEGIC CONTEXT                            │
│  Stakeholder Definition, Persona                                │
│  "Why? For whom? What's in scope?"                              │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ARCHITECTURAL CONTEXT                          │
│  ARCHITECTURE.md                                                │
│  "What is the system? How is it structured?"                    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SPECIFICATION CONTEXT                         │
│  Data Model, API Specification                                  │
│  "What are the entities? What are the API contracts?"           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      UI CONTEXT                                 │
│  UI Specification                                               │
│  "What do screens look like? What are the components?"          │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   WORK ITEMS CONTEXT                            │
│  Feature Brief, Bug Report, Improvement Proposal                │
│  "What specific work to do? What features/bugs/improvements?"   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 IMPLEMENTATION CONTEXT                          │
│  CLAUDE.md                                                      │
│  "How do we build things? What are the conventions?"            │
└─────────────────────────────────────────────────────────────────┘
```

**Key Insight**: Start narrow and add breadth only when needed. Too much context dilutes focus; too little causes misalignment.

---

## Context Selection by Task Type

### 1. New Feature Implementation

**Goal**: Generate tasks that implement a feature aligned with product vision.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Feature Brief | Always (preferred) | Full `docs/work-items/FEAT-*.md` — scope, ACs, impact |
| Required | Stakeholder Definition | Always | Philosophy, principles, scope lock |
| Required | CLAUDE.md | Always | Full document |
| Recommended | Data Model | Features involving entities | Relevant entity definitions, relationships |
| Recommended | API Specification | Features with API endpoints | Relevant endpoint definitions, DTOs |
| Recommended | UI Specification | User-facing features | Relevant screen specs, component hierarchy, interactions |
| Optional | Persona | User-facing features | Relevant persona |
| Optional | Architecture | Multi-component features | Affected components |

**Example Assembly**:
```xml
<context>
  <feature-brief>
    [Full Feature Brief document from docs/work-items/FEAT-XXX-name.md]
  </feature-brief>

  <stakeholder-definition>
    [Sections: Philosophy, Principles, Scope Lock, Success Metrics]
  </stakeholder-definition>

  <code-conventions>
    [Full CLAUDE.md]
  </code-conventions>

  <data-model>
    [Relevant entity definitions, fields, relationships]
  </data-model>

  <api-spec>
    [Relevant endpoint definitions, DTOs, status codes]
  </api-spec>

  <ui-specification>
    [If user-facing: relevant screen specs, component hierarchy, interactions, states]
  </ui-specification>

  <persona>
    [If user-facing: relevant persona details]
  </persona>

  <architecture>
    [If multi-component: affected components and data flow]
  </architecture>
</context>
```

---

### 2. Bug Fix

**Goal**: Generate investigation and fix tasks that address root cause.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Bug Report | Always (preferred) | Full `docs/work-items/BUG-*.md` — reproduction, evidence, impact |
| Required | CLAUDE.md | Always | Full document |
| Optional | Architecture | Multi-component bug | Affected components |
| Optional | Data Model | Data-related bugs | Affected entity definitions |
| Optional | API Specification | API-related bugs | Affected endpoint definitions |
| Optional | Stakeholder Definition | Scope clarification needed | Scope lock, principles |

**Example Assembly**:
```xml
<context>
  <bug-report-doc>
    [Full Bug Report document from docs/work-items/BUG-XXX-name.md]
  </bug-report-doc>

  <code-conventions>
    [Full CLAUDE.md]
  </code-conventions>

  <architecture>
    [Components involved in the bug]
    [Data flow through affected area]
  </architecture>

  <data-model>
    [If data-related: affected entity definitions]
  </data-model>
</context>
```

---

### 3. Refactoring

**Goal**: Generate safe, incremental refactoring tasks that maintain functionality.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Improvement Proposal | Always (preferred) | Full `docs/work-items/IMP-*.md` — current/desired state, risks, criteria |
| Required | CLAUDE.md | Always | Full document |
| Required | Architecture | Always | Current + target state |
| Optional | Data Model | Data layer refactoring | Affected entity definitions |
| Optional | Stakeholder Definition | Large-scale refactoring | Principles, scope |

**Example Assembly**:
```xml
<context>
  <improvement-proposal>
    [Full Improvement Proposal document from docs/work-items/IMP-XXX-name.md]
  </improvement-proposal>

  <code-conventions>
    [Full CLAUDE.md]
  </code-conventions>

  <architecture>
    [Current architecture of affected area]
    [Target architecture after refactoring]
    [Component dependencies]
  </architecture>
</context>
```

---

### 4. Testing

**Goal**: Generate comprehensive test tasks covering requirements and edge cases.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | CLAUDE.md | Always | Testing conventions section |
| Optional | Architecture | Testing component interactions | Component structure |
| Optional | Stakeholder Definition | Testing acceptance criteria | Success metrics |

**Example Assembly**:
```xml
<context>
  <code-conventions>
    [CLAUDE.md testing section]
  </code-conventions>

  <architecture>
    [Components under test]
    [Integration points to verify]
  </architecture>
</context>
```

---

### 5. Integration

**Goal**: Generate tasks for connecting to external services correctly.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Architecture | Always | Integration points, data flow |
| Required | CLAUDE.md | Always | Error handling patterns |
| Required | Data Model | Always | Entities involved in the integration |
| Required | API Specification | Always | Endpoints that connect to or expose the integration |
| Optional | Stakeholder Definition | Scope check | What integrations are in scope |

**Example Assembly**:
```xml
<context>
  <architecture>
    [Where integration fits in system]
    [Data flow through integration]
    [External service details from integration points section]
  </architecture>

  <code-conventions>
    [Full CLAUDE.md]
    [Emphasis on error handling section]
  </code-conventions>

  <data-model>
    [Entities involved in the integration]
  </data-model>

  <api-spec>
    [Endpoints that expose or connect to the integration]
  </api-spec>
</context>
```

---

### 6. Prioritization & Roadmap Planning

**Goal**: Evaluate and prioritize work items based on product strategy.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Work Items | Always | All `docs/work-items/FEAT-*.md`, `BUG-*.md`, `IMP-*.md` to compare |
| Required | Stakeholder Definition | Always | Success metrics, scope, philosophy |
| Required | Persona | Always | User priorities and pain points |
| Optional | Architecture | Technical feasibility | Complexity factors |

**Example Assembly**:
```xml
<context>
  <work-items>
    [All Feature Briefs, Bug Reports, and Improvement Proposals to prioritize]
  </work-items>

  <stakeholder-definition>
    ## Success Metrics
    [What we're optimizing for]

    ## Scope Lock
    [What's in/out of scope]

    ## Product Philosophy
    [Guiding principles]
  </stakeholder-definition>

  <persona>
    [User pain points and priorities]
    [What the user values most]
  </persona>
</context>

<request>
Evaluate these work items and recommend prioritization:
1. FEAT-001: [Feature name]
2. BUG-003: [Bug summary]
3. IMP-002: [Improvement name]

Consider: user impact, severity, development cost, strategic alignment, dependencies.
</request>
```

---

### 7. UI Mockup Generation

**Goal**: Generate a self-contained HTML mockup for stakeholder visual approval before Angular implementation.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | UI Specification | Always | Target screen's spec block (layout, hierarchy, states) + Design System section (colors, typography, spacing) |
| Required | CLAUDE.md | Always | Design tokens, Tailwind conventions, frontend patterns |
| Recommended | API Specification | When screen displays data | Response DTO shapes for realistic placeholder content |
| Optional | Persona | User-facing screens | Content tone for placeholder text |
| Optional | Stakeholder Definition | Branding needed | Product name, philosophy |

**Example Assembly**:
```xml
<context>
  <ui-specification>
    [Target screen spec block: layout sketch, component hierarchy, states]
    [Design System section: colors, typography, spacing tokens]
  </ui-specification>

  <code-conventions>
    [CLAUDE.md: Tailwind conventions, design tokens, frontend patterns]
  </code-conventions>

  <api-spec>
    [If data-driven screen: response DTO shapes for placeholder content]
  </api-spec>
</context>
```

---

### 8. Release Lifecycle Transition

**Goal**: Transition from versioned to continuous development, or mark a version as complete.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | Stakeholder Definition | Always | Full document — current scope structure, all sections |
| Required | CLAUDE.md | Always | Full document |
| Recommended | Architecture | Structural changes during transition | Current component structure |
| Recommended | Release Lifecycle Guide | Always | `guides/release-lifecycle.md` — transition checklist and target structure |

**Example Assembly**:
```xml
<context>
  <stakeholder-definition>
    [Full stakeholder definition document]
  </stakeholder-definition>

  <code-conventions>
    [Full CLAUDE.md]
  </code-conventions>

  <release-lifecycle-guide>
    [Full guides/release-lifecycle.md]
  </release-lifecycle-guide>
</context>
```

---

### 9. ADR Compilation

**Goal**: Compile Architecture Decision Records from a shared ADR repo into pre-filled template sections.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | ADR files | Always | All selected ADR files from the shared ADR repo |
| Recommended | `.ai-framework/templates/` | Always | Target templates for correct heading structure (agents read directly) |

**Note:** This task type is different from the others — it produces pre-filled *template sections*, not a final deliverable. Run it when bootstrapping a new project, before filling in Phase 2 templates.

---

### 10. DDR Compilation

**Goal**: Compile Design Decision Records from a shared DDR repo into pre-filled Design System sections, component patterns, state handling, and responsive breakpoints.

| Priority | Document | Include When | What to Include |
|----------|----------|--------------|-----------------|
| Required | DDR files | Always | All selected DDR files from the shared DDR repo (or specified via a profile) |
| Optional | Profile file | When using a curated visual identity | Profile from DDR repo with override values for tokens |
| Recommended | `.ai-framework/templates/` | Always | Target templates for correct heading structure (agents read directly) |

**Note:** This task type produces pre-filled *Design System sections*, not a final deliverable. Run it when bootstrapping a new project, after applying ADRs (Step 0.5) and before filling in UI-related templates (Phase 2, Step 7).

**Example Assembly**:
```xml
<context>
  <ddrs>
    [All DDR files selected for the project, or referenced by a profile]
  </ddrs>

  <profile>
    [If using a profile: the profile file with override values]
  </profile>
</context>
```

---

## Context Size Management

### Guideline: Quality Over Quantity

With 10 templates, be mindful of total context size. Use section extraction for data-model, api-spec, and ui-specification — include only the entities, endpoints, and screens relevant to your task, not the full documents. Work item documents (Feature Brief, Bug Report, Improvement Proposal) should generally be included in full since they are scoped to a single work item.

### Strategies for Large Documents

#### 1. Section Extraction
Instead of full document, extract only relevant sections:

```xml
<!-- Instead of full architecture doc -->
<architecture-excerpt>
## Order Service
[Order service section only]

## Integration Points
[Only relevant integration section]
</architecture-excerpt>
```

#### 2. Summary + Detail
Provide summary for context, detail only where needed:

```xml
<architecture-summary>
The system uses a microservices architecture with 5 services.
For this task, only the Order Service is relevant.
</architecture-summary>

<architecture-detail>
## Order Service
[Full Order Service definition]
</architecture-detail>
```

---

## Context Assembly Workflow

### Path A: AI Agents (Claude Code, etc.)

Agents have direct file access and don't need XML assembly. Follow these steps:

1. **Identify the task type** from the user's request (Feature, Bug, Refactor, Spec Generation, UI Spec, UI Mockup, Release Transition)
2. **Read the files** listed in the CLAUDE.md routing table for that task type
3. **For large documents**, read only the sections relevant to the task — e.g., for a task about labels, read only the Label entity from `data-model.md` and label endpoints from `api-spec.md`
4. **Read the prompt template** from `.ai-framework/prompts/` — use the **Output Format** section as your deliverable structure, and apply the **Guidance**, **Constraints**, and **Post-Generation Checklist**
5. **Generate the deliverable** directly — no XML wrapping needed

### Path B: Manual Context Assembly (Chat Workflows)

For copy-paste workflows where you assemble an XML prompt to submit to Claude:

#### Step 1: Identify Task Type
What kind of task are you generating? (Feature, Bug, Refactor, Test, Integration, Prioritization, UI Mockup)

#### Step 2: Check Required Documents
Consult the task-type tables above for your task type. Gather required documents.

#### Step 3: Assess Complexity
- **Simple task**: CLAUDE.md only (or + 1 other document)
- **Standard task**: 2-3 documents
- **Complex task**: All 4+ documents

#### Step 4: Extract Relevant Sections
Don't include entire documents if only portions are relevant.

#### Step 5: Structure with XML Tags
Use clear XML tags to separate context sections. This helps Claude parse and reference specific sections.

#### Step 6: Add Task-Specific Details
Include the specific request, constraints, and output format requirements.

---

## Common Mistakes to Avoid

### 1. Missing Critical Context
**Wrong**: Generating feature tasks without scope lock
**Right**: Always include stakeholder definition for features

### 2. Outdated Context
**Wrong**: Using cached/old versions of documents
**Right**: Always pull latest versions before compilation

### 3. Unstructured Context
**Wrong**: Pasting documents without XML tags
**Right**: Use clear tags so Claude can reference sections

### 4. Missing Constraints
**Wrong**: Assuming Claude knows project constraints
**Right**: Explicitly state technology, timeline, and compatibility constraints

---

## Quick Reference Card

```
┌───────────────┬──────────────────────────────────────────────────────────┐
│ Task Type     │ Must Include                        │ Recommended        │
├───────────────┼─────────────────────────────────────┼────────────────────┤
│ New Feature   │ Feature Brief + Stakeholder +       │ Data Model, API Spec,      │
│               │ CLAUDE.md                           │ UI Spec                    │
│ Bug Fix       │ Bug Report + CLAUDE.md              │ Architecture, Data Model   │
│ Refactoring   │ Improvement Proposal + CLAUDE.md +  │ Data Model                 │
│               │ Architecture                        │                            │
│ Testing       │ CLAUDE.md                           │ API Spec, UI Spec          │
│ Integration   │ Architecture + CLAUDE.md +          │                            │
│               │ Data Model + API Spec               │                            │
│ Prioritization│ Work Items + Stakeholder + Persona  │ Architecture               │
│ UI Mockup     │ UI Spec + CLAUDE.md                 │ API Spec, Persona          │
│ Release       │ Stakeholder + CLAUDE.md             │ Architecture,              │
│ Transition    │                                     │ release-lifecycle.md       │
│ ADR           │ ADR files +                         │                            │
│ Compilation   │ .ai-framework/templates/            │                            │
│ DDR           │ DDR files (+ optional profile) +    │                            │
│ Compilation   │ .ai-framework/templates/            │                            │
└───────────────┴─────────────────────────────────────┴────────────────────────────┘
```

---

## Example: Complete Context Compilation (Chat Workflow)

Here's a full example for a new feature task using the chat workflow (XML assembly). Agents skip this — they read files directly.

```xml
<task-generation-request>

<context>

<!-- Layer 1: Strategic -->
<stakeholder-definition>
## Product Philosophy
- Conversation-first: Feels like chat, not a form
- Keyboard-last: Typing only when unavoidable
- Trust before tech: Never surprise the user

## Scope Lock (V1)
Included: Pizza ordering, Multi-pizza cart, Visual guidance
Excluded: Discounts, User accounts, Loyalty programs, In-chat payments
</stakeholder-definition>

<persona>
## Primary User: The Busy Restaurant Owner
- Not tech-savvy, needs simple interactions
- Peak pain during Friday evening rush
- Success = fewer order errors, faster checkout
</persona>

<!-- Layer 2: Architectural -->
<architecture>
## Order Service
- Handles order lifecycle
- Connects to payment and notification services
- REST API with JSON payloads
</architecture>

<!-- Layer 3: Specification -->
<data-model>
## Order Entity (Orders Module)
| Field | Type | Constraints |
|-------|------|-------------|
| id | UUID | PK |
| phone_number | string(20) | Required, Indexed |
| status | enum: OrderStatus | Required |
| total_amount | decimal(10,2) | Required |
| created_at | timestamptz | Required, Auto |

## OrderItem Entity (Orders Module)
| Field | Type | Constraints |
|-------|------|-------------|
| id | UUID | PK |
| order_id | UUID | FK → Order |
| pizza_name | string(100) | Required |
| quantity | int | Required, Min: 1 |
</data-model>

<api-spec>
## GET /api/orders?phone={phone}&limit=10
Auth: Public (phone-based)
Response: { data: Order[], meta: { totalCount } }

## POST /api/orders
Auth: Public
Request: { phone_number, items: [{ pizza_id, quantity }] }
Response: { data: Order }
</api-spec>

<!-- Layer 4: UI -->
<ui-specification>
## Order History Screen
Route: /orders/history
Components: OrderListComponent → OrderCardComponent
API: GET /api/orders?phone={phone}&limit=10
States: Loading (spinner), Empty ("No orders yet"), Error (retry banner)
Interactions: Click order → expand detail, Pull to refresh → re-fetch
</ui-specification>

<!-- Layer 5: Implementation -->
<code-conventions>
## Project Structure
- Flows: /flows/*.json
- Backend: /src/handlers/
- Types: /src/types/

## Patterns
- WhatsApp Flows for all UI
- Zod for validation
- Custom errors with APP- prefix
</code-conventions>

</context>

<task-type>New Feature</task-type>

<request>
Generate implementation tasks for the Order History feature.
</request>

<constraints>
- Must work within WhatsApp Flows (no web views)
- Phone number is the only user identifier
- History limited to last 30 days
</constraints>

<output-format>
[Specify desired task format]
</output-format>

</task-generation-request>
```
