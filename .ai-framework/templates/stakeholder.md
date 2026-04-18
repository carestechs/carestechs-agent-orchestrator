# Stakeholder Definition Document Template

> **Purpose**: Define what we are building, why we are building it, and what the first version will and will not do. This document serves as the product north star for AI task generation.

---

## 1. Executive Summary

> *A concise statement of the product's purpose and success criteria.*

This document defines **what we are building, why we are building it, and what [version] will and will not do**.

The product is a **[product type/category]** designed to:
- [Core value proposition 1]
- [Core value proposition 2]
- [Core value proposition 3]
- [Core value proposition 4]

[Version] is intentionally narrow. Its goal is **[primary goal], not [explicitly excluded goal]**.

**Success means:**
- [Measurable success criterion 1]
- [Measurable success criterion 2]
- [Measurable success criterion 3]
- [Measurable success criterion 4]

---

## 2. Core Business Problem

> *Describe the problem space the product addresses.*

[Current solution/approach] today suffers from:
- [Pain point 1]
- [Pain point 2]
- [Pain point 3]
- [Pain point 4]

**[User type 1]** wants [desired outcome 1].
**[User type 2]** wants [desired outcome 2].

This platform solves both by [how the solution addresses both needs].

---

## 3. Product Philosophy

### 3.1 Guiding Principles

> *The non-negotiable design principles that inform all decisions.*

- **[Principle 1 name]**: [Explanation]
- **[Principle 2 name]**: [Explanation]
- **[Principle 3 name]**: [Explanation]
- **[Principle 4 name]**: [Explanation]
- **[Principle 5 name]**: [Explanation]

### 3.2 What We Intentionally Avoid in [Version]

> *Explicit scope exclusions to prevent feature creep.*

- [Excluded feature 1]
- [Excluded feature 2]
- [Excluded feature 3]
- [Excluded feature 4]
- [Excluded feature 5]

---

## 4. [Key Stakeholder Concern Area] Strategy

> *Address the most critical stakeholder concern with a dedicated section.*

### 4.1 [Version] [Concern Area] Options

[Approach description]:

- **[Option 1]** ([Primary/Secondary])
- **[Option 2]** ([Optional/Required])

### 4.2 Why This Works

- [Rationale 1]
- [Rationale 2]
- [Rationale 3]
- [Rationale 4]

This is not a limitation - it is a **deliberate [strategic choice] choice**.

---

## 5. UX Strategy Overview

> *High-level approach to user experience.*

### 5.1 [Core UX Pattern] Logic

The user [primary interaction pattern].

**Why:**
- [Reason 1]
- [Reason 2]
- [Reason 3]

Once [trigger], the system [response].

---

## 6. [Asset/Resource] Strategy

> *How key resources (visuals, content, etc.) are handled.*

To guarantee [quality goal] regardless of [variable], the platform provides [solution].

This ensures:
- [Benefit 1]
- [Benefit 2]
- [Benefit 3]

### [Asset] Categories

- [Category 1] ([examples])
- [Category 2] ([examples])
- [Category 3] ([examples])
- [Category 4] ([examples])
- [Category 5] ([examples])

[Constraint or note about customization]

---

## 7. Complete User Flow

> *Document the user journey phase by phase.*

### Phase 1 - [Phase Name]

**[Step Name]**
- [Element/component]
- [Actions available]: [Action 1] / [Action 2]

**[Step Name] (Optional)**
- [Element/component]
- [Transition]: [Action]

---

### Phase 2 - [Phase Name]

1. **[Step]**
   - [Interaction type]: [Options]

2. **[Step]**
   - [Logic explanation]
   - [Interaction type]: [Options]

3. **[Step]**
   - [Interaction type]: [Options]

---

### Phase 3 - [Phase Name]

[Continue pattern for remaining phases...]

---

## 8. Backend Responsibilities

> *Define what the backend system must guarantee.*

- [Responsibility 1]
- [Responsibility 2]
- [Responsibility 3]
- [Responsibility 4]
- [Responsibility 5]

**The frontend [role]. The backend [role].**

---

## 9. Success Metrics for [Version]

> *Quantifiable measures of success.*

- [Metric 1 with target]
- [Metric 2 with target]
- [Metric 3 with target]
- [Metric 4 with target]

---

## 10. Scope Lock ([Version] Contract)

> *Explicit scope boundaries - what's in, what's out.*

**Included:**
- [In-scope item 1]
- [In-scope item 2]
- [In-scope item 3]
- [In-scope item 4]

**Excluded:**
- [Out-of-scope item 1]
- [Out-of-scope item 2]
- [Out-of-scope item 3]
- [Out-of-scope item 4]

Any additions move to [next version].

> **Note — Continuous Development Model:**
> The structure above uses the **versioned model**, which is the default for initial launch. After delivering the first version, projects may switch to the **continuous model** — where features flow independently through a lifecycle (Not Planned → Under Consideration → Current Work → Released) instead of being batched into version-scoped releases. See `guides/release-lifecycle.md` for the transition process and alternative stakeholder definition structure.

---

## 11. Final Note to Stakeholders

This product is not trying to do everything.

It is designed to do **one thing exceptionally well**:

> [Single-sentence value proposition that captures the essence]

This document represents the **agreed foundation** for development and launch.

---

## Usage Notes for AI Task Generation

When generating tasks from this document:

1. **Respect Scope Lock**: Never generate tasks for features explicitly excluded
2. **Align with Principles**: All generated tasks should embody the guiding principles
3. **Target Metrics**: Tasks should demonstrably contribute to success metrics
4. **Backend/Frontend Split**: Respect the responsibility division when assigning work
5. **Phase Awareness**: Understand where each task fits in the user flow
