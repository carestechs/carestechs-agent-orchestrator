# Release Lifecycle Guide

> **Purpose**: This guide documents two development models (versioned releases and continuous development), explains how features flow through the stakeholder definition in each model, and provides a step-by-step process for transitioning between them.

---

## 1. Development Models

Projects can operate under either of two development models. Choose the one that fits your current phase.

### 1.1 Versioned Releases

**What it is:** Fixed scope per version. Features are batched into a named release (v1, v2, etc.) with explicit "In Scope" and "Out of Scope" boundaries.

**When to use:**
- Initial launch — when you need a clear finish line to ship against
- Coordinated large releases with multiple stakeholders
- When scope discipline is critical (e.g., first version must ship by a deadline)

**Stakeholder definition structure:**
```
## Scope Lock (v1 Contract)
### In Scope (v1)
- Feature A
- Feature B
### Explicitly Out of Scope
- Feature C (v2 consideration)
- Feature D
```

**Characteristic:** "Any additions move to the next version."

### 1.2 Continuous Development

**What it is:** No version boundaries. Features flow independently through a lifecycle: considered, worked on, released. The stakeholder definition is a living document that reflects the current state of the product.

**When to use:**
- Post-launch iteration driven by real usage and feedback
- Small team with short feedback loops
- When batching features into versions adds overhead without value

**Stakeholder definition structure:**
```
## Development Model
Continuous — features flow independently, no version batching.

## Release History
### v1 — Initial Release (delivered YYYY-MM-DD)
- Feature A
- Feature B

## Current Work
- Feature E (in progress)

## Under Consideration
- Feature F (evaluating scope and priority)

## Not Planned
- Feature G (no current intent to build)
```

**Characteristic:** Features move through lifecycle stages independently.

---

## 2. Feature Lifecycle (Continuous Model)

In the continuous model, every feature follows this lifecycle through the stakeholder definition:

```
Not Planned → Under Consideration → Current Work → Released (in Release History)
```

### Lifecycle Transitions

| From | To | Trigger | Docs to Update | Who Decides |
|------|----|---------|----------------|-------------|
| Not Planned | Under Consideration | Team discussion, user feedback, or strategic shift | Stakeholder definition (move item between sections) | Product owner / team lead |
| Under Consideration | Current Work | Decision to build — scope and priority confirmed | Stakeholder definition + spec docs (data model, API, UI as needed) | Product owner / team lead |
| Current Work | Released | Feature shipped to production | Stakeholder definition (move to Release History with date) | Developer / team confirms deployment |
| (new idea) | Under Consideration | New feature idea surfaces | Stakeholder definition (add to Under Consideration) | Anyone on team |
| Under Consideration | Not Planned | Decision not to build (for now) | Stakeholder definition (move to Not Planned) | Product owner / team lead |

### Release History Entries

When a feature (or group of features) ships, record it in Release History:

```markdown
## Release History

### v1 — Initial Release (delivered 2026-02-15)
- Tasks: Full CRUD with metadata
- Projects: Group tasks with project-level views
- Kanban, List, and Gantt views
- Google Workspace SSO
- Real-time updates via SignalR

### My Tasks view (released 2026-03-10)
- Cross-project personal task dashboard
```

Post-v1 entries don't need version numbers — use the feature name and date.

---

## 3. Transitioning from Versioned to Continuous

Use this checklist when a version ships and the team decides to switch to continuous development.

### Transition Checklist

1. **Record the version as delivered in Release History**
   - Add a "Release History" section to the stakeholder definition
   - List the version name, delivery date, and summary of what shipped
   - Preserve the original scope items as the release record

2. **Restructure the stakeholder definition**
   - Replace "Scope Lock (vX Contract)" / "In Scope (vX)" / "Explicitly Out of Scope" with:
     - **Release History** — what has shipped (with dates)
     - **Current Work** — features actively being built
     - **Under Consideration** — features being evaluated
     - **Not Planned** — features with no current intent to build
   - Add a "Development Model" section stating the project uses continuous development

3. **Classify former "Out of Scope" items**
   - For each item in "Explicitly Out of Scope":
     - If it was tagged "v2 consideration" or similar → move to **Under Consideration**
     - If it was a hard exclusion with no reconsider note → move to **Not Planned**
   - Remove all "v2" / "next version" language

4. **Update spec document headers**
   - Drop "(vX)" from document titles
   - Change status lines from "Active — authoritative for vX development" to "Active — living document, continuously updated"
   - Update Change Policy sections to remove version-gated language

5. **Update AI Task Generation Notes**
   - Change references from "Scope Lock" to the new section names (Current Work, Not Planned, Under Consideration)
   - Ensure the notes reference the continuous model structure

---

## 4. Stakeholder Definition Structure per Model

### Versioned Model (default for initial launch)

```markdown
## Scope Lock (v1 Contract)

### In Scope (v1)
- Feature A
- Feature B

### Explicitly Out of Scope
- Feature C (v2 consideration)
- Feature D

Any additions move to v2.
```

### Continuous Model (post-launch iteration)

```markdown
## Development Model

Continuous development — features flow independently through the lifecycle
(Not Planned → Under Consideration → Current Work → Released).
See `.ai-framework/guides/release-lifecycle.md` for details.

## Release History

### v1 — [Release Name] (delivered YYYY-MM-DD)
- Feature A
- Feature B

## Current Work
- Feature E (brief description of current state)

## Under Consideration
- Feature F (why it's being considered)

## Not Planned
- Feature G (reason it's excluded or deferred)
```

---

## 5. Spec Document Headers per Model

### Versioned Model

```markdown
# API Specification (v1)

> **Version**: v1 — Derived from the v1 scope lock in `stakeholder-definition.md`.
> **Date**: 2025-06-01
> **Status**: Active — this is the authoritative API specification for v1 development.
```

### Continuous Model

```markdown
# API Specification

> **Continuously updated** — derived from the stakeholder definition.
> **Status**: Active — living document, continuously updated.
```

The same pattern applies to Data Model and UI Specification documents.
