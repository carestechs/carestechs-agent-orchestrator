# Bug Report: BUG-001 — [One-line Summary]

> **Purpose**: Structured bug description before generating investigation and fix tasks.
> **Template reference**: `.ai-framework/templates/bug-report.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-001 |
| **Summary** | <!-- TODO: One-line description of the bug --> |
| **Severity** | <!-- TODO: Critical · High · Medium · Low --> |
| **Status** | Reported |
| **Reported By** | <!-- TODO: Source — user, QA, monitoring, developer --> |
| **Date Reported** | <!-- TODO: YYYY-MM-DD --> |
| **Date First Observed** | <!-- TODO: YYYY-MM-DD or "Unknown" --> |

### Severity Justification

<!-- TODO: 1-2 sentences explaining severity — who is affected, how badly, how often -->

---

## 2. Steps to Reproduce

**Preconditions:** <!-- TODO: Required state before starting -->

1. <!-- TODO: Step 1 -->
2. <!-- TODO: Step 2 -->
3. <!-- TODO: Step 3 -->
4. **Observe:** <!-- TODO: What goes wrong -->

**Reproducibility:** <!-- TODO: Always · Sometimes (~X%) · Rarely · Only once -->

---

## 3. Expected vs Actual Behavior

### Expected Behavior

<!-- TODO: What the system should do — reference a spec or AC if possible -->

### Actual Behavior

<!-- TODO: What the system actually does -->

---

## 4. Environment

| Field | Value |
|-------|-------|
| **App Version** | <!-- TODO: Version or commit hash --> |
| **Platform** | <!-- TODO: Browser + version, OS, device --> |
| **User Context** | <!-- TODO: Role, account type, specific data --> |
| **Deployment** | <!-- TODO: Production · Staging · Development · Local --> |

---

## 5. Error Evidence

### Error Messages / Logs

```
<!-- TODO: Paste error messages, stack traces, or relevant log output. Sanitize PII. -->
```

### Network / API Evidence

```
<!-- TODO: Paste relevant request/response data if applicable -->
```

### Screenshots / Recordings

<!-- TODO: Link to or describe visual evidence -->

---

## 6. Additional Context

| Field | Value |
|-------|-------|
| **Frequency** | <!-- TODO: Always · Sometimes · Rarely · Once --> |
| **First occurrence** | <!-- TODO: Date, version, or correlating event --> |
| **Workaround exists** | <!-- TODO: Yes (describe) / No --> |
| **Related bugs** | <!-- TODO: BUG-XXX IDs or external issue links --> |
| **Regression** | <!-- TODO: Yes (worked before) · No (never worked) · Unknown --> |

### Observations

<!-- TODO: Patterns, hypotheses, anything else relevant -->
-

---

## 7. Affected Entities and Components

<!-- TODO: Which parts of the system are involved? Reference data-model, api-spec, architecture -->

| Entity / Component | How Affected | Reference |
|--------------------|-------------|-----------|
| <!-- TODO --> | | |

---

## 8. Impact Assessment

| Dimension | Assessment |
|-----------|------------|
| **Users affected** | <!-- TODO: All users · Subset (describe) · Single user --> |
| **Feature affected** | <!-- TODO: Which feature(s) — reference FEAT-XXX --> |
| **Data impact** | <!-- TODO: None · Incorrect data · Data loss risk · Corruption --> |
| **Business impact** | <!-- TODO: Revenue · User trust · Compliance · Operational · None --> |

---

## 9. Traceability

| Reference | Link |
|-----------|------|
| **Related Feature** | <!-- TODO: FEAT-XXX --> |
| **Violated AC** | <!-- TODO: Which acceptance criterion the bug contradicts --> |
| **Spec Reference** | <!-- TODO: Which spec defines the correct behavior --> |
| **Related Work Items** | <!-- TODO: FEAT/BUG/IMP IDs --> |
