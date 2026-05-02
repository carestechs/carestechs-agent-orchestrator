# Feature Brief: FEAT-014 — Operator UI

> **Purpose**: Give operators a minimal browser surface for the things curl-against-the-API currently covers — list active runs, see what's paused awaiting a signal, send the signal, tail the trace. The trace half already exists as an NDJSON stream; this feature is the **act-on-it** half. Without it the orchestrator is unusable by anyone except its author. With a thin in-process UI, operators can drive lifecycle runs from a browser with no client-side build.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-014 |
| **Name** | Operator UI (paused-runs list, run detail, signal-send form, trace tail) |
| **Target Version** | v0.4.0 |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | 2026-05-02 architecture audit (operator session) — flagged as one of three critical-but-missing pieces alongside FEAT-013 (diff effector) and IMP-003 (swappable reviewer). |
| **Date Created** | 2026-05-02 |

---

## 2. User Story

**As an** operator running lifecycle agents end-to-end, **I want to** see which runs are paused and send signals from a browser without writing JSON payloads by hand, **so that** delivering operator decisions doesn't require shell access, and so that anyone on the team — not just the orchestrator's author — can drive a run through its human checkpoints.

---

## 3. Goal

A logged-in operator can, from a single page, see every paused run in the system, click into a run, see its trace tail and the awaiting human-pause node, fill in a signal-payload form (with the right fields surfaced from the agent declaration), and submit. No CLI required for the common path. No separate frontend repo, no client-side build, no JS framework — server-rendered HTML with HTMX for partial updates is sufficient.

---

## 4. Feature Scope

### 4.1 Included

- **Active runs list** (`GET /ui/runs`) — table of recent runs with status, agent ref, started_at, current node, last activity.
- **Paused runs list** (`GET /ui/runs?status=paused`) — filter to runs awaiting an operator signal. The default landing page when there are paused runs.
- **Run detail page** (`GET /ui/runs/{id}`) — status header, current node, agent ref, intake summary, live trace tail (HTMX-polled or SSE), most-recent step / dispatch state.
- **Signal form** (`POST /ui/runs/{id}/signals`) — when the run is paused on a `mode=human` dispatch, render a form with `name` (defaulted from the awaiting executor's `expected_signal_name`), `taskId` (defaulted from `LifecycleMemory.current_task_id`), and a `payload` text area accepting JSON. Submits to the existing `POST /api/v1/runs/{id}/signals` endpoint.
- **Cancel button** — one-click cancel on the detail page (`POST /ui/runs/{id}/cancel` → existing cancel endpoint). Confirm dialog only.
- **Auth via existing API key**, exchanged for a signed cookie at `/ui/login`. Same secret, same threat model — no new credential surface.
- **Server-rendered Jinja2 templates** + HTMX for partial updates (trace tail, list refresh). Zero npm.
- **Bundled with the orchestrator** — same Docker image, same FastAPI app, same Postgres. Mounted under `/ui/*`.

### 4.2 Excluded

- **Multi-tenant / per-user permissions.** v1 has one operator role; everyone with the API key has full access. Multi-tenant is FEAT-???.
- **Run start form.** Starting a run is the trigger point; that's better-served by the issue-tracker / CI integration than by a UI form. Operators rarely start runs by hand.
- **Run editing beyond signal/cancel.** No "rewrite memory," "skip a step," "change agent ref mid-run." Recovery from a failed run is a re-run, not an edit.
- **Search / filter beyond `?status=`.** Date-range search, agent-ref search, full-text trace search — all FEAT-???. v1 surfaces "what's paused" and "what's recent."
- **Mobile-first design.** Operator workstation is a laptop. The UI is responsive enough not to break, but the design target is desktop.
- **Real-time multi-user collab.** Two operators on the same paused run will both see the form; the second one's signal hits `alreadyReceived=true` (BUG-011 wake still fires — correct behaviour). No live presence indicators.
- **Custom dashboards / saved views.** Out of scope.
- **Frontend framework migration.** Astro / SvelteKit / etc. is a future option if the in-process UI grows past what HTMX comfortably handles. v1 deliberately stays minimal.

---

## 5. Acceptance Criteria

1. An operator with a valid API key can log in at `/ui/login` and land on a paused-runs list.
2. The paused-runs list shows every run with `status=paused`, including the agent ref, the awaiting node name, the current task id, and the time since pause.
3. Clicking a paused run opens a detail page with a fillable signal form pre-populated with the awaiting `name` and `taskId`.
4. Submitting the form delivers the signal via the existing `POST /api/v1/runs/{id}/signals` endpoint and the run resumes (status flips back to `running` per IMP-002).
5. The detail page tails the run's NDJSON trace and updates without a full page refresh.
6. The active-runs list refreshes via HTMX every N seconds without flickering.
7. An operator can cancel a non-terminal run from the detail page.
8. An unauthenticated user hitting any `/ui/*` route is redirected to `/ui/login`.
9. The UI ships in the same Docker image as the API; no extra build step in CI.
10. Page weight: every UI page renders in under 200ms cold (excluding trace polling) on a laptop.

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `Run` | No schema change; UI reads existing fields | `docs/data-model.md` |
| `Dispatch` | UI reads in-flight rows to compute "awaiting signal name" | `docs/data-model.md` |
| FastAPI app | New `app/modules/ui/` module with router, templates, dependencies | `docs/ARCHITECTURE.md` |
| Static assets | New `app/modules/ui/static/` (one CSS file, htmx.min.js bundled) | — |
| Templates | New `app/modules/ui/templates/` Jinja2 hierarchy | — |
| Auth | New `/ui/login` endpoint exchanges API key → signed session cookie. Existing `get_api_key` dep extended to also accept the cookie. | `docs/api-spec.md` |

No DB migration. The UI is a new module on the existing app. Reuses every existing service-layer function — no service-layer changes.

---

## 7. Architectural Notes

- **In-process** — same FastAPI app, same lifespan, same DB pool. No separate process. The UI is a router mounted under `/ui/*`.
- **Module placement** — `src/app/modules/ui/` mirrors `modules/ai/` layout (router, dependencies, templates, static). Fits the existing module convention.
- **Templates** — Jinja2 via FastAPI's standard `Jinja2Templates`. No custom templating layer.
- **HTMX** — bundled minified file served from `/ui/static/htmx.min.js`. No CDN dependency.
- **CSS** — one hand-written stylesheet, no Tailwind / no PostCSS. Operator UI is utility-functional, not branded.
- **Trace tail** — uses the existing `GET /api/v1/runs/{id}/trace?follow=true` NDJSON stream. UI's detail page sets up a small HTMX SSE subscription or polled-fragment swap.
- **Auth** — `/ui/login` accepts the API key as a form field, signs it into a session cookie via `itsdangerous` (already a Starlette dep). Session cookie expires after 12 hours. No password, no user accounts — same threat model as the API key today.
- **No JSON API duplication** — the UI calls into `service.py` functions directly (in-process). It does NOT proxy through the JSON API. That keeps the routes thin and avoids double-validation.

This is the lightest possible shape that meets the acceptance criteria. If it grows into something heavier (multi-tenant, real-time collab, dashboards), the natural next step is splitting into a separate Astro / Next app talking to the JSON API — but that's a v0.5+ concern.

---

## 8. Task Breakdown (Preliminary)

A separate task-generation pass produces final `T-NNN` definitions; this list is the design-time decomposition.

1. **Module skeleton** — `app/modules/ui/` directory, router stub, base template, static asset mounting, lifespan registration.
2. **Auth + session** — `/ui/login` form + cookie sign/verify, `get_session_or_redirect` dep, integration with existing `get_api_key`.
3. **Active runs list page** — query + table render, HTMX auto-refresh.
4. **Paused runs list page** — `?status=paused` filter, surface awaiting-signal info from the in-flight Dispatch rows.
5. **Run detail page** — header, status, current node, intake summary, recent steps/dispatches.
6. **Trace tail panel** — HTMX-driven NDJSON stream consumption on the detail page.
7. **Signal form** — pre-populated from awaiting executor binding, posts to existing service function, displays `alreadyReceived` feedback.
8. **Cancel action** — confirm dialog + POST to existing cancel service.
9. **Layout polish + minimal CSS** — readable defaults, responsive enough not to break.
10. **Smoke test** — Playwright or pytest+httpx walks through login → list → detail → signal-submit → assert run resumes.

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| HTMX trace-tail polling overwhelms the trace store | Low | Medium | Existing trace stream already throttles file polling at ~200ms; UI subscribes via the same stream, no per-UI-client backend cost. |
| Adding Jinja2 + UI bloats the API image / lifespan boot time | Low | Low | Jinja2 is stdlib-adjacent; itsdangerous is already a dep. No new heavy libraries. |
| Session-cookie auth weaker than API-key header | Low | Medium | Same secret, same threat model. Cookie is `HttpOnly` + `Secure` + `SameSite=Strict` + 12h expiry. Operators on shared machines get the standard browser-session warning. |
| UI introduces XSS / CSRF surface | Medium | High | Jinja2 auto-escape on by default. CSRF token middleware on every POST (`itsdangerous`-signed token). All form submits go through the token check. |
| UI becomes the de-facto API — feature creep into things that should be CLI / JSON-API features | Medium | Medium | Hard scope rule: UI only renders / submits — never adds capabilities the JSON API or CLI doesn't already have. New behaviour lands in the service layer first; UI exposes it second. |
| Concurrent edits — two operators sending the same signal at once | Low | Low | BUG-011 dedupe + active-dispatch wake handles this correctly already. UI displays `alreadyReceived=true` cleanly. |

---

## 10. Constraints

- **No client-side build step.** No npm, no webpack, no SvelteKit. Operators need to be able to `docker compose up` and have a working UI.
- **No new top-level service.** UI ships in the same FastAPI app, same Docker image.
- **No service-layer changes.** All UI routes call existing `service.py` functions. New UI behaviour requires a service-layer commit first.
- **Auth cannot weaken the API surface.** Sharing the API key for cookie exchange is the only auth source — no new credential type.
- **Match the existing module convention.** `modules/ui/` follows the same shape as `modules/ai/` — router, dependencies, templates, static. Test placement mirrors it.

---

## 11. Success Metrics

- An operator who has never seen the orchestrator before can deliver an `implementation-complete` signal end-to-end in under 60 seconds (login → paused-runs → detail → form → submit).
- Time-to-action for a paused run drops from "open shell, find run id, craft curl" (3-5 min) to "click form, submit" (< 30s).
- Smoke runs no longer require a shell at all.
- UI modules / templates / static assets stay under 1500 LoC total (signal that the scope is honest).

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | 2026-05-02 architecture audit — gap (4) "No human-in-the-loop UI. Everything is curl + database peek." |
| **Stakeholder Alignment** | Operator signal delivery is the lifecycle's central human checkpoint. A UI for it is the smallest piece that takes the orchestrator from "the author can use this" to "the team can use this." |
| **Architecture Reference** | `docs/ARCHITECTURE.md` — module boundaries; `CLAUDE.md` — pattern: "no frontend in v1" — explicitly relaxed by this FEAT for v0.4.0 because the executor seam is now stable enough to support a UI without bleeding into runtime concerns. |
| **Related Work Items** | IMP-003 (stub reviewer; the UI's smoke-test path benefits from stub-pass mode), FEAT-013 (diff effector; once it ships the run-detail page can render the diff inline next to the reviewer's verdict — natural follow-on), BUG-011 (wake-on-duplicate-signal; UI's "send again" affordance depends on this) |
| **Blocked Features** | Run dashboards, multi-tenant access control, audit-trail UI — all build on this base. |
| **CLAUDE.md Update Required** | "Don't introduce a frontend in v1" anti-pattern is replaced with: "v1 had no frontend. v0.4.0 introduces an in-process operator UI under `/ui/*` with explicit scope (FEAT-014). Any UI work outside that scope re-opens the conversation." |
