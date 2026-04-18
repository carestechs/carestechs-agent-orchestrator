# Getting Started

This guide walks you from "I have a project idea" to "I'm generating AI tasks." Budget about 1-2 hours for the full workflow, or 30 minutes for a rough first pass.

---

## Phase 0: Prerequisites

**What you need:**
- A project idea (even a vague one works)
- A folder for your project

**That's it.** You don't need a tech stack chosen, wireframes, or a business plan. The templates will help you figure those out.

### Choose Your Path

| Path | You have... | Start with... |
|------|-------------|---------------|
| **New Project** | An idea but no code yet | Persona → Stakeholder → CLAUDE.md → Architecture → Data Model → API Spec → UI Spec |
| **Existing Codebase** | Code that needs documentation | CLAUDE.md → Stakeholder → Architecture → Data Model → API Spec → UI Spec → Persona |

---

## Phase 1: Set Up Structure

Copy the scaffold into your project:

```bash
# From your project root
cp -r path/to/ai-task-framework-v1/scaffold/* .
```

You should now have:

```
your-project/
├── CLAUDE.md
├── .ai-framework/                # Bundled framework reference
│   ├── VERSION
│   ├── README.md
│   ├── templates/
│   ├── prompts/
│   └── guides/
└── docs/
    ├── personas/
    │   └── primary-user.md
    ├── stakeholder-definition.md
    ├── ARCHITECTURE.md
    └── work-items/               # Feature briefs, bug reports, improvements
        ├── FEAT-001-example.md
        ├── BUG-001-example.md
        └── IMP-001-example.md
```

Each file has section headers with `<!-- TODO -->` prompts telling you exactly what to fill in.

### Step 0.5: Apply Architecture Decisions (Optional)

If you have a set of Architecture Decision Records (ADRs) from a shared repo (e.g., `your-architecture-decisions`):

1. Select the ADRs relevant to your project's tech stack (e.g., the `profiles/dotnet-angular-modular-monolith.md` manifest lists which ADRs to use together)
2. Use the [`.ai-framework/prompts/compile-adrs.md`](../prompts/compile-adrs.md) prompt to compile them into pre-filled template sections
3. Paste the compiled sections into your project docs before filling in project-specific details

This pre-fills conventions, patterns, and decision tables so you only need to add project-specific content (entities, endpoints, screens, etc.).

### Step 0.6: Apply Design Decisions (Optional)

If you have a set of Design Decision Records (DDRs) from a shared repo (e.g., `your-ui-design-decisions`):

1. **Select a profile** that matches your project type (e.g., `profiles/corporate-clean.md` for B2B/enterprise, `profiles/modern-minimal.md` for content apps, `profiles/bold-startup.md` for consumer products, `profiles/arauco-forest.md` for B2B corporate portals) — or select individual DDRs
2. Use the [`.ai-framework/prompts/compile-ddrs.md`](../prompts/compile-ddrs.md) prompt to compile them into pre-filled template sections
3. Paste the compiled sections into your project docs:
   - **Design System** (colors, typography, spacing, component library, state patterns, responsive breakpoints) → `docs/ui-specification.md`
   - **Design Patterns to Follow / Avoid** → `CLAUDE.md`
   - **Component Examples Appendix** → save as reference for mockup generation

This pre-fills the Design System, state handling patterns, and responsive breakpoints so you only need to add project-specific content (screens, components, interactions). Run this step **before** filling the UI Specification (Step 7).

---

## Phase 2: Fill Templates (New Project Path)

Work through the templates in this order. Each step builds on the previous one.

### Step 1: Persona (`docs/personas/primary-user.md`)

**Goal:** Define who you're building for.

**Key questions to answer:**
- Who is your primary user? What's their role?
- What is their single biggest pain point?
- How do they solve this problem today (without your product)?
- Why target this user segment first?

**"Good enough" checklist:**
- [ ] You can describe the user in one sentence
- [ ] You've named their core pain point
- [ ] You know why their current workaround fails
- [ ] You've noted why you picked this segment over others

**Time:** ~15 minutes

> **Tip:** If you're stuck, think about the last time you personally felt this pain. That's often your best persona.

---

### Step 2: Stakeholder Definition (`docs/stakeholder-definition.md`)

**Goal:** Define what you're building, why, and where the boundaries are.

**Key questions to answer:**
- What type of product is this? (web app, CLI, API, mobile app...)
- What is the one-sentence value proposition?
- What are 3-5 guiding principles for every decision?
- What is explicitly in scope? What is explicitly out?
- How will you measure success?

**"Good enough" checklist:**
- [ ] Executive summary is filled (what, value prop, success criteria)
- [ ] You have at least 3 guiding principles
- [ ] Scope Lock has at least 3 items in scope and 2 items explicitly excluded
- [ ] You have at least 2 success metrics with measurable targets

**Time:** ~20 minutes

> **Tip:** The Scope Lock section is the most important. Be ruthless about what's out of scope — this prevents AI from suggesting features you don't want.

---

### Step 3: CLAUDE.md (`CLAUDE.md`)

**Goal:** Give AI assistants the context they need to write code correctly.

**Key questions to answer:**
- What's the tech stack?
- What are the essential commands (dev, build, test, lint)?
- What naming conventions do you use?
- What patterns should always be followed? What should never be done?

**"Good enough" checklist:**
- [ ] Project overview and tech stack are filled
- [ ] At least 2-3 common commands are documented
- [ ] Naming conventions table has entries for files, functions, and types
- [ ] At least 2 patterns and 2 anti-patterns are listed

**Time:** ~15 minutes

> **Tip:** For a new project, this can be aspirational — describe the conventions you *want* to follow. You'll refine it after writing your first code.

---

### Step 4: Architecture (`docs/ARCHITECTURE.md`)

**Goal:** Document how the system is structured.

**Key questions to answer:**
- What are the major components?
- What technologies are you using for each layer?
- How does data flow through the system?
- What external services do you integrate with?
- How do you handle authentication and security?

**"Good enough" checklist:**
- [ ] Technology stack table is filled
- [ ] At least 2-3 components are described with their responsibilities
- [ ] Primary data flow is described (even as a numbered list)
- [ ] Security approach is outlined (even if just "TBD — will use [X]")

**Time:** ~20 minutes

> **Tip:** For a brand-new project, keep this high-level. You can flesh out the component diagram and data flows after you've written some code.

---

### Step 5: Data Model (`docs/data-model.md`)

**Goal:** Define every entity, its fields, relationships, and module ownership.

**Key questions to answer:**
- What entities does each module own?
- What fields does each entity have (name, type, constraints)?
- How do entities relate to each other (1:N, M:N)?
- Which references cross module boundaries (ID-only)?
- What enums and value types exist?

**"Good enough" checklist:**
- [ ] Every in-scope feature maps to at least one entity
- [ ] Each entity has a field table with types and constraints
- [ ] Relationships are defined with cascade behaviors
- [ ] Cross-module references are ID-only
- [ ] Standard audit fields (id, created_at, updated_at) are on every entity

**Time:** ~20 minutes

> **Tip:** Use the `spec-generation.md` prompt to generate this from your existing stakeholder + architecture + CLAUDE.md docs. Review and refine the output rather than writing from scratch.

---

### Step 6: API Specification (`docs/api-spec.md`)

**Goal:** Define every REST endpoint with routes, request/response shapes, and status codes.

**Key questions to answer:**
- What CRUD endpoints does each entity need?
- What non-CRUD actions exist (assign, move, upload)?
- What are the request and response DTO shapes?
- Which endpoints require authentication? What roles?
- How are lists paginated?

**"Good enough" checklist:**
- [ ] Every entity has appropriate CRUD endpoints
- [ ] Request/response DTOs are fully defined
- [ ] Auth requirements are specified per endpoint
- [ ] List endpoints support pagination
- [ ] Error status codes are listed for each endpoint

**Time:** ~20 minutes

> **Tip:** Generate the data model first, then use it as additional context when generating the API spec — endpoints map naturally from entities.

---

### Step 7: UI Specification (`docs/ui-specification.md`)

**Goal:** Define every screen's layout, component hierarchy, design tokens, interaction patterns, and state handling (loading, empty, error).

**Key questions to answer:**
- What screens does each user flow step require?
- What is the component hierarchy for each screen?
- Which component calls which API endpoint?
- What does each screen look like in loading, empty, and error states?
- What shared/reusable components exist across screens?
- What are the design tokens (colors, typography, spacing)?

**"Good enough" checklist:**
- [ ] Every user flow step has a corresponding screen
- [ ] Every screen maps to at least one API endpoint
- [ ] Every screen has all 4 states defined (default, loading, empty, error)
- [ ] Component hierarchy is defined for each screen
- [ ] Shared components are identified (used in 2+ screens)
- [ ] Design tokens (colors, typography, spacing) are defined
- [ ] Interactions are specific — each maps to a UI element, result, and API call

**Time:** ~20 minutes

> **Tip:** Generate the API spec first, then use it as additional context when generating the UI spec — endpoints map directly to component data needs. Use the `ui-spec-generation.md` prompt to auto-generate from your existing docs.

---

### Step 7.5: HTML Mockups (Optional)

**Goal:** Create browser-viewable HTML prototypes of key screens for stakeholder visual approval before writing Angular code.

**When to use:**
- User-facing screens with novel or complex layouts
- Multi-state screens where stakeholders need to see all states (default, loading, empty, error)
- Screens where the ASCII layout sketch needs visual validation

**When to skip:**
- Standard CRUD layouts (list/detail/form)
- Screens that follow a pattern already approved in a previous mockup
- Backend-only features with no UI

**Process:**
1. Pick a screen from the UI Specification screen inventory
2. Use the [`mockup-generation.md`](../prompts/mockup-generation.md) prompt template
3. Assemble context: target screen spec block + Design System + CLAUDE.md
4. Generate the HTML mockup file
5. Open in a browser and review visually
6. Share with stakeholders for approval

**"Good enough" checklist:**
- [ ] Layout matches the ASCII sketch from the UI Specification
- [ ] Design tokens (colors, fonts, spacing) match the Design System
- [ ] All requested states are shown side-by-side
- [ ] Stakeholder has reviewed and approved

**Time:** ~10 minutes per screen

> **Tip:** Focus on novel layouts — don't mock up every screen. Standard CRUD screens rarely need a visual prototype.

---

## Phase 2-alt: Fill Templates (Existing Codebase Path)

If you already have code, document what exists first, then layer in the strategic context.

### Step 1: CLAUDE.md (Document What Exists)

Start here because this is the most immediately useful — it makes every AI interaction with your code better right away.

- Look at your `package.json` (or equivalent) for commands
- Look at your file structure for naming conventions
- Think about what patterns you've already established
- Note any "gotchas" a new developer (or AI) would hit

### Step 2: Stakeholder Definition (What You're Building Toward)

Even for an existing codebase, clarifying your vision and scope helps the AI make better prioritization decisions.

- Focus on the **Scope Lock** — what's in and out for the current version
- Fill the **Product Philosophy** — this shapes how AI approaches tasks
- Set **Success Metrics** — what are you optimizing for right now?

### Step 3: Architecture (Document Current System)

Describe the system as it is today, not as you wish it were.

- Map out the components that actually exist
- Document the real data flow
- Note integration points and their current state
- Be honest about security — document gaps as "TODO" items

### Step 4: Persona (If Not Already Clear)

If you've been building for a while, you likely already know your user. Document that knowledge so the AI can reference it.

---

## Phase 2.5: Write Work Items

Before generating tasks, describe **what specific work to do** using work item templates. Work items bridge the gap between your system documentation (what the product is) and task generation (how to build it).

### Feature Brief (`docs/work-items/FEAT-XXX-name.md`)

**When:** You want to build a new feature.
**What to fill:** User story, feature-level scope (in/out), acceptance criteria, entity/API/UI impact, edge cases, constraints, traceability.
**Template reference:** `.ai-framework/templates/feature-brief.md`

### Bug Report (`docs/work-items/BUG-XXX-name.md`)

**When:** You need to fix a bug.
**What to fill:** Reproduction steps, expected vs actual behavior, error evidence, environment, impact assessment, traceability.
**Template reference:** `.ai-framework/templates/bug-report.md`

### Improvement Proposal (`docs/work-items/IMP-XXX-name.md`)

**When:** You want to refactor, optimize, or improve code quality.
**What to fill:** Current state + problems, desired state + benefits, risk assessment, success criteria, test coverage baseline, traceability.
**Template reference:** `.ai-framework/templates/improvement-proposal.md`

### ID Convention

- Features: `FEAT-001`, `FEAT-002`, ...
- Bugs: `BUG-001`, `BUG-002`, ...
- Improvements: `IMP-001`, `IMP-002`, ...
- File naming: `docs/work-items/FEAT-001-short-title.md`

> **Tip:** You don't need work items for every small task. For quick, ad-hoc work, the task generation prompts still support inline descriptions as a fallback. But for anything non-trivial, a work item document produces significantly better task breakdowns.

---

## Phase 3: Generate Your First AI Tasks

You've got documentation and work items. Now put them to work.

### Pick a Task Type

| I want to... | Use this prompt template |
|--------------|------------------------|
| Build a new feature | [`.ai-framework/prompts/feature-tasks.md`](../prompts/feature-tasks.md) |
| Fix a bug | [`.ai-framework/prompts/bugfix-tasks.md`](../prompts/bugfix-tasks.md) |
| Refactor existing code | [`.ai-framework/prompts/refactor-tasks.md`](../prompts/refactor-tasks.md) |
| Generate a UI specification | [`.ai-framework/prompts/ui-spec-generation.md`](../prompts/ui-spec-generation.md) |
| Generate an HTML mockup | [`.ai-framework/prompts/mockup-generation.md`](../prompts/mockup-generation.md) |
| Pre-fill templates from ADRs | [`.ai-framework/prompts/compile-adrs.md`](../prompts/compile-adrs.md) |
| Pre-fill design system from DDRs | [`.ai-framework/prompts/compile-ddrs.md`](../prompts/compile-ddrs.md) |

### Assemble Context

Follow [`.ai-framework/guides/context-compilation.md`](context-compilation.md) to pick which documents to include based on your task type. The quick reference:

| Task Type | Always Include | Include If Relevant |
|-----------|---------------|---------------------|
| New Feature | Feature Brief + Stakeholder + CLAUDE.md | Data Model, API Spec, UI Spec, Persona, Architecture |
| Bug Fix | Bug Report + CLAUDE.md | Architecture, Data Model, API Spec |
| Refactoring | Improvement Proposal + CLAUDE.md + Architecture | Data Model, Stakeholder |
| Testing | CLAUDE.md | Architecture, API Spec, UI Spec |
| Integration | Architecture + CLAUDE.md + Data Model + API Spec | Stakeholder |
| Prioritization | Work Items + Stakeholder + Persona | Architecture |
| UI Mockup | UI Spec + CLAUDE.md | API Spec, Persona, Component Examples |
| ADR Compilation | ADR files + `.ai-framework/templates/` | — |
| DDR Compilation | DDR files (+ optional profile) | `.ai-framework/templates/` |

### Generate Tasks

#### AI agents (Claude Code, etc.)

1. Read the required files listed in the CLAUDE.md routing table for your task type
2. Read the prompt template from `.ai-framework/prompts/` — it defines the output structure (Output Format section), quality criteria (Constraints), and validation checklist (Post-Generation Checklist)
3. Generate the deliverable directly, using the file contents as context — no XML assembly needed

#### Chat workflows (manual copy-paste)

1. Open the relevant prompt template from `.ai-framework/prompts/`
2. Copy the XML template structure
3. Fill in the `<context>` sections by pasting from your documentation
4. Fill in the task-specific details (feature description, bug report, refactoring scope)
5. Paste into Claude and iterate

<details>
<summary>Example — generating feature tasks via chat workflow</summary>

```xml
<task-generation-request>
  <context>
    <stakeholder-definition>
      [Paste relevant sections from docs/stakeholder-definition.md]
    </stakeholder-definition>
    <code-conventions>
      [Paste from CLAUDE.md]
    </code-conventions>
    <data-model>
      [Paste relevant entities from docs/data-model.md]
    </data-model>
    <api-spec>
      [Paste relevant endpoints from docs/api-spec.md]
    </api-spec>
  </context>

  <task-type>New Feature</task-type>
  <request>
    Add user authentication with email/password login
  </request>
</task-generation-request>
```

</details>

The AI will return structured tasks with IDs, descriptions, acceptance criteria, complexity estimates, and file lists.

---

## Phase 4: Maintain

Documentation drifts. Keep it alive.

**Review cadence:**

| Document | Review When |
|----------|-------------|
| **CLAUDE.md** | After establishing new patterns or conventions |
| **Stakeholder Definition** | When a feature ships or strategy changes |
| **Architecture** | After adding/removing components or services |
| **Persona** | Quarterly, or after significant user feedback |
| **UI Specification** | After adding/changing screens, components, or design tokens |
| **HTML Mockups** | After design token changes, screen layout changes, or stakeholder feedback |
| **Work Items** | Update status when tasks are generated, in progress, or completed |

**Key rule:** If a task touches a document's area of concern, update the document in the same PR.

For full maintenance guidance, see [`.ai-framework/guides/maintenance.md`](maintenance.md).

---

## Quick Reference

```
1.  Copy scaffold into project                        →  Phase 1
1.5 (Optional) Compile ADRs → pre-fill conventions   →  Phase 1 (Step 0.5)
1.6 (Optional) Compile DDRs → pre-fill design system →  Phase 1 (Step 0.6)
2.  Fill core templates (30-60 min)                   →  Phase 2 (Steps 1-4)
3.  Generate data model + API spec (40 min)           →  Phase 2 (Steps 5-6)
4.  Generate UI specification (20 min)                →  Phase 2 (Step 7)
4.5 (Optional) Create HTML mockups for key screens    →  Phase 2 (Step 7.5)
5.  Write work items (Feature/Bug/Improvement)        →  Phase 2.5
6.  Pick prompt template + add context                →  Phase 3
7.  Generate tasks with AI                            →  Phase 3
8.  Keep docs updated                                 →  Phase 4
```

**Stuck?** Check the full templates in `.ai-framework/templates/` for detailed guidance on any section. Each scaffold file is a simplified version of its corresponding template.
