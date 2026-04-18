# Bug Fix Task Generation Prompt (v2)

> **Purpose**: Generate investigation and fix tasks for reported bugs. This prompt helps structure bug analysis and produces actionable tasks for resolution.
>
> **v2 Note**: This version uses 10 core templates (7 system templates + 3 work item templates). Bugs should be described in a **Bug Report** (`docs/work-items/BUG-*.md`) before task generation. The inline `<bug-report>` is still supported as a fallback for quick/ad-hoc usage.

---

## How to Use This Template

**AI agents (Claude Code, etc.):** Skip the XML context assembly below — you have direct file access. Instead:
1. Read the files listed in CLAUDE.md's routing table for "Bug fix"
2. Read the Bug Report from `docs/work-items/BUG-*.md` for the target bug. If no Bug Report exists, gather the bug details from the user and use the inline `<bug-report>` fallback
3. Use the **Output Format** section below (three-phase structure: Investigation, Implementation, Verification) as your deliverable structure
4. Apply the **Constraints** and **Post-Generation Checklist** to shape your output

**Chat workflows (manual copy-paste):** Copy the XML template below, paste your documentation into the `<context>` sections, fill in the `<bug-report-doc>` (or `<bug-report>` fallback), and submit to Claude.

---

## Prompt Template (Chat Workflow)

```xml
<task-generation-request>

<context>

<code-conventions>
<!-- REQUIRED: Include CLAUDE.md for understanding code structure -->
[Paste CLAUDE.md content]
</code-conventions>

<architecture>
<!-- RECOMMENDED: Include if bug spans multiple components -->
[Paste relevant architecture sections]
</architecture>

<stakeholder-definition>
<!-- OPTIONAL: Include if scope clarification is needed -->
[Paste scope lock and principles if relevant]
</stakeholder-definition>

</context>

<task-type>Bug Fix</task-type>

<bug-report-doc>
<!-- PREFERRED: Paste the full Bug Report document (from docs/work-items/BUG-XXX-name.md) -->
<!-- The Bug Report template provides structured fields for reproduction steps, evidence,
     impact assessment, affected entities, and traceability.
     See .ai-framework/templates/bug-report.md for the full template. -->
[Paste full Bug Report content]
</bug-report-doc>

<!-- FALLBACK: If no Bug Report document exists, use this inline report instead.
     Remove the <bug-report-doc> block above and uncomment this block. -->
<!--
<bug-report>
**Bug ID:** [BUG-XXX]
**Severity:** [Critical | High | Medium | Low]
**Reported Date:** [Date]
**Reported By:** [Source - user, QA, monitoring, etc.]

**Summary:**
[One-line description of the bug]

**Steps to Reproduce:**
1. [Step 1]
2. [Step 2]
3. [Step 3]
4. [Observe: unexpected behavior]

**Expected Behavior:**
[What should happen]

**Actual Behavior:**
[What actually happens]

**Environment:**
- Version: [App version]
- Platform: [Browser, OS, device]
- User context: [Relevant user state]

**Error Messages/Logs:**
```
[Any error messages, stack traces, or relevant log output]
```

**Additional Context:**
- Frequency: [Always | Sometimes | Rarely]
- First occurrence: [Date if known]
- Workaround exists: [Yes/No, describe if yes]
- Related bugs: [Links to related issues]

**Affected Entities/Data:**
- [Entity 1]: [Relevant attributes and business rules]
- [Entity 2]: [Relevant attributes]

**Known Error Patterns:**
- [Error code/pattern seen in logs]
- [Related error handling behavior]
</bug-report>
-->

<request>
Generate tasks to investigate and fix this bug.

Tasks should:
1. Start with investigation/diagnosis tasks
2. Identify root cause before proposing solutions
3. Include fix implementation tasks
4. Include regression tests to prevent recurrence
5. Consider related areas that might have the same issue
6. Include verification steps

Do not:
- Jump to solutions without investigation
- Over-engineer the fix
- Scope creep into refactoring unrelated code
</request>

<constraints>
- Fix must not break existing functionality
- Must include test that would have caught this bug
- Must update error handling if error was unclear
- [Add project-specific constraints]
</constraints>

<output-format>
## Task Output Format

Generate tasks in three phases:

### Phase 1: Investigation

```
### T-[XXX]: [Investigation Task Title]

**Type:** Investigation
**Workflow:** investigation-first
**Complexity:** [S | M | L]
**Dependencies:** None

**Investigation Goal:**
[What question this investigation answers]

**Rationale:**
[1-2 sentences: why this investigation is needed — what symptom or report triggered it]

**Investigation Steps:**
1. [Step to perform]
2. [Step to perform]
3. [Step to perform]

**Expected Findings:**
[What you expect to discover or rule out]

**Output:**
[What this task produces - hypothesis, root cause identification, etc.]
```

### Phase 2: Implementation

```
### T-[XXX]: [Fix Task Title]

**Type:** [Backend | Frontend | Database]
**Workflow:** standard
**Complexity:** [S | M | L]
**Dependencies:** [Investigation task IDs]

**Description:**
[What fix will be implemented]

**Rationale:**
[1-2 sentences: why this fix is needed — which root cause or investigation finding it addresses]

**Root Cause Addressed:**
[How this fix addresses the root cause]

**Implementation Approach:**
[High-level approach to the fix]

**Files to Modify:**
- [file/path] - [what changes]

**Acceptance Criteria:**
- [ ] Bug no longer reproducible with original steps
- [ ] [Additional criteria]

**Regression Risk:**
[What could break, how to verify it doesn't]
```

### Phase 3: Verification & Prevention

```
### T-[XXX]: [Test/Verification Task Title]

**Type:** Testing
**Workflow:** standard
**Complexity:** [S | M]
**Dependencies:** [Fix task IDs]

**Description:**
[What testing will be done]

**Rationale:**
[1-2 sentences: why this verification is needed — what regression risk or edge case it guards against]

**Test Cases:**
- [Test case 1 - the exact bug scenario]
- [Test case 2 - related scenario]
- [Test case 3 - edge case]

**Verification Steps:**
1. [How to verify fix works]
2. [How to verify nothing else broke]
```

## Workflow Classification

The **Workflow** field is pre-set by phase:

- **Phase 1 (Investigation):** always `investigation-first` — complete investigation steps and document findings before any fix tasks begin.
- **Phase 2 (Implementation):** always `standard` — investigation is complete, proceed with the fix.
- **Phase 3 (Verification):** always `standard` — implement tests and verify.

## Summary Section

After all tasks, provide:
- Most likely root cause hypothesis
- Confidence level in diagnosis
- Risk assessment of proposed fix
- Monitoring recommendations post-fix
- Related areas to audit for similar issues
</output-format>

</task-generation-request>
```

---

## Context Selection Guide (v2)

### What to Include

| Document | When to Include | Why |
|----------|-----------------|-----|
| Bug Report | Always (preferred) | Full `docs/work-items/BUG-*.md` for target bug |
| CLAUDE.md | Always | Navigate codebase, understand structure and patterns |
| Architecture | Multi-component bugs | Understand data flow and component interactions |
| Data Model | Data integrity/calculation bugs | Understand entity definitions and business rules |
| API Specification | API response/status bugs | Understand expected endpoint behavior |
| UI Specification | UI/display bugs | Understand expected screen behavior |
| Stakeholder Definition | Scope questions | Clarify expected behavior from product perspective |
| Persona | User-facing bugs | Understand user impact and priority |

### Bug Report vs Inline Report

- **Bug Report** (preferred): Use `docs/work-items/BUG-*.md`. Provides structured impact assessment, traceability, entity mapping, and severity justification. Generates more targeted investigation tasks.
- **Inline `<bug-report>`** (fallback): Use for quick/ad-hoc bug fixes when a full Bug Report hasn't been written yet. Faster but less structured.

---

## Example: Order Calculation Bug

> **Note:** This example uses the inline `<bug-report>` fallback for brevity. For more targeted investigation tasks, use a full Bug Report document via `<bug-report-doc>` as described in the Prompt Template above.

```xml
<task-generation-request>

<context>

<code-conventions>
## Project Structure
- Services: /src/services/
- Models: /src/models/
- Tests: /src/tests/ (mirrors src structure)

## Error Handling
- Custom AppError class with error codes
- All errors logged with context
- User-facing messages sanitized
</code-conventions>

<architecture>
## Order Service
- Handles order creation, modification, totals
- Connects to Menu Service for pricing
- Persists to PostgreSQL via Prisma

## Data Flow
Cart → Order Service (calculate totals) → Database
                   ↓
          Menu Service (get prices)
</architecture>

</context>

<task-type>Bug Fix</task-type>

<bug-report>
**Bug ID:** BUG-234
**Severity:** High
**Reported Date:** 2024-01-15
**Reported By:** Customer complaint

**Summary:**
Order total shows incorrect amount when delivery fee should be waived

**Steps to Reproduce:**
1. Add items totaling $55 to cart
2. Proceed to checkout
3. Select delivery option
4. Observe total amount

**Expected Behavior:**
Total should be $55 (delivery fee waived for orders over $50)

**Actual Behavior:**
Total shows $60 ($55 + $5 delivery fee)

**Environment:**
- Version: 2.3.1
- Platform: WhatsApp (all devices)

**Error Messages/Logs:**
No errors - calculation completes but with wrong value

**Additional Context:**
- Frequency: Always when order is $50.01 - $54.99
- First noticed: After v2.3.0 release
- Workaround: Add more items to get over $55

**Affected Entities/Data:**
- Order: items (OrderItem[]), subtotal (computed), deliveryFee (fixed/calculated), total (subtotal + deliveryFee)
- Business Rules: Subtotal = sum of (item.price * quantity), Delivery fee waived if subtotal > $50, Total = subtotal + deliveryFee
</bug-report>

<request>
Generate tasks to investigate and fix the delivery fee calculation bug.
Focus on finding why the $50 threshold isn't being applied correctly.
</request>

<constraints>
- Fix must not affect orders already placed
- Must add test covering this exact scenario
- Must verify threshold works at boundary values ($50.00, $50.01)
</constraints>

<output-format>
[Standard format as specified above]
</output-format>

</task-generation-request>
```

---

## Post-Generation Checklist

After Claude generates tasks, verify:

- [ ] Investigation tasks come before fix tasks
- [ ] Root cause is identified, not just symptoms treated
- [ ] Fix addresses root cause, not workaround
- [ ] Test would catch this bug if it regressed
- [ ] Boundary conditions are tested
- [ ] Related code areas are audited
- [ ] Error messages improved if they were unclear
- [ ] Monitoring/alerting considered for future detection
