# Compile ADRs into Project Templates

> **Purpose**: Read selected Architecture Decision Records (ADRs) — their Decision, Rationale, and Constraints sections — and derive template content using derivation rules. Output pre-filled template sections that can be pasted into project docs, so you only need to add project-specific content (entities, endpoints, screens, etc.).
>
> **When to use**: When bootstrapping a new project with a known tech stack and existing architectural decisions from a shared ADR repo. Run this before filling in templates (between Phase 1 and Phase 2 in `getting-started.md`).

---

## How to Use This Prompt

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read each ADR file the user specifies (from the ADR repo or local paths)
2. For each ADR, read the Category, Decision, Rationale, and Constraints sections
3. Apply the Derivation Rules (below) to generate content for each target template section
4. Read the target templates from `.ai-framework/templates/` to get the correct heading structure
5. Merge derived content into the template sections and output the result

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your ADR files into the `<adrs>` block, and submit to Claude.

---

## ADR Derivation Rules

These rules teach the AI how to transform generic ADR content (Decision, Rationale, Constraints) into template sections. No "Compiles To" blocks are needed in the ADRs — the rules derive everything from the standard ADR format.

### Rule 1: Every ADR → ARCHITECTURE.md Key Architectural Decisions

**Input:** ADR title + Decision + first Rationale bullet
**Output:** One row in the Key Architectural Decisions table

| Decision | Choice | Rationale |
|----------|--------|-----------|
| *ADR title (short form)* | *Decision statement (condensed)* | *First Rationale bullet (condensed)* |

*Every* selected ADR produces exactly one row, regardless of category.

### Rule 2: Category Targeting

The ADR's **Category** field determines which additional template documents it targets, beyond ARCHITECTURE.md:

| Category | Target Documents | Target Sections |
|----------|-----------------|-----------------|
| `dotnet` | CLAUDE.md | Patterns to Follow, Anti-Patterns to Avoid |
| `python` | CLAUDE.md | Patterns to Follow, Anti-Patterns to Avoid |
| `angular` | CLAUDE.md | Patterns to Follow, Anti-Patterns to Avoid |
| `react` | CLAUDE.md | Patterns to Follow, Anti-Patterns to Avoid |
| `database` | CLAUDE.md, data-model.md | Patterns to Follow; Key Modeling Decisions, Database Conventions |
| `api` | CLAUDE.md, api-spec.md | Patterns to Follow; Key API Decisions, Common Conventions |
| `ai` | CLAUDE.md, ARCHITECTURE.md | Patterns to Follow, Anti-Patterns to Avoid; Key Architectural Decisions |
| *(unknown)* | CLAUDE.md | Patterns to Follow, Anti-Patterns to Avoid *(fallback)* |

### Rule 3: Constraints → Patterns to Follow / Anti-Patterns to Avoid

**Input:** Constraint bullets from "Constraints (non-negotiable for AI)"
**Output:** CLAUDE.md entries

- Constraints with **positive phrasing** (MUST, ALWAYS, use, ensure, each, every, all) → **Patterns to Follow** bullet
- Constraints with **negative phrasing** (NEVER, MUST NOT, do not, no direct, avoid) → **Anti-Patterns to Avoid** bullet

Format each derived entry as: `- **[Short label]:** [constraint text]`

When a constraint contains both a positive and negative aspect, split it into one Pattern entry and one Anti-Pattern entry.

### Rule 4: Naming Conventions

**Input:** Constraint or Decision bullets that describe naming rules (file names, class names, table names, column names, URL paths, etc.)
**Output:** CLAUDE.md Naming Conventions table rows

| Element | Convention | Example |
|---------|------------|---------|
| *What is being named* | *The naming rule* | *An example from the ADR or inferred* |

Only produce rows when the ADR explicitly describes naming rules. Do not invent naming conventions.

### Rule 5: database-category ADRs → data-model.md

**Input:** ADRs with `Category: database`
**Output:**
- **Key Modeling Decisions** table — one row per ADR (same format as Rule 1: title, decision, rationale)
- **Database Conventions** — positive-phrased constraints become convention entries

### Rule 6: api-category ADRs → api-spec.md

**Input:** ADRs with `Category: api`
**Output:**
- **Key API Decisions** table — one row per ADR (same format as Rule 1: title, decision, rationale)
- **Common Conventions** — constraints about envelope format, auth, pagination, error responses, versioning, etc. become convention entries

---

## Prompt Template (Chat Workflow)

```xml
<adr-compilation-request>

<adrs>
<!-- REQUIRED: Paste or list the ADR files to compile.
     Each ADR should follow the standard ADR format with Category, Decision,
     Rationale, and Constraints sections. No "Compiles To" blocks needed. -->
[Paste ADR file contents here, or list file paths for agents to read]
</adrs>

<templates>
<!-- RECOMMENDED: Include the target templates so the output matches their structure.
     Agents read these directly from .ai-framework/templates/. -->
[Paste relevant templates from .ai-framework/templates/ if using chat workflow]
</templates>

<request>
## Step 0: Validate Dependencies

Before compiling, check each ADR's `Requires` and `Conflicts with` fields:

1. **Missing dependencies**: If an ADR lists a `Requires` that is NOT in the selected ADR set, emit a **warning** at the top of the output: `⚠ [adr-file] requires [missing-adr] which is not in the selected set. Include it or remove the dependent ADR.`
2. **Conflicting ADRs**: If two selected ADRs list each other in `Conflicts with`, emit an **error** at the top of the output: `❌ [adr-a] conflicts with [adr-b]. Remove one before compiling.`
3. **If using a stack profile**: Read the stack profile to determine which ADRs are Required/Recommended/Optional. Warn if any Required ADR from the profile is missing from the selected set.

Proceed with compilation only after listing all warnings/errors. If there are errors (conflicts), stop and ask the user to resolve them. Warnings (missing dependencies) can proceed but should be surfaced.

## Step 1: Derive and Compile

Read all provided ADR files and apply the Derivation Rules to generate
pre-filled template fragments for each target document.

For each target document:
1. Use the exact heading structure from the corresponding template in `.ai-framework/templates/`
2. Apply Rule 1 (every ADR → ARCHITECTURE.md row)
3. Apply Rule 2 (category targeting) to determine additional target documents
4. Apply Rules 3-6 to derive Patterns, Anti-Patterns, Naming Conventions, and category-specific content
5. Merge content from all ADRs that target the same section (e.g., combine all Naming Convention rows)
6. De-duplicate if multiple ADRs contribute the same rule
7. Mark remaining project-specific sections with `<!-- TODO: [description] -->` scaffolds
8. Preserve the ADR source in a comment (e.g., `<!-- from: modular-monolith.md -->`) for traceability

Output ONLY the sections that derivation rules fill, plus TODO scaffolds for the rest.
Do not generate project-specific content (overview, entities, endpoints, screens).
</request>

<output-format>
## Output Structure

Generate one block per target document. Each block contains merged sections from all
applicable ADRs (derived via the Derivation Rules), with TODO placeholders for project-specific content.

### Output: CLAUDE.md sections

Merge into these sections from the `claude-md.md` template:
- **Patterns to Follow** — derived from positive-phrased Constraints (Rule 3)
- **Anti-Patterns to Avoid** — derived from negative-phrased Constraints (Rule 3)
- **Naming Conventions** — derived from naming-related Constraints/Decisions (Rule 4)

Leave all other sections (Project Overview, Common Commands, Key Directories, etc.) as TODO scaffolds.

### Output: ARCHITECTURE.md sections

Merge into:
- **Key Architectural Decisions** table — one row per ADR (Rule 1)

Leave all other sections (System Overview, Component Diagram, Data Flow, etc.) as TODO scaffolds.

### Output: data-model.md sections

Merge into (only for database-category ADRs, Rule 5):
- **Key Modeling Decisions** table — one row per database-category ADR
- **Database Conventions** — derived from positive-phrased Constraints

Leave all other sections (Entity definitions, Relationships, Enums, etc.) as TODO scaffolds.

### Output: api-spec.md sections

Merge into (only for api-category ADRs, Rule 6):
- **Key API Decisions** table — one row per api-category ADR
- **Common Conventions** — derived from Constraints about envelope, auth, pagination, errors

Leave all other sections (Endpoint definitions, DTOs, etc.) as TODO scaffolds.

## Quality Checks

- [ ] Every ADR has a row in ARCHITECTURE.md Key Architectural Decisions (Rule 1)
- [ ] Every ADR's Constraints appear as Patterns or Anti-Patterns in CLAUDE.md (Rule 3)
- [ ] database-category ADRs have entries in data-model.md (Rule 5)
- [ ] api-category ADRs have entries in api-spec.md (Rule 6)
- [ ] No duplicate rows in merged tables
- [ ] Heading structure matches the templates in `.ai-framework/templates/`
- [ ] Project-specific sections have TODO scaffolds (not invented content)
- [ ] ADR sources are traceable via comments
</output-format>

</adr-compilation-request>
```

---

## ADR Format Reference

Each ADR file in the ADR repo follows this generic structure. The compilation prompt derives all template content from Decision, Rationale, and Constraints — no "Compiles To" section is needed:

```markdown
# [Decision Title]

**Category:** dotnet | python | angular | react | database | api | ai
**Status:** Active
**Requires:** [ADR file paths this decision depends on — omit if none]
**Conflicts with:** [ADR file paths that are mutually exclusive — omit if none]

## Decision

[1-2 sentences: what was decided]

## Rationale

- [Why this decision was made]
- [What alternatives were considered]

## Constraints (non-negotiable for AI)

- [Hard rule 1 — the AI must never violate this]
- [Hard rule 2]
```

The derivation rules use Category to determine target documents and Constraint phrasing (MUST/NEVER) to determine whether content becomes a Pattern or Anti-Pattern.

---

## Context Selection Guide

### What to Include

| Document | Priority | What to Include |
|----------|----------|-----------------|
| ADR files | Required | All ADR files the user wants to compile |
| `.ai-framework/templates/` | Recommended | Target templates for correct heading structure (agents read these directly) |

### What NOT to Include

- Project docs (`docs/`) — ADR compilation pre-fills templates, it doesn't read existing project docs
- Prompt templates for other task types — this prompt is self-contained

---

## Example: Compiling a .NET + Angular Stack

### Input ADRs (generic format — no "Compiles To" blocks)

```xml
<adr-compilation-request>

<adrs>
<!-- Selected ADRs for a .NET modular monolith + Angular project -->

# Modular Monolith
**Category:** dotnet
**Status:** Active

## Decision
Use a modular monolith architecture with separate C# class library projects per module.

## Rationale
- Compile-time module boundaries without microservice overhead
- Each team can work independently within their module

## Constraints (non-negotiable for AI)
- Modules MUST NOT reference each other directly (except through Shared interfaces)
- Each module MUST own its own controllers, services, DTOs, and data access
- The API host MUST be thin — only DI wiring, middleware, and pipeline config
- NEVER place controllers, services, or entities in the Api host project

---

# UUID Primary Keys
**Category:** database
**Status:** Active

## Decision
Use UUIDs for all primary keys, generated server-side.

## Rationale
- No sequential ID leaks, safe for distributed systems
- Avoids integer overflow concerns at scale

## Constraints (non-negotiable for AI)
- All entities MUST use `Guid` for primary keys
- Primary keys MUST be generated server-side, never client-supplied
- NEVER use auto-increment integer IDs

---

# REST Envelope Format
**Category:** api
**Status:** Active

## Decision
All API responses use a standard envelope: `{ data, errors, metadata }`.

## Rationale
- Consistent contract for frontend consumers
- Enables uniform error handling across all endpoints

## Constraints (non-negotiable for AI)
- All API responses MUST use the `{ data, errors, metadata }` envelope
- Errors MUST use the array format `[{ code, message, field? }]`
- NEVER return raw objects without the envelope wrapper
- Collection endpoints MUST include pagination metadata in the `metadata` field
</adrs>

<request>
Compile these ADRs into pre-filled template sections.
</request>

</adr-compilation-request>
```

### Derived Output (via Derivation Rules)

**ARCHITECTURE.md — Key Architectural Decisions** *(Rule 1: every ADR → one row)*

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture style | Modular monolith | Compile-time module boundaries without microservice overhead |
| Primary keys | UUID (server-generated) | No sequential ID leaks, safe for distributed systems |
| API response format | Standard envelope `{ data, errors, metadata }` | Consistent contract for frontend consumers |

**CLAUDE.md — Patterns to Follow** *(Rule 3: positive-phrased constraints)*

- **Module isolation:** Each module MUST own its own controllers, services, DTOs, and data access <!-- from: modular-monolith.md -->
- **Thin API host:** The API host MUST be thin — only DI wiring, middleware, and pipeline config <!-- from: modular-monolith.md -->
- **UUID primary keys:** All entities MUST use `Guid` for primary keys, generated server-side <!-- from: uuid-primary-keys.md -->
- **Response envelope:** All API responses MUST use the `{ data, errors, metadata }` envelope <!-- from: rest-envelope.md -->
- **Structured errors:** Errors MUST use the array format `[{ code, message, field? }]` <!-- from: rest-envelope.md -->
- **Pagination metadata:** Collection endpoints MUST include pagination metadata in the `metadata` field <!-- from: rest-envelope.md -->

**CLAUDE.md — Anti-Patterns to Avoid** *(Rule 3: negative-phrased constraints)*

- **No cross-module references:** Modules MUST NOT reference each other directly (except through Shared interfaces) <!-- from: modular-monolith.md -->
- **No code in Api host:** NEVER place controllers, services, or entities in the Api host project <!-- from: modular-monolith.md -->
- **No integer IDs:** NEVER use auto-increment integer IDs <!-- from: uuid-primary-keys.md -->
- **No raw responses:** NEVER return raw objects without the envelope wrapper <!-- from: rest-envelope.md -->

**data-model.md — Key Modeling Decisions** *(Rule 5: database-category ADR)*

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary keys | UUID (server-generated) | No sequential ID leaks, safe for distributed systems |

**data-model.md — Database Conventions** *(Rule 5: database-category constraints)*

- All entities use `Guid` for primary keys, generated server-side <!-- from: uuid-primary-keys.md -->

**api-spec.md — Key API Decisions** *(Rule 6: api-category ADR)*

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Response format | Standard envelope `{ data, errors, metadata }` | Consistent contract for frontend consumers |

**api-spec.md — Common Conventions** *(Rule 6: api-category constraints)*

- All responses use the `{ data, errors, metadata }` envelope <!-- from: rest-envelope.md -->
- Errors use the array format `[{ code, message, field? }]` <!-- from: rest-envelope.md -->
- Collection endpoints include pagination metadata in the `metadata` field <!-- from: rest-envelope.md -->

---

## Post-Compilation Checklist

After the AI generates compiled template sections:

- [ ] Every ADR has a row in ARCHITECTURE.md Key Architectural Decisions
- [ ] Every ADR's Constraints appear as Patterns or Anti-Patterns in CLAUDE.md
- [ ] database-category ADRs have entries in data-model.md
- [ ] api-category ADRs have entries in api-spec.md
- [ ] No duplicate rows in merged tables
- [ ] TODO scaffolds exist for all project-specific sections
- [ ] Output heading structure matches `.ai-framework/templates/`
- [ ] Paste compiled sections into your project docs and fill in the TODO scaffolds
