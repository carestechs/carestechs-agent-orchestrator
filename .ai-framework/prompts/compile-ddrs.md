# Compile DDRs into Project Templates

> **Purpose**: Read selected Design Decision Records (DDRs) — their Decision, Rationale, and Constraints sections — and derive template content using derivation rules. Output pre-filled template sections that can be pasted into project docs, so the Design System, component patterns, and state handling are consistent before you write a single line of UI code.
>
> **When to use**: When bootstrapping a new project with a desired visual identity and shared design decisions from a shared DDR repo. Run this between applying ADRs (Step 0.5) and filling in templates (Phase 2) in `getting-started.md`.

---

## How to Use This Prompt

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read each DDR file the user specifies (from the DDR repo or local paths)
2. If the user specifies a **profile**, read the profile file first to get the DDR list and override values
3. For each DDR, read the Category, Decision, Rationale, Constraints, and Examples sections
4. Apply the Derivation Rules (below) to generate content for each target template section
5. Read the target templates from `.ai-framework/templates/` to get the correct heading structure
6. Merge derived content into the template sections and output the result

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your DDR files into the `<ddrs>` block, and submit to Claude.

---

## DDR Derivation Rules

These rules teach the AI how to transform generic DDR content (Decision, Rationale, Constraints, Examples) into template sections. The rules derive everything from the standard DDR format — no extra metadata is needed in the DDRs.

### Rule 1: Every DDR → ui-specification.md Key UI Decisions

**Input:** DDR title + Decision + first Rationale bullet
**Output:** One row in the Key UI Decisions table (Section 1.2)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| *DDR title (short form)* | *Decision statement (condensed)* | *First Rationale bullet (condensed)* |

*Every* selected DDR produces exactly one row, regardless of category.

### Rule 2: Category Targeting

The DDR's **Category** field determines which additional template sections it targets, beyond the Key UI Decisions table:

| Category | Target Documents | Target Sections |
|----------|-----------------|-----------------|
| `design-tokens` | ui-specification.md | Brand Colors (2.1), Typography Scale (2.2), Spacing Scale (2.3) |
| `components` | ui-specification.md, CLAUDE.md | Component Library (2.4); Design Patterns to Follow |
| `layout` | ui-specification.md | Shared Layouts (Section 4) constraints |
| `interactions` | CLAUDE.md | Design Patterns to Follow, Design Anti-Patterns to Avoid |
| `states` | ui-specification.md | State Patterns (2.5) |
| `accessibility` | CLAUDE.md | Design Patterns to Follow, Design Anti-Patterns to Avoid |
| `responsive` | ui-specification.md | Responsive Breakpoints (2.6) |

### Rule 3: Constraints → CLAUDE.md Design Patterns / Anti-Patterns

**Input:** Constraint bullets from "Constraints (non-negotiable for AI)"
**Output:** CLAUDE.md entries under the **Design Patterns** subsection

- Constraints with **positive phrasing** (MUST, ALWAYS, use, ensure, each, every, all) → **Design Patterns to Follow** bullet
- Constraints with **negative phrasing** (NEVER, MUST NOT, do not, no direct, avoid) → **Design Anti-Patterns to Avoid** bullet

Format each derived entry as: `- **[Short label]:** [constraint text] (from: [ddr-filename])`

When a constraint contains both a positive and negative aspect, split it into one Pattern entry and one Anti-Pattern entry.

### Rule 4: design-tokens DDRs → ui-specification.md Design System

**Input:** DDRs with `Category: design-tokens`
**Output:**

- **Brand Colors** (2.1) — Populate the color token table from color-palette DDR. If a profile provides override hex values, use those instead of placeholders.
- **Typography Scale** (2.2) — Populate font family, size, weight, line-height from typography-scale DDR. Profile overrides apply.
- **Spacing Scale** (2.3) — Populate spacing token table from spacing-scale DDR.
- Additional design-tokens DDRs (shadows, border-radius, opacity, transitions) → add rows to an **Additional Tokens** table below 2.3, or incorporate into existing tables where natural.

### Rule 5: components DDRs → ui-specification.md Component Library + CLAUDE.md Design Patterns

**Input:** DDRs with `Category: components`
**Output:**

- **Component Library** (2.4) — One row per component DDR with the component name, variants, and key customization notes from the Decision section
- **CLAUDE.md Design Patterns to Follow** — Positive constraints from each component DDR
- **CLAUDE.md Design Anti-Patterns to Avoid** — Negative constraints from each component DDR
- **Component Examples Appendix** — Collect all Examples sections from component DDRs into a reference appendix (for use by the mockup prompt)

### Rule 6: states DDRs → ui-specification.md State Patterns (Section 2.5)

**Input:** DDRs with `Category: states`
**Output:**

- **State Patterns** table (new Section 2.5):

| State | Pattern | Key Constraints | Example Reference |
|-------|---------|----------------|-------------------|
| *Loading (skeleton)* | *Skeleton placeholders matching content layout* | *From skeleton-loading DDR constraints* | *See Component Examples Appendix* |
| *Loading (action)* | *Inline spinner for button/form actions* | *From spinner DDR constraints* | *See Component Examples Appendix* |
| *Empty* | *Centered heading + description + CTA* | *From empty-state DDR constraints* | *See Component Examples Appendix* |
| *Error* | *Inline banner with retry / full-page error* | *From error-state DDR constraints* | *See Component Examples Appendix* |

### Rule 7: layout DDRs → ui-specification.md Shared Layouts

**Input:** DDRs with `Category: layout`
**Output:**

- **Shared Layouts** (Section 4) — Add constraint annotations to the existing layout section:
  - Sidebar width, collapse behavior, persistence from sidebar-navigation DDR
  - Content max-width and centering from content-width DDR
  - Page-level padding values from page-padding DDR

Format as constraint comments within the layout descriptions.

### Rule 8: responsive DDRs → ui-specification.md Responsive Breakpoints (Section 2.6)

**Input:** DDRs with `Category: responsive`
**Output:**

- **Responsive Breakpoints** table (new Section 2.6):

| Breakpoint | Width | Tailwind Prefix | Primary Use |
|------------|-------|-----------------|-------------|
| *Mobile* | *< 640px* | *(base)* | *Single column, stacked layout* |
| *Tablet* | *640px - 1023px* | *sm:, md:* | *Collapsed sidebar, 2-column* |
| *Desktop* | *1024px+* | *lg:, xl:* | *Full sidebar, multi-column* |

- Include the chosen responsive strategy (mobile-first or desktop-first) as a note below the table.

### Rule 9: Examples Sections → Component Examples Appendix

**Input:** All DDRs with non-empty Examples sections (typically `components` and `states` categories)
**Output:**

- A **Component Examples Appendix** section — a collected reference of all HTML/Tailwind examples, organized by category, that the mockup generation prompt can reference.
- Each example block should include the DDR source file name and the example title.

---

## Prompt Template (Chat Workflow)

```xml
<ddr-compilation-request>

<ddrs>
<!-- REQUIRED: Paste or list the DDR files to compile.
     Each DDR should follow the standard DDR format with Category, Decision,
     Rationale, Constraints, and Examples sections.
     Alternatively, specify a profile file that lists the DDRs to include. -->
[Paste DDR file contents here, or list file paths for agents to read]
</ddrs>

<profile>
<!-- OPTIONAL: If using a profile, paste the profile file here.
     The profile lists which DDRs to include and provides override values
     for design tokens (colors, fonts, spacing). -->
[Paste profile contents, or specify profile file path for agents to read]
</profile>

<templates>
<!-- RECOMMENDED: Include the target templates so the output matches their structure.
     Agents read these directly from .ai-framework/templates/. -->
[Paste relevant templates from .ai-framework/templates/ if using chat workflow]
</templates>

<request>
## Step 0: Validate Dependencies

Before compiling, check each DDR's `Requires` and `Conflicts with` fields:

1. **Missing dependencies**: If a DDR lists a `Requires` that is NOT in the selected DDR set, emit a **warning** at the top of the output: `⚠ [ddr-file] requires [missing-ddr] which is not in the selected set. Include it or remove the dependent DDR.`
2. **Conflicting DDRs**: If two selected DDRs list each other in `Conflicts with`, emit an **error** at the top of the output: `❌ [ddr-a] conflicts with [ddr-b]. Remove one before compiling.`
3. **If using a profile**: Read the profile to determine which DDRs are included. Warn if any DDR listed in the profile is missing from the DDR repo.

Proceed with compilation only after listing all warnings/errors. If there are errors (conflicts), stop and ask the user to resolve them. Warnings (missing dependencies) can proceed but should be surfaced.

## Step 1: Apply Profile Overrides (if applicable)

If a profile is provided:
1. Read the profile's override values for design tokens (colors, fonts, spacing, etc.)
2. Use profile values instead of DDR placeholder/default values when populating token tables
3. Note the profile name in the output header for traceability

## Step 2: Derive and Compile

Read all provided DDR files and apply the Derivation Rules to generate
pre-filled template fragments for each target document.

For each target document:
1. Use the exact heading structure from the corresponding template in `.ai-framework/templates/`
2. Apply Rule 1 (every DDR → ui-specification.md Key UI Decisions row)
3. Apply Rule 2 (category targeting) to determine additional target sections
4. Apply Rules 3-9 to derive Design System tables, Patterns, Anti-Patterns, State Patterns, Responsive Breakpoints, and Component Examples
5. Merge content from all DDRs that target the same section (e.g., combine all design-tokens into the Design System section)
6. De-duplicate if multiple DDRs contribute the same rule
7. Mark remaining project-specific sections with `<!-- TODO: [description] -->` scaffolds
8. Preserve the DDR source in a comment (e.g., `<!-- from: color-palette.md -->`) for traceability

Output ONLY the sections that derivation rules fill, plus TODO scaffolds for the rest.
Do not generate project-specific content (screen inventory, screen specifications, shared components).
</request>

<output-format>
## Output Structure

Generate one block per target document. Each block contains merged sections from all
applicable DDRs (derived via the Derivation Rules), with TODO placeholders for project-specific content.

### Output: ui-specification.md sections

Merge into these sections from the `ui-specification.md` template:
- **Key UI Decisions** (1.2) — one row per DDR (Rule 1)
- **Brand Colors** (2.1) — from design-tokens/color-palette DDR (Rule 4), with profile overrides
- **Typography Scale** (2.2) — from design-tokens/typography-scale DDR (Rule 4), with profile overrides
- **Spacing Scale** (2.3) — from design-tokens/spacing-scale DDR (Rule 4)
- **Component Library** (2.4) — one row per components-category DDR (Rule 5)
- **State Patterns** (2.5) — from states-category DDRs (Rule 6)
- **Responsive Breakpoints** (2.6) — from responsive-category DDRs (Rule 8)
- **Shared Layouts** (4) — layout constraints from layout-category DDRs (Rule 7)

Leave all other sections (Screen Inventory, Screen Specifications, Shared Components) as TODO scaffolds.

### Output: CLAUDE.md sections

Merge into these sections from the `claude-md.md` template:
- **Design Patterns to Follow** — derived from positive-phrased Constraints (Rule 3), especially from components, interactions, and accessibility DDRs
- **Design Anti-Patterns to Avoid** — derived from negative-phrased Constraints (Rule 3)

Leave all other sections (Project Overview, Common Commands, Code Patterns, etc.) as TODO scaffolds.

### Output: Component Examples Appendix

Collect all Examples sections from DDRs (Rule 9):
- Organized by category (components, then states)
- Each example block labeled with source DDR file name
- HTML/Tailwind code blocks preserved exactly as written in the DDR

This appendix is referenced by the mockup generation prompt for visual consistency.

## Quality Checks

- [ ] Every DDR has a row in ui-specification.md Key UI Decisions (Rule 1)
- [ ] Every DDR's Constraints appear as Design Patterns or Anti-Patterns in CLAUDE.md (Rule 3)
- [ ] design-tokens DDRs populate the Design System tables (Rule 4)
- [ ] components DDRs populate Component Library + examples (Rule 5)
- [ ] states DDRs populate State Patterns table (Rule 6)
- [ ] layout DDRs annotate Shared Layouts section (Rule 7)
- [ ] responsive DDRs populate Responsive Breakpoints table (Rule 8)
- [ ] All Examples sections collected into Component Examples Appendix (Rule 9)
- [ ] Profile overrides applied to token values (if profile used)
- [ ] No duplicate rows in merged tables
- [ ] Heading structure matches the templates in `.ai-framework/templates/`
- [ ] Project-specific sections have TODO scaffolds (not invented content)
- [ ] DDR sources are traceable via comments
</output-format>

</ddr-compilation-request>
```

---

## DDR Format Reference

Each DDR file in the DDR repo follows this standard structure. The compilation prompt derives all template content from Decision, Rationale, Constraints, and Examples — no extra metadata is needed:

```markdown
# [Decision Title]

**Category:** design-tokens | components | layout | interactions | states | accessibility | responsive
**Status:** Active
**Requires:** [DDR file paths this decision depends on — omit if none]
**Conflicts with:** [DDR file paths that are mutually exclusive — omit if none]

## Decision

[1-2 sentences: what was decided]

## Rationale

- [Why this decision was made]
- [What alternatives were considered]

## Constraints (non-negotiable for AI)

- [Hard rule 1 — MUST/NEVER phrasing for compilation]
- [Hard rule 2]

## Examples

[Inline HTML/Tailwind code blocks — required for components and states categories]
```

The derivation rules use Category to determine target document sections and Constraint phrasing (MUST/NEVER) to determine whether content becomes a Pattern or Anti-Pattern.

---

## Profile Format Reference

A profile is a curated list of DDRs with specific token overrides for a visual identity:

```markdown
# [Profile Name]

**Description:** [One sentence describing the visual identity]
**Target:** [What kind of projects this profile is for]

## Included DDRs

[List of DDR file paths to compile]

## Token Overrides

[Override values for design tokens — colors, fonts, spacing]
[These values replace DDR defaults when compiling]
```

---

## Context Selection Guide

### What to Include

| Document | Priority | What to Include |
|----------|----------|-----------------|
| DDR files | Required | All DDR files the user wants to compile (or a profile that references them) |
| Profile file | Optional | A profile from the DDR repo for curated token values |
| `.ai-framework/templates/` | Recommended | Target templates for correct heading structure (agents read these directly) |

### What NOT to Include

- Project docs (`docs/`) — DDR compilation pre-fills templates, it doesn't read existing project docs
- ADR files — ADR compilation is a separate step using `compile-adrs.md`
- Prompt templates for other task types — this prompt is self-contained

---

## Example: Compiling the Corporate Clean Profile

### Input

The agent reads all 29 DDR files listed in `profiles/corporate-clean.md` plus the profile itself. Below is an abbreviated excerpt showing 3 representative DDRs and the profile:

```xml
<ddr-compilation-request>

<ddrs>
# Color Palette — Semantic Color Token System
**Category:** design-tokens
**Status:** Active
**Requires:** None
**Conflicts with:** None
## Decision
Use semantic color tokens (primary, secondary, success, warning, error, neutral)
referenced via Tailwind classes.
## Rationale
- Semantic tokens decouple brand colors from component markup, enabling theme changes
  by modifying token values rather than searching every file for hex values.
## Constraints (non-negotiable for AI)
- MUST reference color tokens in component markup, never raw hex values
- NEVER use raw hex values in component markup — always reference tokens via Tailwind classes

# Buttons — Variants, Sizes, and States
**Category:** components
**Status:** Active
**Requires:** ddrs/design-tokens/color-palette.md, ddrs/design-tokens/border-radius.md
**Conflicts with:** None
## Decision
Use 4 button variants: primary, secondary, ghost, destructive. 3 sizes: sm, md, lg.
## Rationale
- Four variants cover all interaction contexts without needing custom components.
## Constraints (non-negotiable for AI)
- MUST include focus-visible:ring-2 focus-visible:ring-offset-2 on all button variants
- MUST use rounded-md for all button sizes
- NEVER use more than one primary button per view section
## Examples
<!-- Primary button example -->
<button class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium
               bg-primary text-on-primary rounded-md hover:bg-primary/90
               focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-primary
               transition-colors duration-150">
  Save
</button>

# Breakpoints — Responsive Breakpoint System
**Category:** responsive
**Status:** Active
**Requires:** None
**Conflicts with:** None
## Decision
Use Tailwind default breakpoints: sm (640px), md (768px), lg (1024px), xl (1280px).
## Rationale
- Standard Tailwind breakpoints avoid custom config and align with documentation.
## Constraints (non-negotiable for AI)
- MUST use lg: as the primary desktop breakpoint (1024px+)
- NEVER define custom breakpoints when Tailwind defaults suffice

[... + 26 more DDR files from the corporate-clean profile ...]
</ddrs>

<profile>
# Corporate Clean Profile
**Description:** Professional design for B2B/enterprise applications
**Target:** Enterprise SaaS, admin dashboards, internal tools

## Token Overrides
### Colors
| Token | Hex | Tailwind | Usage |
|-------|-----|----------|-------|
| primary | #2563EB | blue-600 | Primary actions, active states |
| secondary | #7C3AED | purple-600 | Secondary actions, accents |
| success | #16A34A | green-600 | Success states |
| warning | #CA8A04 | yellow-600 | Warning states |
| error | #DC2626 | red-600 | Error states |

### Typography
| Property | Value |
|----------|-------|
| Heading font | Inter |
| Body font | Inter |
| h1 | 1.875rem (30px) / weight 700 / line-height 1.2 |
| body | 0.875rem (14px) / weight 400 / line-height 1.5 |

## Behavioral Overrides
- cards: Flat variant only (border, no shadow)
- buttons: No scale transforms, use hover:bg-primary/90 only
</profile>

<request>
Compile these DDRs with the corporate-clean profile overrides.
</request>

</ddr-compilation-request>
```

### Derived Output (via Derivation Rules)

**ui-specification.md — Key UI Decisions** *(Rule 1: every DDR → one row)*

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Color palette | Semantic tokens with blue-600 primary | Consistent branding across all components <!-- from: color-palette.md --> |
| Typography scale | Inter, rem-based, 14px body | Professional readability, information density <!-- from: typography-scale.md --> |
| Spacing scale | 4px base unit, Tailwind spacing | Consistent rhythm, utility-class alignment <!-- from: spacing-scale.md --> |
| Button variants | Primary/secondary/ghost/destructive × 3 sizes | All interaction contexts covered <!-- from: buttons.md --> |
| Card patterns | Flat variant (border-only) | Clean, professional appearance <!-- from: cards.md --> |
| Sidebar navigation | Fixed 256px sidebar, collapsible on tablet | Predictable navigation landmark <!-- from: sidebar-navigation.md --> |
| Breakpoints | Tailwind defaults, lg: as desktop | Standard Tailwind alignment <!-- from: breakpoints.md --> |
| ... | *(one row per remaining DDR)* | ... |

**ui-specification.md — Brand Colors** *(Rule 4: design-tokens → Design System, with profile overrides)*

| Token | Hex | Usage |
|-------|-----|-------|
| `primary` | #2563EB | Primary actions, active states, links <!-- from: color-palette.md, override: corporate-clean --> |
| `secondary` | #7C3AED | Secondary actions, accents <!-- from: color-palette.md, override: corporate-clean --> |
| `success` | #16A34A | Success states, confirmations <!-- from: color-palette.md, override: corporate-clean --> |
| `warning` | #CA8A04 | Warning states, alerts <!-- from: color-palette.md, override: corporate-clean --> |
| `error` | #DC2626 | Error states, destructive actions <!-- from: color-palette.md, override: corporate-clean --> |

**ui-specification.md — Typography Scale** *(Rule 4: design-tokens, with profile overrides)*

| Level | Size | Weight | Line-Height | Font | Usage |
|-------|------|--------|-------------|------|-------|
| `h1` | 1.875rem (30px) | 700 | 1.2 | Inter | Page titles <!-- override: corporate-clean --> |
| `h2` | 1.5rem (24px) | 600 | 1.3 | Inter | Section headings |
| `h3` | 1.25rem (20px) | 600 | 1.4 | Inter | Subsection headings |
| `body` | 0.875rem (14px) | 400 | 1.5 | Inter | Body text <!-- override: corporate-clean --> |
| `body-sm` | 0.8125rem (13px) | 400 | 1.5 | Inter | Dense text |
| `caption` | 0.75rem (12px) | 400 | 1.4 | Inter | Timestamps, metadata |

**ui-specification.md — Spacing Scale** *(Rule 4: design-tokens)*

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 0.25rem (4px) | Tight internal gaps <!-- from: spacing-scale.md --> |
| `space-2` | 0.5rem (8px) | Standard internal padding |
| `space-4` | 1rem (16px) | Section padding |
| `space-6` | 1.5rem (24px) | Page-level padding, section gaps |
| `space-8` | 2rem (32px) | Large section separation |

**ui-specification.md — Component Library** *(Rule 5: components DDRs)*

| UI Need | Component | Notes |
|---------|-----------|-------|
| Buttons | Primary/secondary/ghost/destructive × sm/md/lg | focus-visible ring, rounded-md <!-- from: buttons.md --> |
| Cards | Flat (border-only) and elevated variants | p-4 body, px-4 py-3 header/footer <!-- from: cards.md --> |
| Tables | Sticky header, sort indicators, hover rows | overflow-x-auto mobile wrap <!-- from: tables.md --> |
| Forms | Label above, inline validation, fieldset groups | Required asterisk, border-error on invalid <!-- from: forms.md --> |
| Modals | sm/md/lg centered with scrim backdrop | Focus trap, Escape to close <!-- from: modals.md --> |
| Navigation | Fixed sidebar + top bar | Collapsible icon-only, mobile drawer <!-- from: navigation.md --> |
| Badges/Chips | Status badges (semantic colors) + filter chips | rounded-full, removable chips <!-- from: badges-chips.md --> |

**ui-specification.md — State Patterns** *(Rule 6: states DDRs)*

| State | Pattern | Key Constraints | Example Reference |
|-------|---------|----------------|-------------------|
| Loading (skeleton) | Skeleton placeholders with animate-pulse | Match real content dimensions; min 200ms | See Component Examples Appendix <!-- from: skeleton-loading.md --> |
| Loading (action) | Inline spinner (border-2 animate-spin) | For button/form actions only, not page loads | See Component Examples Appendix <!-- from: spinner.md --> |
| Empty | Centered heading + description + CTA | Always include heading, description, and CTA | See Component Examples Appendix <!-- from: empty-state.md --> |
| Error (inline) | Banner with bg-error/10, retry button | Human-readable message, role="alert" | See Component Examples Appendix <!-- from: error-state.md --> |
| Error (full-page) | Centered icon + heading + actions | For 404/500 when no content can render | See Component Examples Appendix <!-- from: error-state.md --> |

**ui-specification.md — Shared Layouts constraints** *(Rule 7: layout DDRs)*

Constraint annotations for the Shared Layouts section:
- **Sidebar:** Fixed `w-64` (256px) expanded, `w-16` collapsed. `transition-all duration-200`. Mobile drawer with `bg-black/[0.32]` backdrop. <!-- from: sidebar-navigation.md -->
- **Content width:** `max-w-7xl mx-auto px-4 sm:px-6 lg:px-8` for dashboard layouts. <!-- from: content-width.md -->
- **Page padding:** `py-6` page-level padding, `gap-6` between sections. <!-- from: page-padding.md -->

**ui-specification.md — Responsive Breakpoints** *(Rule 8: responsive DDRs)*

| Breakpoint | Width | Tailwind Prefix | Primary Use |
|------------|-------|-----------------|-------------|
| Mobile | < 640px | (base) | Single column, stacked layout <!-- from: breakpoints.md --> |
| Tablet | 640px - 1023px | sm:, md: | Collapsed sidebar, 2-column <!-- from: breakpoints.md --> |
| Desktop | 1024px+ | lg:, xl: | Full sidebar, multi-column <!-- from: breakpoints.md --> |

**Responsive Strategy:** Desktop-first <!-- from: desktop-first.md, corporate-clean profile -->

**CLAUDE.md — Design Patterns to Follow** *(Rule 3: positive-phrased constraints)*

- **Semantic color tokens:** MUST reference color tokens in component markup, never raw hex values (from: color-palette.md)
- **Rem-based typography:** MUST use rem units for all font sizes (from: typography-scale.md)
- **Tailwind spacing:** MUST use Tailwind spacing classes (p-2, gap-4), never arbitrary pixel values (from: spacing-scale.md)
- **Button focus rings:** MUST include focus-visible:ring-2 focus-visible:ring-offset-2 on all button variants (from: buttons.md)
- **Required field indicators:** MUST show asterisk (*) on required field labels (from: forms.md)
- **Skeleton loading:** MUST show skeleton placeholders while loading, never blank screens (from: skeleton-loading.md)
- **Error retry:** MUST include retry button for network errors (from: error-state.md)
- **WCAG contrast:** MUST achieve 4.5:1 contrast ratio for body text (from: wcag-contrast.md)
- **Focus-visible:** MUST use focus-visible (not focus) for keyboard focus indicators (from: focus-visible.md)
- **Sticky table headers:** MUST use sticky top-0 on thead for data tables (from: tables.md)

**CLAUDE.md — Design Anti-Patterns to Avoid** *(Rule 3: negative-phrased constraints)*

- **No raw hex values:** NEVER use raw hex in component markup — always reference tokens (from: color-palette.md)
- **No arbitrary spacing:** NEVER use arbitrary pixel values for spacing (from: spacing-scale.md)
- **No custom shadows:** NEVER use custom box-shadow values — use Tailwind shadow classes (from: shadows.md)
- **No nested cards:** NEVER nest cards inside cards (from: cards.md)
- **No placeholder-only labels:** NEVER use placeholder text as the only label (from: forms.md)
- **No blank loading screens:** NEVER show a blank screen while loading (from: skeleton-loading.md)
- **No raw errors to users:** NEVER show raw stack traces or API error responses (from: error-state.md)
- **No multiple primary buttons:** NEVER use more than one primary button per view section (from: buttons.md)

**Component Examples Appendix** *(Rule 9: all Examples sections collected)*

#### From: buttons.md (components)
```html
<button class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium
               bg-primary text-on-primary rounded-md hover:bg-primary/90
               focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-primary
               transition-colors duration-150">
  Save
</button>
```

#### From: cards.md (components)
```html
<div class="rounded-md border border-neutral-200 bg-white">
  <div class="px-4 py-3 border-b border-neutral-200">
    <h3 class="text-sm font-semibold text-neutral-900">Card Title</h3>
  </div>
  <div class="p-4">
    <p class="text-sm text-neutral-700">Card body content.</p>
  </div>
</div>
```

#### From: skeleton-loading.md (states)
```html
<div class="rounded-md border border-neutral-200 p-4 space-y-3">
  <div class="h-5 w-3/4 rounded bg-neutral-200 animate-pulse"></div>
  <div class="h-4 w-full rounded bg-neutral-200 animate-pulse"></div>
  <div class="h-4 w-5/6 rounded bg-neutral-200 animate-pulse"></div>
</div>
```

#### From: empty-state.md (states)
```html
<div class="flex flex-col items-center justify-center py-16 px-4 text-center">
  <h3 class="text-lg font-semibold text-neutral-900">No projects yet</h3>
  <p class="mt-1 text-sm text-neutral-500 max-w-sm">Create your first project to get started.</p>
  <button class="mt-6 inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm
                 font-medium text-white hover:bg-primary/90 transition-colors duration-150">
    Create Project
  </button>
</div>
```

*(... additional examples from tables, forms, modals, navigation, badges-chips, spinner, error-state DDRs)*

---

## Post-Compilation Checklist

After the AI generates compiled template sections:

- [ ] Every DDR has a row in ui-specification.md Key UI Decisions (Rule 1)
- [ ] design-tokens DDRs populate the Design System tables — colors, typography, spacing (Rule 4)
- [ ] components DDRs populate Component Library table (Rule 5)
- [ ] states DDRs populate State Patterns table with 4 columns (Rule 6)
- [ ] layout DDRs annotate Shared Layouts section with constraint comments (Rule 7)
- [ ] responsive DDRs populate Responsive Breakpoints table (Rule 8)
- [ ] All Examples sections collected into Component Examples Appendix (Rule 9)
- [ ] Every DDR's Constraints appear as Design Patterns or Anti-Patterns in CLAUDE.md (Rule 3)
- [ ] Profile override values are used instead of DDR defaults (if profile provided)
- [ ] No duplicate rows in merged tables
- [ ] DDR sources are traceable via comments (e.g., `<!-- from: color-palette.md -->`)
- [ ] TODO scaffolds exist for all project-specific sections (screens, components, etc.)
- [ ] Output heading structure matches `.ai-framework/templates/`
- [ ] Paste compiled sections into your project docs and fill in the TODO scaffolds
