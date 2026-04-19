# Deterministic Flow — States & Transitions

> Design scratchpad for the new deterministic-flow FEAT (successor to the FEAT-005 lifecycle agent).
> Rows marked `(proposed)` are inferences from prior discussion — review and overwrite/confirm.

## Model

- **Flow engine** — passive source of truth for the current state of work items and tasks. No logic; no external intake.
- **Orchestrator** — owns all intake (admin actions, agent outputs, GitHub/CI webhooks), all transition logic, all dispatches. Writes new state to the flow engine.

Each transition has:

- **Signal source** — the real-world cause (admin, agent, dev, GitHub, CI, derived-from-child-state).
- **Intake channel** — how the orchestrator learns (HTTP endpoint, webhook, internal event).
- **Orchestrator action** — side effects after writing the new state to the engine.

## Actors (v1)

- **admin** — opens/closes work items, approves task proposals, assigns tasks, reviews agent-authored artifacts, acts as second reviewer for dev-implemented tasks (solo-dev v1).
- **dev** — authors/revises/approves plans on dev-assigned tasks, implements, receives rejections.
- **agent** — drafts plans, drafts implementations, drafts reviews.
- **external** — GitHub webhooks (PRs, merges), CI signals.

---

## Work Item State Machine

States: `open → in_progress ⇄ locked → ready → closed`
`locked` reachable only from `in_progress`, returns to `in_progress`.

| #  | From          | To            | Signal source                  | Intake channel                         | Orchestrator action |
|----|---------------|---------------|--------------------------------|----------------------------------------|---------------------|
| W1 | (none)        | `open`        | admin                          | `POST /work-items` (proposed)          | write state=open; dispatch task-generation agent |
| W2 | `open`        | `in_progress` | derived (first task approved)  | internal (from T2)                     | write state=in_progress if first approval |
| W3 | `in_progress` | `locked`      | admin                          | `POST /work-items/{id}/lock` (proposed)| write state=locked; pause pending dispatches |
| W4 | `locked`      | `in_progress` | admin                          | `POST /work-items/{id}/unlock` (proposed)| write state=in_progress; resume pending dispatches |
| W5 | `in_progress` | `ready`       | derived (all tasks terminal)   | internal (from T10/T12)                | write state=ready |
| W6 | `ready`       | `closed`      | admin                          | `POST /work-items/{id}/close` (proposed)| write state=closed |

---

## Task State Machine

States: `proposed ⇄ approved → assigning → planning → plan_review → implementing → impl_review → done`
Plus `deferred` from any non-terminal (admin signal).
Rejection edges: `plan_review → planning`, `impl_review → implementing` (same owner, unbounded).

| #   | From          | To            | Signal source                         | Intake channel                                | Orchestrator action |
|-----|---------------|---------------|---------------------------------------|-----------------------------------------------|---------------------|
| T1  | (none)        | `proposed`    | agent (task-generation output)        | internal (dispatched from W1)                 | write state=proposed per task draft |
| T2  | `proposed`    | `approved`    | admin                                 | `POST /tasks/{id}/approve` (proposed)         | write state=approved; fire W2 if first; move to T4 |
| T3  | `proposed`    | `proposed`    | admin (reject with feedback)          | `POST /tasks/{id}/reject` (proposed)          | attach feedback; notify proposer for revision |
| T4  | `approved`    | `assigning`   | derived (from T2)                     | internal                                      | write state=assigning; notify admin to assign |
| T5  | `assigning`   | `planning`    | admin (writes assignee)               | `POST /tasks/{id}/assign` (proposed)          | write state=planning; dispatch plan-draft agent if agent-assigned |
| T6  | `planning`    | `plan_review` | agent or dev (plan submitted)         | `POST /tasks/{id}/plan` (proposed)            | write state=plan_review; route to approver per matrix |
| T7  | `plan_review` | `implementing`| approver (dev self-signal or admin)   | `POST /tasks/{id}/plan/approve` (proposed)    | write state=implementing; dispatch implementation agent if agent-assigned |
| T8  | `plan_review` | `planning`    | approver (reject)                     | `POST /tasks/{id}/plan/reject` (proposed)     | attach feedback; write state=planning; notify author |
| T9  | `implementing`| `impl_review` | dev (PR opened) or agent (done)       | GitHub webhook on PR open / `POST /tasks/{id}/implementation` (proposed) | write state=impl_review; route to reviewer per matrix |
| T10 | `impl_review` | `done`        | reviewer (approve)                    | `POST /tasks/{id}/review/approve` (proposed)  | write state=done; allow PR merge; fire W5 check |
| T11 | `impl_review` | `implementing`| reviewer (reject)                     | `POST /tasks/{id}/review/reject` (proposed)   | attach feedback; write state=implementing; notify implementer |
| T12 | any non-term. | `deferred`    | admin                                 | `POST /tasks/{id}/defer` (proposed)           | write state=deferred; fire W5 check |

### Approval Matrix (for reference)

| Stage         | Dev-assigned task          | Agent-assigned task       |
|---------------|----------------------------|---------------------------|
| `proposed`    | admin                      | admin                     |
| `plan_review` | same dev (self-signal)     | admin                     |
| `impl_review` | another dev (= admin v1)   | admin                     |

---

## Signal Catalogue (derived from tables above)

Consolidated set of distinct external intake channels. Internal/derived transitions are not listed — they fire from other transitions completing.

| # | Signal name              | Source  | Intake channel                         | Triggers |
|---|--------------------------|---------|----------------------------------------|----------|
| S1 | open-work-item          | admin   | `POST /work-items`                     | W1 |
| S2 | lock-work-item          | admin   | `POST /work-items/{id}/lock`           | W3 |
| S3 | unlock-work-item        | admin   | `POST /work-items/{id}/unlock`         | W4 |
| S4 | close-work-item         | admin   | `POST /work-items/{id}/close`          | W6 |
| S5 | approve-task            | admin   | `POST /tasks/{id}/approve`             | T2 (→ W2 if first, → T4) |
| S6 | reject-task             | admin   | `POST /tasks/{id}/reject`              | T3 |
| S7 | assign-task             | admin   | `POST /tasks/{id}/assign`              | T5 |
| S8 | submit-plan             | agent/dev | `POST /tasks/{id}/plan`              | T6 |
| S9 | approve-plan            | dev/admin | `POST /tasks/{id}/plan/approve`      | T7 |
| S10 | reject-plan            | dev/admin | `POST /tasks/{id}/plan/reject`       | T8 |
| S11 | submit-implementation  | dev (PR) / agent | GitHub webhook / `POST /tasks/{id}/implementation` | T9 |
| S12 | approve-review         | admin/dev | `POST /tasks/{id}/review/approve`    | T10 (→ W5 check) |
| S13 | reject-review          | admin/dev | `POST /tasks/{id}/review/reject`     | T11 |
| S14 | defer-task             | admin   | `POST /tasks/{id}/defer`               | T12 (→ W5 check) |

---

## Resolved Decisions

- **W5 is derived** from "all tasks in terminal state" (no explicit admin gate).
- **`assigning` stays as a distinct state** (separate from `approved` and `planning`) for auditing — lets us measure "time unassigned" and makes the admin's assignment action first-class.
- **PR-merge coupling is in scope.** Orchestrator controls merge gating as a required check on the PR; T10's approval is what releases the merge. This is the first of several planned external-system integrations (GitHub, later others) — the orchestrator expands its capabilities to interact with multiple systems, PR management being the first.

## Open Questions (Deferred)

- **Dissent/rejection audit trail** — where rejected plans and implementations are persisted (attached to task as history entries, or a separate artifact). Defer.
- **Signal authorization** — how the orchestrator authenticates `admin` vs `dev` on each POST. Out of scope for v1.
