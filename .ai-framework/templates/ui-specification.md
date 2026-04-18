# UI Specification Template

> **Purpose**: Document the screen layouts, component hierarchy, design tokens, interaction patterns, and state management for every user-facing screen. This sits between the API Specification (what data is available) and Feature Tasks (what to implement), giving AI the structural understanding needed to generate frontend tasks with consistent detail — including loading states, empty states, responsive behavior, and accessibility.

---

## 1. Overview

### 1.1 UI Summary

[One paragraph describing the application's user interface at a high level — what type of app it is (SPA, PWA, etc.), how many major screens exist, what the primary interaction paradigm is (form-heavy, drag-and-drop, conversational, etc.), and any key UI decisions.]

### 1.2 Key UI Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| [Design system / component library] | [e.g., Angular Material] | [Why — consistency, accessibility, speed] |
| [Layout pattern] | [e.g., Sidebar + main content] | [Why — navigation density, user workflows] |
| [Responsive strategy] | [e.g., Desktop-first, collapse sidebar on tablet] | [Why — primary device is desktop] |
| [Accessibility standard] | [e.g., WCAG 2.1 AA] | [Why — compliance, inclusive design] |
| [State management] | [e.g., Angular signals + services] | [Why — simplicity, reactivity] |
| [Styling approach] | [e.g., Tailwind CSS utility classes, no component CSS] | [Why — consistency, rapid iteration] |
| [Icon set] | [e.g., Material Icons] | [Why — matches component library] |

---

## 2. Design System

### 2.1 Brand Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `primary` | [#XXXXXX] | [Primary actions, active states, links] |
| `primary-light` | [#XXXXXX] | [Hover states, backgrounds] |
| `primary-dark` | [#XXXXXX] | [Active/pressed states] |
| `secondary` | [#XXXXXX] | [Secondary actions, accents] |
| `neutral-50` | [#XXXXXX] | [Backgrounds] |
| `neutral-100` | [#XXXXXX] | [Card backgrounds, borders] |
| `neutral-200` | [#XXXXXX] | [Dividers, subtle borders] |
| `neutral-700` | [#XXXXXX] | [Secondary text] |
| `neutral-900` | [#XXXXXX] | [Primary text] |
| `success` | [#XXXXXX] | [Success states, confirmations] |
| `warning` | [#XXXXXX] | [Warning states, alerts] |
| `error` | [#XXXXXX] | [Error states, destructive actions] |
| `info` | [#XXXXXX] | [Informational messages] |

### 2.2 Typography Scale

| Level | Size | Weight | Font Family | Usage |
|-------|------|--------|-------------|-------|
| `h1` | [2rem / 32px] | [700] | [Font name] | [Page titles] |
| `h2` | [1.5rem / 24px] | [600] | [Font name] | [Section headings] |
| `h3` | [1.25rem / 20px] | [600] | [Font name] | [Card titles, subsections] |
| `body` | [1rem / 16px] | [400] | [Font name] | [Body text, descriptions] |
| `body-sm` | [0.875rem / 14px] | [400] | [Font name] | [Secondary text, labels] |
| `caption` | [0.75rem / 12px] | [400] | [Font name] | [Timestamps, metadata] |

### 2.3 Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | [0.25rem / 4px] | [Tight internal padding] |
| `space-2` | [0.5rem / 8px] | [Standard internal padding] |
| `space-3` | [0.75rem / 12px] | [Component gaps] |
| `space-4` | [1rem / 16px] | [Section padding, card padding] |
| `space-6` | [1.5rem / 24px] | [Section margins] |
| `space-8` | [2rem / 32px] | [Page-level padding] |

> **Base unit**: [X]rem. All spacing is a multiple of the base unit. Use Tailwind spacing classes (`p-2`, `gap-4`, `mt-6`, etc.) mapped to these values.

### 2.4 Component Library

> **Note**: If DDRs were compiled using `compile-ddrs.md`, the rows below are pre-filled from component-category DDRs. Only add project-specific customizations.

**Library**: [e.g., Angular Material]
**Version**: [e.g., 20.x]

| UI Need | Component | Customization Notes |
|---------|-----------|-------------------|
| [Buttons] | [mat-button, mat-raised-button, mat-icon-button] | [Color overrides with brand tokens] |
| [Forms] | [mat-form-field, mat-input, mat-select] | [Outline appearance by default] |
| [Tables] | [mat-table with mat-sort and mat-paginator] | [Sticky header, row hover states] |
| [Dialogs] | [mat-dialog] | [Standard width, custom close button] |
| [Menus] | [mat-menu] | [Context menus for right-click actions] |
| [Snackbar] | [mat-snackbar] | [Success/error variants] |
| [Drag & Drop] | [CDK drag-drop] | [For Kanban board columns] |
| [Tabs] | [mat-tab-group] | [For view switching] |

### 2.5 State Patterns

> *Defines the standard UI patterns for loading, empty, error, and disabled states across the application. If DDRs were compiled, these are pre-filled from states-category DDRs.*

| State | Pattern | Key Constraints | Example Reference |
|-------|---------|----------------|-------------------|
| Loading (skeleton) | [e.g., Skeleton placeholders matching content layout, animate-pulse] | [e.g., Match real content dimensions; show for at least 200ms to avoid flicker] | [See Component Examples Appendix or describe inline] |
| Loading (action) | [e.g., Inline spinner for button/form actions] | [e.g., Use for action feedback only, not page loads; include sr-only text] | [See Component Examples Appendix or describe inline] |
| Empty | [e.g., Centered heading + description + CTA button] | [e.g., Always include heading, description, and actionable CTA; never blank] | [See Component Examples Appendix or describe inline] |
| Error (inline) | [e.g., Error banner with retry button] | [e.g., Human-readable message; include retry for network errors] | [See Component Examples Appendix or describe inline] |
| Error (full-page) | [e.g., Full-page error with illustration + message + home link] | [e.g., Never show raw stack traces; provide navigation escape] | [See Component Examples Appendix or describe inline] |
| Disabled | [e.g., opacity-50 + cursor-not-allowed] | [e.g., Use opacity for disabled states, not gray colors] | [Describe inline] |

### 2.6 Responsive Breakpoints

> *Defines the breakpoint system and responsive strategy. If DDRs were compiled, these are pre-filled from responsive-category DDRs.*

| Breakpoint | Width | Tailwind Prefix | Primary Use |
|------------|-------|-----------------|-------------|
| Mobile | [< 640px] | (base) | [Single column, stacked layout, full-width cards] |
| Tablet | [640px - 1023px] | [sm:, md:] | [Collapsed sidebar, 2-column grid where appropriate] |
| Desktop | [1024px - 1279px] | [lg:] | [Full sidebar, multi-column layouts] |
| Large Desktop | [1280px+] | [xl:, 2xl:] | [Max-width content, additional whitespace] |

**Responsive Strategy**: [Mobile-first / Desktop-first] — [One sentence explaining the choice and its implications for Tailwind class authoring]

---

## 3. Screen Inventory

> *Every screen in the application listed with its route, auth requirement, parent layout, and primary user action.*

| Screen Name | Route | Auth Required | Parent Layout | Primary User Action |
|-------------|-------|---------------|---------------|-------------------|
| [Login] | [/login] | [No] | [Public layout] | [Authenticate via OAuth] |
| [Dashboard] | [/dashboard] | [Yes] | [App shell] | [Select a project] |
| [Project Board] | [/projects/:id/board] | [Yes] | [App shell] | [Move tasks between columns] |
| [Task Detail] | [/projects/:id/tasks/:taskId] | [Yes] | [App shell (overlay/panel)] | [Edit task fields] |
| [...] | [...] | [...] | [...] | [...] |

---

## 4. Shared Layouts

### 4.1 App Shell (Authenticated)

[Describe the overall layout structure for authenticated users — header, sidebar, main content area, footer (if any).]

```
┌──────────────────────────────────────────────────────────┐
│  Header: [logo, search, user avatar/menu]                │
├──────────┬───────────────────────────────────────────────┤
│ Sidebar  │  Main Content Area                            │
│          │                                               │
│ [nav]    │  [Page content rendered here via router]      │
│ [items]  │                                               │
│          │                                               │
│          │                                               │
├──────────┴───────────────────────────────────────────────┤
│  Footer (optional): [status, version]                    │
└──────────────────────────────────────────────────────────┘
```

**Sidebar behavior**:
- [Desktop: always visible, fixed width (e.g., 260px)]
- [Tablet: collapsible, toggle via hamburger icon]
- [Mobile: overlay drawer]

**Header contents**:
- [Left: app logo + project name]
- [Center: global search]
- [Right: notifications icon, user avatar with dropdown menu]

### 4.2 Public Layout (Unauthenticated)

[Describe the layout for public/login pages — typically simpler, centered content.]

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│              [Centered content card]                     │
│              [Logo + login form]                         │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Screen Specifications

> *One block per screen. Include layout sketch, component hierarchy, API mapping, states, and interactions.*

### 5.1 [Screen Name]

**Route**: [/path]
**Auth**: [Required / Public]
**Layout**: [App shell / Public]

#### Layout Sketch

```
[ASCII diagram or description of the screen layout]
```

#### Component Hierarchy

```
[ScreenName]Page
├── [ComponentA]
│   ├── [SubComponentA1]
│   └── [SubComponentA2]
├── [ComponentB]
│   └── [SubComponentB1]
└── [ComponentC]
```

#### Component → API Mapping

| Component | Data Needed | API Endpoint | Trigger |
|-----------|-------------|-------------|---------|
| [ComponentA] | [List of items] | [GET /api/items?filter=X] | [On page load] |
| [ComponentB] | [Item details] | [GET /api/items/:id] | [On item select] |
| [ComponentC] | [Create item] | [POST /api/items] | [On form submit] |

#### States

| State | Condition | UI Behavior |
|-------|-----------|-------------|
| **Default** | [Data loaded, items exist] | [Show list/grid/board with data] |
| **Loading** | [API request in flight] | [Show skeleton loaders / spinner] |
| **Empty** | [No items match filters or none exist] | [Show illustration + "No items yet" message + CTA to create first item] |
| **Error** | [API request failed] | [Show error banner with retry button] |

#### User Interactions

| Action | UI Element | Result | API Call |
|--------|-----------|--------|----------|
| [Click create button] | [FAB / toolbar button] | [Open create dialog] | [None until submit] |
| [Submit form] | [Save button in dialog] | [Close dialog, add item to list] | [POST /api/items] |
| [Click item] | [List row / card] | [Navigate to detail or open side panel] | [GET /api/items/:id] |
| [Drag item] | [Kanban card] | [Move to new column, update status] | [PATCH /api/items/:id] |

---

*(Repeat Section 5.X for each screen in the Screen Inventory)*

---

## 6. Shared Components

> *Reusable components used across multiple screens. Document their inputs, outputs, and visual variants.*

### 6.1 [Component Name]

**Used in**: [Screen A, Screen B, Screen C]
**Description**: [One sentence — what this component renders and its purpose]

#### Inputs / Outputs

| Name | Direction | Type | Description |
|------|-----------|------|-------------|
| [item] | Input | [ItemDto] | [The data to display] |
| [compact] | Input | [boolean] | [Whether to use compact layout] |
| [clicked] | Output | [EventEmitter\<string\>] | [Emitted when user clicks, payload is item ID] |

#### Visual Variants

| Variant | When Used | Visual Difference |
|---------|-----------|-------------------|
| [Default] | [Standard display in lists] | [Full size, all fields visible] |
| [Compact] | [Sidebar, dropdowns] | [Smaller, only name + avatar shown] |
| [Selected] | [Currently active item] | [Highlighted border, accent background] |

---

*(Repeat Section 6.X for each shared component)*

---

## 7. Usage Notes for AI Task Generation

### When generating frontend tasks, use this document to:

1. **Derive component structure** — Use the Component Hierarchy to know exactly which Angular components to create or modify for each screen.
2. **Map data requirements** — Use Component → API Mapping to know which service calls each component needs.
3. **Specify all states** — Every component task should include handling for loading, empty, and error states as defined in the States table.
4. **Define interactions precisely** — Use the User Interactions table to specify exact behavior, not vague descriptions.
5. **Reuse shared components** — Check Section 6 before creating new components. If a shared component exists, use it.
6. **Follow the design system** — Reference Section 2 for colors, typography, spacing, and component library usage.
7. **Respect layouts** — Reference Section 4 for app shell structure. New screens must fit within the defined layouts.

8. **Use state patterns consistently** — Reference Section 2.5 for the standard loading, empty, error, and disabled patterns. Every screen must use these patterns — do not invent new loading or error UIs.
9. **Use defined breakpoints** — Reference Section 2.6 for the responsive breakpoints and strategy. Follow the mobile-first or desktop-first approach consistently across all screens.
10. **Reference component examples** — If DDRs were compiled with a Component Examples Appendix, use those HTML/Tailwind patterns as the starting point for mockups and implementations. DDR examples take precedence over AI invention.

### Rules:

- Every frontend task must reference a specific screen from Section 5
- Every component must handle all 4 states (default, loading, empty, error) unless explicitly noted otherwise
- Use Angular Material components listed in Section 2.4 — do not build custom primitives
- Use Tailwind classes for layout and spacing — do not create component CSS
- Shared components (Section 6) must be used instead of duplicating UI across screens
