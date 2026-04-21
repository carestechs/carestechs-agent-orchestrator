# FEAT-006 Deterministic Lifecycle Signal Flow

> **Scope:** the human-driven (or tool-driven) HTTP signal surface that
> moves a work item through its lifecycle. Every step is a caller hitting
> one endpoint; no LLM is involved.
>
> **Status snapshot (as of FEAT-007 landing):** signals + GitHub
> merge-gating are live. Task-generation dispatch is still a stub —
> tasks must be seeded manually.

---

## Stage 0 — Open a work item

**Call:** `POST /api/v1/work-items` with `{externalRef, type, title}`.

- Creates a `WorkItem` row at `status=proposed`.
- If `FLOW_ENGINE_LIFECYCLE_BASE_URL` is configured, the orchestrator
  also creates an item in the flow engine and stores `engine_item_id`.
  If not, local-only.
- Response: `202` with the new work-item DTO.

## Stage 1 — Brief approved (W1)

**Call:** `POST /api/v1/work-items/{id}/brief-approved` (admin).

- Transitions `proposed → pending_tasks`.
- Mirrors to engine if wired.
- **Dispatches task generation.** Today this is a stub that logs
  `"task-generation dispatched for work_item X"` and no tasks get
  auto-created. In practice, you seed tasks manually by inserting rows
  (the e2e test does this) or via `POST /work-items/{id}/tasks` if
  exposed.

## Stage 2 — Task approval / rejection / defer (S5 / S6 / S14)

Each task lands at `proposed`. Admin reviews each.

- `POST /api/v1/tasks/{id}/approve` → `proposed → assigning`
  (transition **T4**).
- `POST /api/v1/tasks/{id}/reject` with `{feedback}` → records an
  `Approval` row with `decision=reject`. Task stays at `proposed` (no
  state change — rejection is informational until re-proposed). This is
  the "rejection asymmetry" called out in CLAUDE.md.
- `POST /api/v1/tasks/{id}/defer` with `{reason}` → `proposed → deferred`
  (**T14**). Terminal. Counts as "resolved" for work-item readiness.

**Derivation W2** fires the moment the *first* task reaches `assigning`
(or anything past `proposed`): work item `pending_tasks → in_progress`.
Emitted as an `item.transitioned` webhook from the engine → consumed by
`lifecycle/reactor.py`. If no engine, derivation runs inline.

## Stage 3 — Task assignment (S7)

**Call:** `POST /api/v1/tasks/{id}/assign` with
`{assigneeType: "dev"|"agent", assigneeId}`.

- Inserts a `TaskAssignment` row.
- `assigning → assignment_review` (**T5**).

Then an approval:

- `POST /api/v1/tasks/{id}/assign/approve` → `assignment_review → planning`
  (**T6**).

The approval matrix depends on `solo_dev_mode`:

- `true` (default): admin approves.
- `false`: a different dev than the implementer approves.

## Stage 4 — Plan submit + review (S8 / S9 / S10)

**Submit:** `POST /api/v1/tasks/{id}/plan` with `{planPath, planSha}`
(role depends on assignee: dev for dev-assigned, admin for agent-assigned).

- Inserts `TaskPlan`.
- `planning → plan_review` (**T7**).

**Approve:** `POST /api/v1/tasks/{id}/plan/approve` →
`plan_review → implementing` (**T9**).

**Reject:** `POST /api/v1/tasks/{id}/plan/reject` with `{feedback}` →
records `Approval(reject)` with feedback. Task stays at `plan_review`
(same rejection asymmetry); implementer re-submits a new plan.

## Stage 5 — Implementation submit (S11) — *FEAT-007 gate starts here*

**Call:** `POST /api/v1/tasks/{id}/implementation` with
`{prUrl, commitSha, summary}`.

What happens in order, inside `submit_implementation_signal`:

1. **Idempotency check.** `lifecycle_signals` row inserted keyed on
   `(task_id, "submit-implementation", hash(payload))`. Replay returns
   the current task + `already_received=true`.
2. **Correlation row.** `PendingSignalContext` row gets a UUID; it's
   encoded in the engine comment so the webhook reactor can match the
   event back.
3. **State machine.** `tasks.submit_implementation` flips
   `implementing → impl_review` (**T10**), mirrors to engine.
4. **TaskImplementation row** inserted with `pr_url`, `commit_sha`,
   `summary`, `submitted_by`.
5. **Commit.**
6. **GitHub check create** (FEAT-007). If the Checks client is real and
   `prUrl` is set:
   - Parse `prUrl` → `(owner, repo, pull_number)`.
   - `POST https://api.github.com/repos/{owner}/{repo}/check-runs` with
     `{name: "orchestrator/impl-review", head_sha: commitSha, status: "in_progress"}`.
   - Store returned `check_id` on the `TaskImplementation` row, commit again.
   - If GitHub 5xx/timeout: log warning, `github_check_id` stays NULL,
     signal still returns 202.
   - If the client is noop: store `"noop"` sentinel, skip HTTP entirely.

**Alternate entry: GitHub PR webhook.** If a PR is opened with `T-NNN`
in title/body, `/hooks/github/pr` matches the task by `external_ref` and
calls `submit_implementation_signal` internally with `pr_url=None` — so
state advances but no check-run is posted (gap: today the webhook path
doesn't thread `prUrl` through).

## Stage 6 — Review approve / reject (S12 / S13)

**Approve:** `POST /api/v1/tasks/{id}/review/approve` (admin if
`solo_dev_mode`; else another dev).

- State: `impl_review → done` (**T11**).
- `work_items.maybe_advance_to_ready` checks if *all* tasks on the work
  item are terminal (`done | deferred`). If yes, work item flips
  `in_progress → ready` (**derivation W5**).
- Commit.
- **GitHub check update:** look up latest `TaskImplementation`, if it
  has a real `check_id`, PATCH to `conclusion=success`. Noop/NULL paths
  skip.

**Reject:** `POST /api/v1/tasks/{id}/review/reject` with `{feedback}`.

- Records `Approval(reject)` with feedback. Task stays at `impl_review`.
  Implementer submits a new implementation (back to S11, which creates
  a *new* check-run; the rejected one stays at `failure` on GitHub).
- **GitHub check update:** PATCH the stored `check_id` to
  `conclusion=failure`.

Correction budget: `LIFECYCLE_MAX_CORRECTIONS` (default 2) caps the
number of rejections per task; exceeding it would terminate an agent
run — not enforced on the signal path currently.

## Stage 7 — Close the work item (S4)

Once work item is at `ready`:

**Call:** `POST /api/v1/work-items/{id}/close` with `{notes}` (admin).

- `ready → closed` (**W6**). Terminal.

## Optional mid-flow signals

- **`POST /work-items/{id}/lock`** (S2) with `{reason}` — freezes the
  work item (`any → locked`). Stores `locked_from` so unlock knows where
  to restore. No new task work happens while locked.
- **`POST /work-items/{id}/unlock`** (S3) — `locked → locked_from`.

---

## State summary — entities touched per signal

| Signal | Work item | Task | Aux row written |
|--------|-----------|------|-----------------|
| S1 open | → proposed | — | — |
| W1 brief-approved | → pending_tasks | — | — (stub dispatch) |
| S5 approve | — (W2 may fire) | → assigning | Approval |
| S6 reject | — | — | Approval |
| S14 defer | — (W5 may fire) | → deferred | — |
| S7 assign | — | → assignment_review | TaskAssignment |
| S7b assign-approve | — | → planning | Approval |
| S8 plan | — | → plan_review | TaskPlan |
| S9 plan-approve | — | → implementing | Approval |
| S10 plan-reject | — | — | Approval |
| S11 implementation | — | → impl_review | TaskImplementation (+ GitHub check-run) |
| S12 review-approve | W5: → ready | → done | Approval (+ check → success) |
| S13 review-reject | — | — | Approval (+ check → failure) |
| S2 lock | → locked | — | — |
| S3 unlock | → prior status | — | — |
| S4 close | → closed | — | — |

Derivations (no direct caller, fired by reactor on engine webhook or
inline when no engine):

- **W2** — first task leaves `proposed` → work item `in_progress`.
- **W5** — all tasks terminal (`done | deferred`) → work item `ready`.

---

## What's not implemented in this flow

- **Task generation** — `dispatch_task_generation` is a stub. Seed tasks
  manually.
- **PR-webhook → check-run** — the `/hooks/github/pr` path calls
  `submit_implementation_signal` with `pr_url=None`, so it advances task
  state but never posts a check.
- **Check reset on PR reopen** — a `failure`-resolved check stays
  `failure` if the PR is reopened and re-pushed.
- **Agent-to-signal wiring** — the FEAT-005 lifecycle agent updates
  local memory, not these endpoints. Cross-driving the signal surface
  from an agent run is unscoped in v1.
