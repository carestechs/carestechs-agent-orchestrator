# Documentation Maintenance Guide (v2)

> **Purpose**: This guide explains when and how to update the 10 core documentation types (7 system templates + 3 work item templates) to keep them synchronized with the codebase. Outdated documentation leads to incorrect AI task generation and wasted effort.

---

## Core Principle: Documentation as Code

Treat documentation with the same rigor as code:
- Review docs in PRs that change related code
- Include doc updates in definition of done
- Automate sync verification where possible

---

## Document Lifecycle Matrix

| Document | Created | Updated | Reviewed | Retired |
|----------|---------|---------|----------|---------|
| Persona | Product discovery | Major pivot | Quarterly | Segment deprecated |
| Stakeholder Definition | Project start | Feature shipped or strategy change | Per feature ship | Project end |
| Architecture | System design | Structural changes | Quarterly | System retired |
| CLAUDE.md | Project start | Convention changes | Quarterly | - |
| Data Model | Project start | Entity/field changes | Per release | Major version rewrite |
| API Specification | Project start | Endpoint/DTO changes | Per release | Major version rewrite |
| UI Specification | Project start | Screen/component changes | Per release | Major version rewrite |
| HTML Mockups | Pre-implementation | Design/layout changes | Per release | Screen implemented |
| Component Examples | DDR compilation | Design token changes, component DDR updates | Per release | Project design system stabilized |
| Feature Brief | Before feature task generation | Scope/AC changes during implementation | When tasks are generated | Feature completed or cancelled |
| Bug Report | When bug is discovered | Investigation reveals new info | When fix is verified | Bug resolved or closed |
| Improvement Proposal | Before refactoring task generation | Risk/scope changes during implementation | When improvement is complete | Improvement completed or deferred |

---

## Update Triggers by Document Type

### 1. Persona Documents

**Update When:**
- User research reveals new insights
- Customer feedback contradicts assumptions
- Market conditions change
- New customer segment identified
- Usage data shows different behavior than expected

**Update Process:**
1. Document the trigger (what changed)
2. Review current persona with stakeholders
3. Update affected sections
4. Propagate changes to dependent docs (stakeholder definition)

**Review Checklist:**
- [ ] Pain points still accurate?
- [ ] Behavior patterns match reality?
- [ ] Technology relationship current?
- [ ] Strategic fit still valid?

---

### 2. Stakeholder Definition

**Update When:**
- Feature shipped (move from Current Work → Release History)
- Development model changed (versioned → continuous) — see `guides/release-lifecycle.md`
- Product strategy shifts
- Success metrics need adjustment
- Current Work, Under Consideration, or Not Planned items change
- New guiding principles adopted

**Update Process:**
1. Identify which section changed (Release History, Current Work, Under Consideration, Not Planned)
2. Move items between sections as appropriate
3. Review current metrics against actuals
4. Communicate changes to team

**Review Checklist:**
- [ ] Success metrics still measurable and relevant?
- [ ] Current Work reflects what's actually being built?
- [ ] Release History is up to date with shipped features?
- [ ] Guiding principles still apply?
- [ ] Philosophy aligned with execution?

---

### 3. Architecture Document

**Update When:**
- New component added
- Component removed or deprecated
- Integration point added/removed
- Technology stack changes
- Scaling approach changes
- Security model changes

**Update Process:**
1. Update diagrams first
2. Update component descriptions
3. Update data flow documentation
4. Review integration points
5. Verify consistency with code

**Review Checklist:**
- [ ] Diagrams match deployed system?
- [ ] All components documented?
- [ ] Integration points current?
- [ ] Security section accurate?
- [ ] Technology stack list complete?

---

### 4. CLAUDE.md (Code Conventions)

**Update When:**
- New pattern adopted
- Pattern deprecated
- File structure changes
- New tool/library adopted
- Naming conventions change
- Common gotchas discovered

**Update Process:**
1. Propose change in team discussion
2. Update CLAUDE.md
3. Add examples of new pattern
4. Communicate to team
5. Update related tooling (linters, etc.)

**Review Checklist:**
- [ ] Patterns match actual code?
- [ ] Examples are current?
- [ ] No outdated conventions?
- [ ] Common gotchas documented?
- [ ] Security checklist current?

---

### 5. Data Model (`docs/data-model.md`)

**Update When:**
- New entity added to a module
- Field added, removed, or type-changed on an existing entity
- Index added, removed, or modified
- Enum value added or renamed
- Relationship or cascade behavior changed
- Business rule added or corrected based on implementation learnings
- Constraint added for performance reasons discovered during development

**Update Process:**
1. Edit `data-model.md` first — add/modify the entity, field, index, or relationship
2. Implement the change in code (EF Core entity, migration, service logic)
3. Include both the doc update and code change in the same PR
4. Verify the ER diagram in Section 7 still reflects the change (update if needed)

**Review Checklist:**
- [ ] All entities match EF Core entity classes?
- [ ] Field types and constraints match migration code?
- [ ] Indexes listed match actual migration indexes?
- [ ] Enum values match C# enum definitions?
- [ ] Cross-module references are ID-only (no navigation properties)?
- [ ] ER diagram reflects current entity structure?
- [ ] Business rules are accurate and complete?

---

### 6. API Specification (`docs/api-spec.md`)

**Update When:**
- New endpoint added to any module
- Endpoint route, method, or auth requirement changed
- Request or response DTO shape changed (field added, renamed, type changed)
- New query parameter or filter added to a list endpoint
- Status code or error condition added or corrected
- Shared DTO (e.g., `UserSummaryDto`, `PaginationMeta`) modified

**Update Process:**
1. Edit `api-spec.md` first — add/modify the endpoint, DTO, or status code
2. Implement the change in code (controller, service, DTO class)
3. Include both the doc update and code change in the same PR
4. Verify the Endpoint Summary table in Section 5 is updated

**Review Checklist:**
- [ ] All endpoints match controller actions?
- [ ] Request/response JSON shapes match DTO classes?
- [ ] Status codes match actual controller responses?
- [ ] Auth requirements match `[Authorize]` attributes?
- [ ] Pagination parameters match list endpoint implementations?
- [ ] Endpoint Summary table is complete and accurate?
- [ ] Shared DTOs match actual C# DTO classes?

---

### 7. UI Specification (`docs/ui-specification.md`)

**Update When:**
- New screen or page added to the application
- Screen layout or component hierarchy changed
- Design token changed (color, typography, spacing)
- New shared component added or existing one modified
- Interaction pattern added or changed (new user action, new drag-drop behavior)
- Component → API mapping changed (component now calls a different endpoint)
- Screen state handling changed (new loading skeleton, different empty state)

**Update Process:**
1. Edit `ui-specification.md` first — add/modify the screen spec, component, or design token
2. Implement the change in code (Angular component, template, styles)
3. Include both the doc update and code change in the same PR
4. Verify the Screen Inventory table in Section 3 is updated if a new screen was added

**Review Checklist:**
- [ ] All screens in the inventory have matching specifications in Section 5?
- [ ] Component hierarchies match actual Angular component trees?
- [ ] Component → API mappings match actual service calls in components?
- [ ] All 4 states (default, loading, empty, error) are defined for every screen?
- [ ] Shared components in Section 6 match actual reusable components?
- [ ] Design tokens match actual Tailwind config / Angular Material theme?
- [ ] User interactions match actual event bindings in templates?
- [ ] Routes match actual Angular router configuration?

---

### 8. HTML Mockups

**Update When:**
- Design tokens change (colors, typography, spacing in UI Specification)
- Screen layout changes (component hierarchy or ASCII sketch modified)
- Stakeholder feedback requires visual revisions
- Angular Material theme reconfigured

**Update Process:**
1. Identify which mockups are affected by the change (check `mockups/` directory)
2. Regenerate affected mockups using the [`mockup-generation.md`](../prompts/mockup-generation.md) prompt with updated context
3. Open regenerated files in a browser and verify visual accuracy
4. Include updated mockup files in the same PR as the code/doc change

**Review Checklist:**
- [ ] Colors match current design tokens from UI Specification?
- [ ] Layout matches current ASCII sketch and component hierarchy?
- [ ] All states are accurate and correctly labeled?
- [ ] File naming follows `mockups/{task-id}-{screen-name}.html` convention?

---

### 9. Component Examples (from DDR Compilation)

**Update When:**
- Design tokens change in the UI Specification (colors, typography, spacing)
- A component DDR is updated in the shared DDR repo (new variant, changed constraint)
- A state pattern DDR is updated (new loading, empty, or error pattern)
- The project switches profiles or adds/removes DDRs from the compiled set

**Update Process:**
1. Re-run DDR compilation using `compile-ddrs.md` with the updated DDR files or profile
2. Replace the Component Examples Appendix with the newly compiled version
3. Update any CLAUDE.md Design Patterns that changed due to updated DDR constraints
4. Regenerate affected HTML mockups using the updated component examples as context

**Review Checklist:**
- [ ] Component examples match current design token values (colors, fonts, spacing)?
- [ ] Button, card, form, and state examples use the correct Tailwind classes?
- [ ] Examples are consistent with the UI Specification Design System section?
- [ ] Any new component DDRs have been compiled and their examples added?
- [ ] CLAUDE.md Design Patterns/Anti-Patterns are in sync with DDR constraints?

---

### 10. Work Items (`docs/work-items/`)

**Update When:**
- Investigation reveals new information about a bug (update Bug Report)
- Feature scope changes during implementation (update Feature Brief)
- Risk assessment changes during refactoring (update Improvement Proposal)
- Tasks are generated, in progress, or completed (update Status field)

**Update Process:**
1. Update the Status field in Section 1 to reflect current state
2. If scope/details changed, update the relevant sections and note the change
3. When completed, update Status to final state (Completed, Resolved, etc.)

**Status Lifecycle:**

| Work Item Type | Status Progression |
|---------------|-------------------|
| Feature Brief | Not Started → In Progress → Tasks Generated → Completed · Cancelled |
| Bug Report | Reported → Investigating → Fix In Progress → Resolved · Won't Fix |
| Improvement Proposal | Proposed → Approved → In Progress → Completed · Deferred · Rejected |

**Review Checklist:**
- [ ] Status field reflects actual state of work?
- [ ] Acceptance criteria still accurate (not changed during implementation)?
- [ ] Traceability links are correct?
- [ ] Completed work items marked as such?

---

## Sync Verification Checklist

Use this checklist periodically to verify documentation is in sync with code.

### Weekly Verification

- [ ] CLAUDE.md patterns match recent PRs
- [ ] Architecture reflects any new services or endpoints
- [ ] Data Model reflects any entity/field changes in recent PRs
- [ ] API Specification reflects any endpoint/DTO changes in recent PRs
- [ ] UI Specification reflects any screen/component changes in recent PRs
- [ ] HTML Mockups reflect current design tokens and layout specs
- [ ] Work item statuses reflect actual state of work

### Sprint/Release Verification

- [ ] Architecture diagram reflects deployments
- [ ] Stakeholder definition reflects current state (Release History, Current Work, Not Planned)
- [ ] CLAUDE.md conventions match team practices
- [ ] Data Model entities match EF Core entity classes and migrations
- [ ] API Specification endpoints match controller actions
- [ ] UI Specification screens match Angular routes and components

### Quarterly Verification

- [ ] Full documentation audit across all 10 document types
- [ ] Archive completed/resolved work items (move to `docs/work-items/archive/` or mark clearly)
- [ ] Stakeholder definition alignment with product direction
- [ ] Architecture review against deployed system
- [ ] Persona assumptions validated against user data

---

## Documentation Review in PRs

### When to Require Doc Updates

Add doc updates to PR when:

| Code Change | Document to Update |
|-------------|-------------------|
| New service/component | Architecture |
| New pattern introduced | CLAUDE.md |
| Changed file structure | CLAUDE.md |
| Added external integration | Architecture |
| Changed environment vars | CLAUDE.md |
| Updated dependencies | CLAUDE.md (if affects patterns) |
| Shipped major feature | Stakeholder (update scope) |
| User feedback received | Persona (if contradicts assumptions) |
| Added/changed entity or field | Data Model |
| Added/changed index or constraint | Data Model |
| Added/changed endpoint | API Specification |
| Changed DTO shape | API Specification |
| Added/changed screen or page | UI Specification |
| Changed component hierarchy | UI Specification |
| Changed design tokens | UI Specification |
| Added/changed shared component | UI Specification |
| Changed design tokens          | HTML Mockups (affected screens) |
| Changed screen layout          | HTML Mockups (affected screens) |

### PR Checklist for Reviewers

```markdown
## Documentation Review

- [ ] Architecture updated if components changed
- [ ] CLAUDE.md updated if new patterns introduced
- [ ] Stakeholder definition reflects current state
- [ ] UI Specification updated if screens or components changed
- [ ] Changelog entry added to each updated spec document
```

---

## Changelog Entries

Every update to a living spec document must include a changelog entry at the bottom of that document.

**Format:**

| Date | Change | Context |
|------|--------|---------|
| YYYY-MM-DD | Brief description of what changed | PR link, feature name, or reason |

**Rules:**
- One row per logical change (group related field additions into one entry, don't list each field separately)
- Date is the date the change is made, not the date it ships
- Context should help someone understand *why* this changed — link a PR, name the feature, or describe the trigger
- Changelog entries are append-only — never edit or remove previous entries
- When multiple docs are updated in the same PR, each doc gets its own changelog entry

**Documents with changelogs:** `data-model.md`, `api-spec.md`, `ARCHITECTURE.md`, `ui-specification.md`

---

## Handling Documentation Debt

### Identifying Doc Debt

Signs of documentation debt:
- AI generates tasks that don't match codebase
- New team members confused by docs
- Docs reference removed features/code
- Multiple "accurate" versions of truth
- Tribal knowledge not written down

### Paying Down Doc Debt

1. **Triage**: List all known inaccuracies
2. **Prioritize**: Focus on CLAUDE.md and Architecture first (most impact on AI task generation)
3. **Schedule**: Allocate time each sprint for doc maintenance
4. **Culture**: Make doc updates part of definition of done

### Doc Debt Sprint Template

```markdown
## Documentation Debt Sprint

### High Priority (AI Task Generation Impact)
- [ ] Update CLAUDE.md with current patterns
- [ ] Verify architecture matches deployed system
- [ ] Verify data model matches EF Core entities and migrations
- [ ] Verify API spec matches controller actions and DTOs
- [ ] Verify UI spec matches Angular routes, components, and design tokens

### Medium Priority (Team Productivity)
- [ ] Update stakeholder definition (Current Work, Release History)
- [ ] Archive completed features to Release History

### Low Priority (Long-term Health)
- [ ] Quarterly persona review
- [ ] Stakeholder definition alignment
```

---

## Quick Reference: "When Do I Update X?"

```
Code Change                    → Update These Docs
─────────────────────────────────────────────────────────────
Added new service/component    → Architecture
Changed component structure    → Architecture
Added external integration     → Architecture
Introduced new code pattern    → CLAUDE.md
Changed file structure         → CLAUDE.md
Changed environment vars       → CLAUDE.md
Updated dependencies           → CLAUDE.md (if affects patterns)
Product strategy shifted       → Stakeholder Definition
Feature lifecycle state changed → Stakeholder Definition
User feedback contradicts docs → Persona
New user research insights     → Persona
Added/changed entity or field  → Data Model
Added/changed index/constraint → Data Model
Added/changed API endpoint     → API Specification
Changed DTO shape or status    → API Specification
Added/changed screen or page   → UI Specification
Changed component hierarchy    → UI Specification
Changed design tokens          → UI Specification
Added/changed shared component → UI Specification
Changed design tokens          → HTML Mockups (affected screens)
Changed screen layout          → HTML Mockups (affected screens)
Updated DDR in shared repo     → Re-run DDR compilation, update
                                 Component Examples, CLAUDE.md
                                 Design Patterns, UI Spec Design System
Feature tasks generated        → Feature Brief (Status → Tasks Generated)
Bug fix verified               → Bug Report (Status → Resolved)
Improvement completed          → Improvement Proposal (Status → Completed)
```
