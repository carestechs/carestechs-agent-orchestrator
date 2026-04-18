# UI Spec Generation Prompt

> **Purpose**: Generate a UI Specification document from existing strategic, architectural, and specification documentation. Use this prompt when you have the stakeholder definition, architecture, API spec, and code conventions, and need to derive the screen layouts, component hierarchy, design tokens, and interaction patterns before generating frontend feature tasks.
>
> **When to use**: After completing the API Specification (Step 6) and before generating feature tasks (Phase 3). This fills the gap between "what data is available" and "what the UI looks like."

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "UI spec generation"
2. Read the output format template from `.ai-framework/templates/ui-specification.md` for section structure
3. Use the **Guidance** and **Output Format** sections below to derive screens, components, and interactions
4. Apply the **Constraints** and **Post-Generation Checklist** to validate quality

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<ui-spec-generation-request>

<context>

<stakeholder-definition>
<!-- REQUIRED: User flows drive screen inventory — every flow step maps to a screen.
     Scope lock determines which screens exist. Philosophy shapes interaction patterns. -->
[Paste full stakeholder-definition.md content]
</stakeholder-definition>

<architecture>
<!-- REQUIRED: Component structure, real-time capabilities, and module boundaries
     inform the component hierarchy and data flow patterns. -->
[Paste full ARCHITECTURE.md content]
</architecture>

<api-spec>
<!-- REQUIRED: Endpoints map directly to component data needs.
     DTOs inform what fields each component displays. -->
[Paste full api-spec.md content]
</api-spec>

<data-model>
<!-- RECOMMENDED: Entity definitions inform display fields, relationships inform
     navigation patterns, enums inform dropdown/filter options. -->
[Paste relevant sections from data-model.md]
</data-model>

<code-conventions>
<!-- REQUIRED: Angular/Material/Tailwind constraints, naming conventions,
     and frontend patterns directly constrain component implementation. -->
[Paste full CLAUDE.md content]
</code-conventions>

<persona>
<!-- OPTIONAL: User expertise level and pain points inform interaction complexity,
     information density, and onboarding patterns. -->
[Paste persona details if available]
</persona>

<design-decisions>
<!-- RECOMMENDED: If DDRs were compiled, include the compiled Design System output.
     This provides pre-defined colors, typography, spacing, component patterns,
     state patterns, and responsive breakpoints.
     Use compiled DDR values directly — do not re-derive or override them. -->
[Paste compiled DDR output (Design System tables, State Patterns, Responsive Breakpoints), if available]
</design-decisions>

</context>

<spec-type>UI Specification</spec-type>

<request>
Generate a complete UI Specification document for this project.

Use the template structure from `.ai-framework/templates/ui-specification.md`
as the output format.

Derive all content from the context documents provided:
- Screens from stakeholder user flows and scope lock
- Component hierarchy from architecture modules and API endpoint groupings
- Design tokens from project branding and conventions
- Interaction patterns from user flow steps and API endpoints
- States (loading, empty, error) for every screen and component

Do not invent screens or features beyond the defined scope lock.
</request>

<guidance>
## Deriving Screens from User Flows

1. Read the Scope Lock — every in-scope feature implies at least one screen
2. Read the User Flow — each phase maps to one or more screens
3. For each screen, identify:
   - What data it displays (from API Spec response DTOs)
   - What actions the user can take (from API Spec request endpoints)
   - What navigation leads to/from it (from user flow transitions)

## Deriving Components from Entities

1. Each entity typically has:
   - A **list component** (table or card grid) — maps to list endpoint
   - A **detail component** (view/edit form) — maps to get/update endpoints
   - A **create component** (dialog or form) — maps to create endpoint
   - A **card component** (compact display) — used in lists, boards, and references

2. Cross-entity relationships create:
   - **Nested lists** (e.g., project → task list within project detail)
   - **Selection components** (e.g., assignee selector referencing users)
   - **Navigation patterns** (e.g., click task card → task detail)

## Deriving Interactions from Endpoints

Map each API endpoint to a user interaction:

| Endpoint Pattern | Typical Interaction |
|-----------------|-------------------|
| GET /api/resources | Page load, filter change, search |
| GET /api/resources/:id | Click item to view detail |
| POST /api/resources | Submit create form/dialog |
| PUT/PATCH /api/resources/:id | Submit edit form, inline edit, drag-drop |
| DELETE /api/resources/:id | Click delete with confirmation dialog |
| POST /api/resources/:id/action | Click action button (assign, move, etc.) |

## Deriving Design Tokens

1. **If DDRs were compiled**: Use the compiled Design System values directly for colors, typography, spacing, state patterns, and responsive breakpoints. Do not re-derive or invent new values — the DDR compilation is the authoritative source.
2. If the project has brand guidelines (but no DDR compilation), extract colors, fonts, and spacing
3. If using a component library (Angular Material), document the theme configuration
4. Map semantic colors (primary, error, success) to specific hex values
5. Define typography scale based on the heading hierarchy needed

## States for Every Screen

Every screen specification MUST include:

| State | What to Define |
|-------|---------------|
| **Default** | What the screen looks like with data loaded |
| **Loading** | What shows while data is being fetched (skeleton, spinner, shimmer) |
| **Empty** | What shows when there's no data yet (illustration, message, CTA) |
| **Error** | What shows when data fetch fails (error message, retry button) |
</guidance>

<constraints>
- Follow all conventions from CLAUDE.md exactly (Angular standalone components, Angular Material, Tailwind, etc.)
- Use Angular Material components — do not define custom UI primitives when Material provides an equivalent
- Use Tailwind CSS for all layout and spacing — do not create component-scoped CSS
- Respect module boundaries from the architecture document — components belong to the module whose data they primarily display
- Only include screens for features within the scope lock
- Every screen must map to at least one API endpoint
- Every entity with a list endpoint must have a corresponding list screen or embedded list component
- Use the response envelope format from CLAUDE.md when describing what data components receive
</constraints>

<output-format>
## Output Requirements

Generate a complete document following the template structure from
`.ai-framework/templates/ui-specification.md`:

1. **Overview** — UI summary + Key UI Decisions table
2. **Design System** — Colors, typography, spacing, component library usage
3. **Screen Inventory** — Table of all screens with routes, auth, layouts
4. **Shared Layouts** — App shell structure, public layout
5. **Screen Specifications** — Per-screen blocks with:
   - Layout sketch (ASCII)
   - Component hierarchy (tree)
   - Component → API mapping (table)
   - States: default, loading, empty, error
   - User interactions: action → result → API call
6. **Shared Components** — Reusable components with inputs/outputs/variants
7. **Usage Notes** — Rules for AI task generation

## Quality Checks:
- [ ] Every user flow step from the stakeholder definition has a corresponding screen
- [ ] Every screen maps to at least one API endpoint from the API spec
- [ ] Every entity from the data model has a display component (list, detail, or card)
- [ ] All 4 states (default, loading, empty, error) are defined for every screen
- [ ] Component hierarchy uses Angular standalone components (no NgModules)
- [ ] All UI uses Angular Material components and Tailwind CSS (no custom primitives or CSS files)
- [ ] Shared components are identified and documented (used in 2+ screens)
- [ ] Routes follow a consistent pattern and match the screen inventory
- [ ] Interactions are specific (not vague) — each maps to a UI element, result, and API call
</output-format>

</ui-spec-generation-request>
```

---

## Context Selection Guide

### What to Include

| Document | Priority | What to Include |
|----------|----------|-----------------|
| Stakeholder Definition | Required | Full document — user flows, scope lock, philosophy |
| Architecture | Required | Full document — modules, components, real-time capabilities |
| API Specification | Required | Full document — all endpoints, DTOs, status codes |
| CLAUDE.md | Required | Full document — Angular/Material/Tailwind conventions |
| Data Model | Recommended | Full document — entities inform display fields and navigation |
| Persona | Optional | Include for interaction complexity and onboarding decisions |
| Compiled DDR output | Recommended | Pre-defined Design System values from DDR compilation — use directly instead of deriving |

**All four required documents should be included in full.** The UI spec needs the complete picture to derive a comprehensive screen inventory and component hierarchy.

---

## Workflow

### Step 1: Ensure Prerequisites Exist

Before generating a UI spec, you should have:
1. **Stakeholder Definition** — for user flows and scope
2. **Architecture** — for module structure
3. **Data Model** — for entity definitions (generate first if missing)
4. **API Specification** — for endpoint definitions (generate first if missing)
5. **CLAUDE.md** — for frontend conventions

### Step 2: Generate UI Specification

Assemble all context documents and run the prompt above. The AI will derive:
- Screens from user flows
- Components from entities and endpoints
- Interactions from API endpoints
- Design tokens from conventions

### Step 3: Review and Validate

After generating, validate against the source docs:
- Does every user flow step have a screen?
- Does every screen map to at least one API endpoint?
- Does every entity have a display component?
- Are all states (loading, empty, error) specified for every screen?
- Are Angular Material and Tailwind constraints respected?

---

## Example: Generating a UI Spec for a Project Management App

```xml
<ui-spec-generation-request>

<context>

<stakeholder-definition>
## Executive Summary
Internal project management web application with Kanban, List, and Gantt views.

## User Flow
Phase 1: Login via Google OAuth
Phase 2: Select or create project, manage members
Phase 3: Create tasks, assign to members, set status/priority/dates
Phase 4: View tasks in Kanban (drag columns), List (sortable table), Gantt (timeline)
Phase 5: Collaborate via comments and file attachments on tasks

## Scope Lock (V1)
Included: Projects, Tasks (Kanban/List/Gantt), Team members, Labels, Comments
Excluded: Time tracking, Billing, External integrations, Mobile app
</stakeholder-definition>

<architecture>
## Frontend
- Angular 20+ SPA with standalone components
- Angular Material for UI components
- Tailwind CSS for styling
- Route-based lazy loading

## Modules
- Auth → Google OAuth, JWT
- Users → profiles, roles
- Projects → projects, membership
- Tasks → tasks, labels, comments, attachments
</architecture>

<api-spec>
## Projects Module
- GET /api/projects — List user's projects
- POST /api/projects — Create project
- GET /api/projects/:id — Get project detail
- PATCH /api/projects/:id — Update project

## Tasks Module
- GET /api/projects/:id/tasks — List tasks (with filters)
- POST /api/projects/:id/tasks — Create task
- GET /api/tasks/:id — Get task detail
- PATCH /api/tasks/:id — Update task (status, assignee, etc.)
- DELETE /api/tasks/:id — Soft-delete task
</api-spec>

<code-conventions>
## Frontend Conventions
- Angular standalone components (no NgModules)
- Angular Material for all UI components
- Tailwind CSS for all styling
- TypeScript strict mode — no `any`
- Signals where appropriate
</code-conventions>

</context>

<spec-type>UI Specification</spec-type>

<request>
Generate a complete UI Specification for this project management application.
Derive all screens from the user flow phases.
Map each screen to the appropriate API endpoints.
</request>

</ui-spec-generation-request>
```

---

## Post-Generation Checklist

After the AI generates a UI spec document, verify:

- [ ] Every user flow phase from the stakeholder definition has at least one screen
- [ ] Every screen in the Screen Inventory has a full specification in Section 5
- [ ] Every screen specification includes all 4 states (default, loading, empty, error)
- [ ] Every screen has a component hierarchy tree
- [ ] Every component maps to at least one API endpoint
- [ ] Every interaction specifies the UI element, result, and API call
- [ ] Shared components used in 2+ screens are documented in Section 6
- [ ] Design tokens (colors, typography, spacing) are fully defined
- [ ] All Angular Material components used are listed with customization notes
- [ ] Routes are consistent with the screen inventory
- [ ] No screens exist for features outside the scope lock
- [ ] Layout sketches match the shared layout definitions in Section 4
