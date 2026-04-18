# HTML Mockup Generation Prompt (v1)

> **Purpose**: Generate a self-contained HTML mockup file for stakeholder visual approval before Angular implementation. Mockups show all screen states side-by-side in a single file that opens in any browser with zero installation.
>
> **When to use**: After completing the UI Specification (Step 7) and before generating feature tasks (Phase 3). Use for user-facing screens, complex layouts, or multi-state screens where a visual prototype aids stakeholder review.
>
> **When to skip**: Standard CRUD layouts, screens that follow an already-approved pattern, or purely backend features.
>
> **v1 Note**: This is an optional workflow artifact — not a core document type. Mockups are static HTML prototypes for visual review, not functional implementations.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "UI mockup"
2. Read the target screen's spec block and Design System section from `docs/ui-specification.md`
3. Use the **Output Format** section below for the HTML structure
4. Apply the **Guidance**, **Constraints**, and **Post-Generation Checklist** to shape the mockup

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, fill in the `<mockup-scope>`, and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<mockup-generation-request>

<context>

<ui-specification>
<!-- REQUIRED: The target screen's specification block from ui-specification.md,
     including the ASCII layout sketch, component hierarchy, states, and interactions.
     Also include the Design System section (colors, typography, spacing) for token accuracy. -->
[Paste the target screen's spec block from docs/ui-specification.md]
[Paste the Design System section (Section 2) from docs/ui-specification.md]
</ui-specification>

<code-conventions>
<!-- REQUIRED: Design tokens, Tailwind conventions, and frontend patterns from CLAUDE.md.
     Ensures mockup colors, fonts, and spacing match the implementation target. -->
[Paste relevant sections from CLAUDE.md]
</code-conventions>

<api-spec>
<!-- RECOMMENDED: Response DTO shapes for the target screen's endpoints.
     Enables realistic placeholder content in the mockup. -->
[Paste relevant endpoint response DTOs from docs/api-spec.md]
</api-spec>

<persona>
<!-- OPTIONAL: Content tone and language for placeholder text. -->
[Paste persona details if available]
</persona>

<stakeholder-definition>
<!-- OPTIONAL: Product name, branding, and philosophy for visual consistency. -->
[Paste relevant sections from docs/stakeholder-definition.md]
</stakeholder-definition>

<component-examples>
<!-- RECOMMENDED: If DDRs were compiled, include the Component Examples Appendix.
     These provide pre-approved HTML/Tailwind patterns for buttons, cards, forms,
     loading states, empty states, and error states.
     DDR component examples take precedence over AI invention. -->
[Paste Component Examples Appendix from DDR compilation output, if available]
</component-examples>

</context>

<mockup-scope>
Screen Name: [Name from UI Specification screen inventory]
Task ID: [T-XXX — the task this mockup supports]
Route: [Angular route path, e.g., /login, /projects/:id/board]
States to Render: [List states to show, e.g., Default, Loading, Empty, Error]
Viewport: [Target viewport, e.g., Desktop (1280px), or Responsive]
</mockup-scope>

<guidance>
## Technical Conventions

1. **Single self-contained HTML file** — all states shown side-by-side in a responsive grid
2. **CDN dependencies only (zero install):**
   - Tailwind CSS Play CDN: `<script src="https://cdn.tailwindcss.com"></script>`
   - Google Fonts (Inter + Roboto): `<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Roboto:wght@400;500&display=swap" rel="stylesheet" />`
   - Material Icons: `<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet" />`
3. **Translate M3 color tokens** from the UI Specification Design System into `tailwind.config.extend.colors` inside a `<script>` block. If DDR-compiled values exist, use those exactly.
4. **DDR component examples take precedence** — if a Component Examples Appendix is provided, use those HTML/Tailwind patterns for buttons, cards, forms, states, etc. instead of inventing new patterns
5. **File naming:** `mockups/{task-id}-{screen-name}.html` (lowercase, kebab-case)
   - Example: `mockups/t011-login.html`, `mockups/t025-project-board.html`
6. **Reviewer header** at the top of the page identifying the task ID, screen name, and which states are shown
7. **Static only** — no JavaScript logic beyond the Tailwind config script. No event handlers, no state management, no fetch calls
8. **Use Tailwind utility classes** for all styling — no inline styles except `font-size` on Material Icon `<span>` elements
9. **Use realistic placeholder content** derived from API response DTO shapes when available
</guidance>

<constraints>
- Zero-install: File must open and render correctly in any modern browser by double-clicking
- CDN-only: No local dependencies, no npm, no build step
- Actual design tokens: Colors, fonts, and spacing must match the UI Specification Design System — do not invent new values
- No inline styles: Use Tailwind classes exclusively (exception: Material Icons `font-size`)
- No JavaScript logic: The file is a static visual prototype only
- Grid layout: All states rendered side-by-side in a responsive grid (stacks on small screens)
</constraints>

<output-format>
## HTML Structure

Generate a single HTML file with this structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{Task ID} {Screen Name} Mockup — {Product Name}</title>

  <!-- Tailwind CSS Play CDN -->
  <script src="https://cdn.tailwindcss.com"></script>

  <!-- Google Fonts: Inter + Roboto -->
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Roboto:wght@400;500&display=swap" rel="stylesheet" />

  <!-- Material Icons -->
  <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet" />

  <!-- Tailwind config with design tokens -->
  <script>
    tailwind.config = {
      theme: {
        extend: {
          fontFamily: {
            sans: ["Inter", "Roboto", "sans-serif"],
          },
          colors: {
            // Map M3 tokens from UI Specification Design System
            primary: "#...",
            "on-primary": "#...",
            surface: "#...",
            "on-surface": "#...",
            // ... additional tokens
          },
        },
      },
    };
  </script>

  <!-- Optional: CSS animations for loading states (spinners, shimmer) -->
  <style>
    /* Only keyframe animations — no layout styles */
  </style>
</head>

<body class="bg-surface font-sans text-on-surface min-h-screen">

  <!-- Reviewer header -->
  <header class="bg-white border-b border-outline/20 px-6 py-4">
    <h1 class="text-lg font-medium text-on-surface/70">
      {Task ID} {Screen Name} Mockup
      <span class="text-sm font-normal text-on-surface/40 ml-2">
        — {State 1}, {State 2}, ... shown side-by-side
      </span>
    </h1>
  </header>

  <!-- States grid -->
  <main class="max-w-7xl mx-auto px-6 py-12 grid grid-cols-1 lg:grid-cols-{N} gap-12">

    <!-- One <section> per state -->
    <section>
      <h2 class="text-sm font-medium uppercase tracking-wider text-on-surface/50 mb-4 text-center">
        {State Name}
      </h2>
      <div>
        <!-- Screen content for this state -->
      </div>
    </section>

    <!-- Repeat for each state -->

  </main>

</body>
</html>
```
</output-format>

</mockup-generation-request>
```

---

## Context Selection Guide

### What to Include

| Document | Priority | What to Include |
|----------|----------|-----------------|
| UI Specification | Required | Target screen's spec block + Design System section (colors, typography, spacing) |
| CLAUDE.md | Required | Design tokens, Tailwind conventions, frontend patterns |
| API Specification | Recommended | Response DTO shapes for the target screen's endpoints |
| Persona | Optional | Content tone for realistic placeholder text |
| Component Examples Appendix | Recommended | DDR-compiled HTML/Tailwind patterns for consistent components |
| Stakeholder Definition | Optional | Product name, branding, philosophy |

### What NOT to Include

- Architecture document (not relevant to visual mockups)
- Data Model (use API Spec DTOs instead — they represent what the screen actually displays)
- Full documents — extract only the sections relevant to the target screen

---

## Workflow

### Step 1: Pick a Screen

Choose a screen from the UI Specification screen inventory. Prioritize:
- User-facing screens with novel layouts (not standard CRUD)
- Screens with multiple states that stakeholders need to approve
- Screens where the ASCII layout sketch needs visual validation

### Step 2: Assemble Context

Gather the target screen's spec block, the Design System section, and relevant API response DTOs.

### Step 3: Generate Mockup

Run the prompt above. The AI will produce a single HTML file.

### Step 4: Review in Browser

Open the file by double-clicking. Verify all states render correctly.

### Step 5: Share for Approval

Share the HTML file with stakeholders for visual feedback. Iterate if needed.

---

## Example: Login Page Mockup

```xml
<mockup-generation-request>

<context>

<ui-specification>
## Design System (excerpt)
- Primary: #005cbb
- On-Primary: #ffffff
- Surface: #faf9fd
- On-Surface: #1a1b1f
- Surface-Container: #ffffff
- Outline: #74777f
- Error: #ba1a1a
- Font: Inter (headings), Roboto (body)

## 5.1 Login Screen
Route: /login
Auth: Public

### Layout Sketch
┌──────────────────────────────────────┐
│              (centered)              │
│         ┌──────────────────┐         │
│         │    [icon: logo]  │         │
│         │   "TecherPlannr"  │         │
│         │   ─────────────  │         │
│         │ [Sign in w/ Google] │       │
│         └──────────────────┘         │
└──────────────────────────────────────┘

### States
- Default: Card centered, button enabled
- Loading: Button disabled, spinner, "Signing in..."
- Error: Error text below button, button re-enabled
</ui-specification>

<code-conventions>
- Tailwind CSS for all styling
- Angular Material components
- Inter + Roboto fonts via Google Fonts
- Material Icons for iconography
</code-conventions>

</context>

<mockup-scope>
Screen Name: Login
Task ID: T-011
Route: /login
States to Render: Default, Loading, Error
Viewport: Desktop (1280px)
</mockup-scope>

</mockup-generation-request>
```

**Output:** `mockups/t011-login.html` — a single HTML file showing all 3 login states side-by-side.

---

## Post-Generation Checklist

After the AI generates a mockup file, verify:

- [ ] File opens in a browser by double-clicking (no build step required)
- [ ] All requested states are visible and correctly labeled
- [ ] Design tokens (colors, fonts, spacing) match the UI Specification Design System
- [ ] Layout matches the ASCII sketch from the UI Specification
- [ ] Placeholder content is realistic (derived from API DTOs when available)
- [ ] File is named correctly: `mockups/{task-id}-{screen-name}.html`
- [ ] Reviewer header at top identifies the task ID and states
- [ ] No JavaScript logic beyond Tailwind config
- [ ] Responsive grid: states stack vertically on small screens
