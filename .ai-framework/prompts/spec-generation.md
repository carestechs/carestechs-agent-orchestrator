# Spec Generation Prompt

> **Purpose**: Generate a Data Model or API Specification document from existing strategic documentation. Use this prompt when you have stakeholder definitions, architecture docs, and code conventions, and need to derive the data model or API endpoints before generating implementation tasks.
>
> **When to use**: After completing the 4 core templates (Persona, Stakeholder, Architecture, CLAUDE.md) and before generating feature tasks. This fills the gap between "what we're building" and "how to break it into tasks."

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "Spec generation"
2. Read the output format template from `.ai-framework/templates/` for the target spec type
3. Use the **Guidance** and **Output Format** sections below to shape the generated document
4. Apply the **Constraints** and **Post-Generation Checklist** to validate quality

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<spec-generation-request>

<context>

<stakeholder-definition>
<!-- REQUIRED: Include full stakeholder definition — scope lock, user flows,
     backend responsibilities, and success metrics are critical for deriving entities -->
[Paste full stakeholder-definition.md content]
</stakeholder-definition>

<architecture>
<!-- REQUIRED: Include full architecture doc — module boundaries, tech stack,
     and data flow inform entity ownership and API structure -->
[Paste full ARCHITECTURE.md content]
</architecture>

<code-conventions>
<!-- REQUIRED: Include full CLAUDE.md — naming conventions, database conventions,
     and patterns directly constrain how entities and endpoints are defined -->
[Paste full CLAUDE.md content]
</code-conventions>

<persona>
<!-- OPTIONAL: Include if user-facing features need persona context -->
[Paste persona details]
</persona>

</context>

<spec-type>[Data Model | API Specification]</spec-type>

<request>
Generate a complete [Data Model | API Specification] document for this project.

Use the template structure from `.ai-framework/templates/[data-model.md | api-spec.md]`
as the output format.

Derive all content from the context documents provided:
- Entities/endpoints from the stakeholder scope and user flows
- Module ownership from the architecture doc
- Naming and conventions from CLAUDE.md
- Field types and constraints from domain rules in the stakeholder definition

Do not invent features or entities beyond the defined scope lock.
</request>

<guidance>
## For Data Model Generation

When deriving entities:
1. Read the Scope Lock — every in-scope feature implies at least one entity
2. Read the User Flow — each phase implies data that must be persisted
3. Read Backend Responsibilities — these map to service operations on entities
4. Read the Architecture — module boundaries determine entity ownership
5. Read CLAUDE.md — database conventions dictate naming, types, and patterns

For each entity, determine:
- Which module owns it (from Architecture module list)
- What fields it needs (from user flow steps and feature descriptions)
- What relationships it has (from flow transitions and cross-references)
- What constraints apply (from business rules in stakeholder definition)
- What indexes are needed (from query patterns implied by the UI)

Cross-module references:
- Use ID-only references (no navigation properties across modules)
- Note which module owns the reference and which module owns the target
- Document the purpose of each cross-module reference

## For API Specification Generation

When deriving endpoints:
1. Start with entities — each entity typically needs CRUD endpoints
2. Read the User Flow — each user action implies an API call
3. Map endpoints to modules — the owning module exposes the endpoint
4. Read CLAUDE.md — API conventions dictate route patterns, response format, and auth
5. Consider the frontend — what data does each page/view need?

For each endpoint, determine:
- HTTP method and path (from REST conventions + entity name)
- Auth requirements (from architecture security section)
- Request DTO fields (from entity fields that are user-editable)
- Response DTO fields (from what the frontend needs to display)
- Status codes (from error handling patterns in CLAUDE.md)
- Pagination (for list endpoints)

Endpoint grouping:
- Group by module, then by resource within the module
- Order: List → Create → Get → Update → Delete (standard CRUD order)
- Add non-CRUD actions as sub-resources (e.g., POST /api/tasks/{id}/assign)
</guidance>

<constraints>
- Follow all naming conventions from CLAUDE.md exactly
- Respect module boundaries from the architecture document
- Only include entities/endpoints for features within the scope lock
- Use the response envelope format defined in CLAUDE.md or architecture docs
- Apply database conventions (snake_case tables, UUID PKs, TIMESTAMPTZ, etc.)
- Include all standard audit fields (created_at, updated_at) on every entity
</constraints>

<output-format>
## Output Requirements

Generate a complete document following the template structure:

### For Data Model:
Use the section structure from `.ai-framework/templates/data-model.md`:
1. Overview with modeling decisions table
2. Module ownership table
3. Full entity definitions with field tables, indexes, and business rules
4. Relationships (1:N, M:N, cross-module)
5. Enums and value types
6. Database conventions summary
7. Entity-relationship diagram (ASCII)

### For API Specification:
Use the section structure from `.ai-framework/templates/api-spec.md`:
1. Overview with API decisions table
2. Common conventions (envelope, errors, auth, pagination)
3. All endpoints grouped by module with full details
4. Shared DTOs
5. Endpoint summary table

### Quality Checks:
- [ ] Every in-scope feature from stakeholder definition has corresponding entities/endpoints
- [ ] Every entity belongs to exactly one module
- [ ] Cross-module references use IDs only
- [ ] All fields have explicit types and constraints
- [ ] Naming follows CLAUDE.md conventions throughout
- [ ] No entities/endpoints for out-of-scope features
</output-format>

</spec-generation-request>
```

---

## Context Selection Guide

### What to Include

| Document | Priority | What to Include |
|----------|----------|-----------------|
| Stakeholder Definition | Required | Full document — scope, flows, backend responsibilities |
| Architecture | Required | Full document — modules, tech stack, security |
| CLAUDE.md | Required | Full document — conventions, patterns, naming |
| Persona | Optional | Include for user-facing entity/endpoint decisions |

**All three required documents should be included in full.** Unlike feature task generation where you excerpt relevant sections, spec generation needs the complete picture to derive a comprehensive model.

---

## Workflow

### Step 1: Generate Data Model First

The data model should be generated before the API spec because:
- Entities inform endpoint structure (CRUD per entity)
- Field definitions inform request/response DTOs
- Relationships inform nested routes and query parameters

### Step 2: Generate API Spec Second

With the data model in hand, add it as extra context:

```xml
<spec-generation-request>

<context>
  <!-- Same context as before, plus: -->
  <data-model>
  [Paste the data-model.md you generated in Step 1]
  </data-model>
</context>

<spec-type>API Specification</spec-type>

<request>
Generate a complete API Specification document for this project.
Use the data model as the source of truth for entities, fields, and relationships.
</request>

</spec-generation-request>
```

### Step 3: Review and Validate

After generating each document, validate against the source docs:
- Does every scope-lock item have representation?
- Are module boundaries consistent with the architecture?
- Do naming conventions match CLAUDE.md?

---

## Example: Generating a Data Model

```xml
<spec-generation-request>

<context>

<stakeholder-definition>
## Executive Summary
The product is an internal project management web application.

## Scope Lock (V1)
Included: Projects, Tasks with Kanban/List/Gantt views, Team members, Labels, Comments
Excluded: Time tracking, Billing, External integrations, Mobile app

## User Flow
Phase 1: Login via Google OAuth
Phase 2: Select or create project, manage members
Phase 3: Create tasks, assign to members, set status/priority/dates
Phase 4: View tasks in Kanban (drag columns), List (sortable table), Gantt (timeline)
Phase 5: Collaborate via comments and file attachments on tasks
</stakeholder-definition>

<architecture>
## Modules
- TecherPlannr.Auth → handles Google OAuth, JWT
- TecherPlannr.Users → user profiles, roles
- TecherPlannr.Projects → projects, membership
- TecherPlannr.Tasks → tasks, labels, comments, attachments
- TecherPlannr.Shared → common interfaces, base entity
</architecture>

<code-conventions>
## Database Conventions
- snake_case for tables (plural) and columns
- UUID primary keys
- TIMESTAMPTZ for all date/time columns
- Soft deletes with deleted_at where appropriate
## Naming
- Entity class: PascalCase (TaskItem, not Task)
- DbContext: {Module}DbContext
</code-conventions>

</context>

<spec-type>Data Model</spec-type>

<request>
Generate a complete Data Model document for this project management application.
Derive all entities from the scope lock and user flows.
Assign each entity to the correct module per the architecture.
</request>

</spec-generation-request>
```

---

## Post-Generation Checklist

After the AI generates a spec document, verify:

### For Data Model:
- [ ] Every feature in the scope lock maps to at least one entity
- [ ] Each entity has an explicit module owner
- [ ] All field types are specific (not vague — `string(200)` not just `string`)
- [ ] Relationships are fully defined with cascade behaviors
- [ ] Cross-module references are ID-only (no navigation properties)
- [ ] Enums are defined with all values listed
- [ ] Standard audit fields (id, created_at, updated_at) are on every entity
- [ ] Indexes are defined for fields used in lookups and filters
- [ ] Business rules are documented per entity
- [ ] Entity-relationship diagram matches the field/relationship definitions

### For API Specification:
- [ ] Every entity has appropriate CRUD endpoints
- [ ] Non-CRUD actions (assign, move, upload) are covered
- [ ] Auth requirements are specified for every endpoint
- [ ] Request DTOs only include user-editable fields (not id, created_at, etc.)
- [ ] Response DTOs include all fields the frontend needs
- [ ] List endpoints support pagination
- [ ] Error status codes are comprehensive (400, 401, 403, 404, 409 as applicable)
- [ ] Response envelope format is consistent across all endpoints
- [ ] Endpoint summary table is complete and matches detailed definitions
