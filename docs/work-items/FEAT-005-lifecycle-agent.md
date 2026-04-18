# Feature Brief: FEAT-005 — Lifecycle Agent v1 + Self-Hosted Feature Delivery Proof

> **Purpose**: Ship the first *real* agent — a lifecycle agent whose flow mirrors the ia-framework's 8-stage loop (intake → task generation → task assignment → plan creation → implementation → review → corrections → closure), runs against this very repo, and drives one real work item from a markdown brief to `Status: Completed` with every stage traced. This is the feature that converts Stakeholder Success Metric #1 ("≥1 feature of this repo shipped end-to-end via the orchestrator") from aspirational to proven. It rests on FEAT-002 (runtime loop), FEAT-003 (Anthropic policy), and FEAT-004 (trace streaming) — all of which ship infrastructure whose value is only realized once an actual agent plugs into them.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-005 |
| **Name** | Lifecycle Agent v1 + Self-Hosted Feature Delivery Proof |
| **Target Version** | v0.5.0 |
| **Status** | Not Started |
| **Priority** | Critical |
| **Requested By** | Tech Lead (`ai@techer.com.br`) |
| **Date Created** | 2026-04-18 |

---

## 2. User Story

**As a** solo tech lead driving feature delivery (see `docs/personas/primary-user.md`), **I want to** drop a filled-in work item at `docs/work-items/IMP-XXX.md`, run `orchestrator run lifecycle-agent@0.1.0 --intake workItemPath=docs/work-items/IMP-XXX.md --follow`, and watch the orchestrator generate a task list, write one plan per task, pause for me to execute implementations in Claude Code (signaled back via `orchestrator tasks mark-implemented T-XXX`), run an automated review against the task's acceptance criteria, loop on corrections up to a bounded cap, and finally edit the work-item's Status field to `Completed` — **so that** the framework's discipline runs itself on me, instead of me re-running stage prompts by hand, and the claim "this orchestrator drives feature delivery" becomes a test I can point at.

---

## 3. Goal

`orchestrator run lifecycle-agent@0.1.0 --intake workItemPath=docs/work-items/<id>.md --follow` loads an 8-node YAML agent definition under `agents/`, iterates through each stage with the Anthropic provider from FEAT-003 making decisions, writes concrete artifacts to the repo at every write-stage (`tasks/<id>-tasks.md`, `plans/plan-T-XXX-*.md`, in-place Status edits on the brief), suspends cleanly at the implementation stage for an operator-injected signal, resumes, runs an automated review against `git diff main..HEAD` for the task under review, and terminates with `stop_reason=done_node` + the brief's Status set to `Completed`. With `LLM_PROVIDER=stub`, the same flow completes deterministically (the stub picks first available tool) and produces placeholder artifacts — satisfying AD-3 composition integrity in both directions. The proof is a real, small, non-trivial work item from this repo's own backlog, shipped through this agent, with the commit history showing each stage's output.

---

## 4. Feature Scope

### 4.1 Included

- **Lifecycle-agent YAML** at `agents/lifecycle-agent@0.1.0.yaml`. Declares all 8 stage nodes (names: `intake`, `task_generation`, `task_assignment`, `plan_creation`, `implementation`, `review`, `corrections`, `closure`), a permissive `flow.transitions` map that allows the policy to loop (e.g., `plan_creation → plan_creation` for per-task iteration; `review → corrections` on failure), an `intake_schema` that declares `workItemPath: str`, and `terminal_nodes: {closure}`. Each node's `input_schema` matches the tool arguments Claude is expected to emit for that stage.
- **Per-node system-prompt references**: a minimal additive extension to `AgentDefinition` — a new optional field `policy.system_prompts: dict[node_name, path]` on `AgentDefinition` (or nested under a new `AgentPolicy` block) that lets a node declare "when the policy is invoked at *this* node, use this file's contents as the system prompt". For the lifecycle agent, nodes point at the existing `.ai-framework/prompts/feature-tasks.md`, `bugfix-tasks.md`, `refactor-tasks.md`, `plan-generation.md`. Loader validates that every referenced path exists; prompts are read at run start and cached per run. Non-prompt nodes (intake, task_assignment, closure) use a built-in default prompt.
- **Tool implementations** in `src/app/modules/ai/tools/lifecycle/`. One module per tool. Each is a thin adapter delegating to a service function (per CLAUDE.md "tools are thin adapters"):
  - `load_work_item(path)` — reads YAML front-matter (or leading table) + markdown body; returns `{"id", "type", "status", "title", "body"}`. Fails with `PolicyError` if the file is missing, the Status is already `Completed`/`Cancelled`, or the type isn't one of `FEAT|BUG|IMP`.
  - `generate_tasks(work_item_id, tasks_markdown)` — accepts the LLM's rendered tasks document, writes atomically to `tasks/<work_item_id>-tasks.md` via temp-file-rename. Refuses to overwrite an existing file (run-start precondition: the target tasks file doesn't already exist; caller gets a clear error).
  - `assign_task(task_id, executor)` — deterministic v1: every task's executor is `local-claude-code` (hardcoded; the node writes this back into the run memory's task list). Tool exists to preserve the stage's visibility in the trace even though it's not LLM-driven.
  - `generate_plan(task_id, plan_markdown)` — writes to `plans/plan-<task_id>-<slug>.md` atomically. Refuses to overwrite. Slug is derived from the task title by the caller (truncated + lowercased + spaces → hyphens; non-`[a-z0-9-]` dropped).
  - `wait_for_implementation(task_id)` — *does not* write a file. Emits a `Step` row with `status=in_progress`, persists a trace entry of kind `step` with `node_name=implementation`, and returns control to the runtime loop with a special `PAUSE` sentinel. The runtime treats this like a normal dispatched node whose completion event will arrive via the new operator-signal endpoint (see below), not via the engine webhook.
  - `review_implementation(task_id, verdict, feedback)` — accepts a structured verdict `pass|fail` plus freeform `feedback`. Writes to `plans/plan-<task_id>-<slug>-review-<attempt>.md`. The policy is expected to invoke this *after* reading `git diff` output that the node surfaces as input (see below). `verdict=fail` routes to `corrections`; `pass` routes to the next task or `closure`.
  - `close_work_item(work_item_id)` — edits the work-item markdown in place, setting the `Status` field to `Completed` and adding a `Completed: <ISO-8601>` timestamp under the Identity table. Atomic temp-file-rename. Refuses to edit if the Status isn't `In Progress` at close time (guards concurrent human edits).
- **Git-diff context for review**. The `review` node's `input_schema` includes `task_id`. Before invoking the policy, the runtime runs `git diff main...HEAD -- <files-touched-in-this-task-only>` (files tracked in memory per task from plan metadata) via `subprocess.run(['git', 'diff', ...], check=True, text=True, timeout=10)` inside a boundary function in `src/app/modules/ai/tools/lifecycle/git.py`; the diff is passed to the policy as part of the user-message body. `git` not available, not a repo, or detached-HEAD errors surface as `PolicyError`, terminating the run.
- **Operator-signal endpoint** `POST /api/v1/runs/{id}/signals` with body `{"name": "implementation-complete", "task_id": "T-XXX", "commit_sha": "...", "notes": "..."}`. Auth identical to other control-plane routes (`X-API-Key`). Persists a `WebhookEvent`-like row (new kind `operator_signal` on `WebhookEvent` — or a parallel `RunSignal` table; see Section 6), wakes the supervisor for the run, and returns `202`. Unknown signal names return `400`. Unknown `task_id` for the run returns `404`. Idempotency: two signals with the same `{run_id, name, task_id}` — second is a no-op (the first wake is sufficient; the loop's reconciliation doesn't re-advance a task already past `waiting_for_implementation`).
- **CLI** `orchestrator tasks mark-implemented T-XXX --run-id <id> [--commit-sha SHA] [--notes TEXT]` — thin client that POSTs to the signal endpoint and exits 0 on `202`.
- **Runtime-loop integration**: the runtime already supports `PAUSE`-like suspension via the supervisor's per-run `asyncio.Event`. FEAT-005 adds a minimal extension: when a policy-selected tool returns the new `PauseForSignal` marker, the loop persists the step as `in_progress`, releases its session, and awaits the supervisor's wake *without* dispatching to the engine. The signal endpoint's handler both persists the event *and* calls `supervisor.wake(run_id)`. No new supervisor surface needed.
- **Correction bound**. The `corrections` node increments a per-task counter in `RunMemory`. When attempts for a single task exceed `LIFECYCLE_MAX_CORRECTIONS` (default `2`, env-overridable), the runtime terminates the run with `stop_reason=error` and a `final_state.reason="correction_budget_exceeded"`. Bounded to prevent runaway loops.
- **Per-run memory shape**: typed `LifecycleMemory` in `src/app/modules/ai/tools/lifecycle/memory.py` with fields `work_item` (the loaded doc), `tasks` (ordered list of `{id, title, executor, status, plan_path}`), `current_task_id`, `review_history` (list of `{task_id, attempt, verdict, feedback}`), `files_touched_per_task` (dict), and `correction_attempts` (dict). Serialized as JSON in the trace at every decision.
- **Self-hosted proof artifact**. One real work item is authored specifically for this proof — `docs/work-items/IMP-002-lifecycle-proof.md` — whose scope is trivially small (a concrete, verifiable repo change such as "add a `uv run orchestrator agents show lifecycle-agent` smoke test"). The acceptance suite for FEAT-005 includes an integration test that drives this IMP through the agent end-to-end using a recorded Anthropic transcript (via `respx` + saved fixtures), and a separate `@pytest.mark.live`-guarded contract test that does the same thing against the real API. The run's output (tasks, plans, review, closed work item, commits) lands on a dedicated `feat/imp-002-lifecycle-proof` branch whose git history is cited in the PR description as evidence.
- **Documentation updates**:
  - `CLAUDE.md` gains a new "Lifecycle Agent" section under Patterns, documenting the agent's stage ↔ tool mapping, the pause/resume contract, and the self-hosted proof command.
  - `docs/ARCHITECTURE.md` adds a "Lifecycle Agent" entry under Runtime Loop Components describing the stage/tool surface and operator-signal transport.
  - `docs/data-model.md` documents the `RunSignal` row (or the `WebhookEvent.kind=operator_signal` extension, whichever is chosen in Section 6) and the `AgentDefinition.policy.system_prompts` field, with changelog entries.
  - `docs/api-spec.md` documents `POST /runs/{id}/signals` with request/response shape and examples, changelog entry.
  - `docs/ui-specification.md` documents the new CLI command.
  - `README.md` gains a "Self-hosted feature delivery" section with the exact commands and a link to the proof branch.
- **Stakeholder-definition status update**: at the end of the FEAT-005 work, flip Success Metric #1 in `docs/stakeholder-definition.md` from target-stated to target-met with a pointer to the proof branch / tag. Changelog entry.

### 4.2 Excluded

- **Spawning Claude Code as a subprocess / headless SDK integration.** The implementation stage is operator-driven in v1: the operator runs `claude` in another terminal, lands a diff, and signals back. Spawning a headless Claude Code session from the agent is a tantalizing future feature — it *is* the path to full autonomy — but it doubles the scope of this brief and depends on headless auth, branch-management, and subprocess lifecycle concerns that deserve their own feature. FEAT-006 territory.
- **Multi-work-item-in-flight.** One run drives one work item from intake to closure. Running three work items concurrently is a separate concern; each gets its own `orchestrator run` invocation. No cross-run coordination.
- **Cross-run memory.** Per AD-4. A lifecycle run's memory is discarded at termination; re-running against a partially-completed brief is not supported (the tools refuse to overwrite existing `tasks/*-tasks.md` or `plans/*.md`).
- **Automated `git` operations beyond `diff` for review.** No automatic `git add`, `git commit`, `git push`, or `git checkout`. The operator owns the git surface; the agent only reads (diff) and writes markdown files in the working tree.
- **Automated PR creation or CI gating.** `gh pr create` and CI-feedback ingestion are separate future features.
- **Non-work-item intake formats.** Only the existing `docs/work-items/FEAT-*.md`, `BUG-*.md`, `IMP-*.md` markdown format is supported. A free-form intake (e.g., a plain "add dark mode" sentence) requires a different intake strategy and is out of scope.
- **Task-list / plan regeneration.** Running the agent twice against the same work item (where tasks already exist on disk) is a hard error. Resumability from a mid-loop crash is future work — in v1, a crashed lifecycle run requires the operator to delete the partial artifacts and re-start.
- **Custom per-task executors beyond `local-claude-code`.** Multi-executor routing (e.g., "this task goes to Claude, that one to a human") is a later feature. The `assign_task` tool exists so the stage is visible, but its logic is constant.
- **Task-dependency graphs.** v1 iterates tasks in the order they appear in the generated list, one at a time. Parallel or DAG-ordered execution is future.
- **Corrections budget dynamic tuning / escalation to human.** Hitting the max-corrections cap ends the run with `error`; there is no "ask the human what to do" branch. Future.
- **Changes to `carestechs-flow-engine`.** AD-1 holds. The lifecycle-agent nodes dispatch to the engine the same way every other agent's nodes do (write-file tools run inside the engine as deterministic subflows per AD-1, triggered by the orchestrator via HTTP; the pause-for-signal node is the single exception — it is not dispatched to the engine at all, it is a runtime-local suspension).
- **Any modification to the `.ai-framework/prompts/*.md` files.** The lifecycle agent reads them verbatim. If a prompt needs to change, that's a `.ai-framework` maintenance concern, not a FEAT-005 change.
- **Retry/backoff tuning on Anthropic for lifecycle nodes specifically.** FEAT-003's retry ladder applies uniformly; no lifecycle-specific policy.

---

## 5. Acceptance Criteria

- **AC-1**: `orchestrator agents show lifecycle-agent@0.1.0` prints the 8-node definition, all terminal nodes, and the 4 referenced prompt paths (all resolved and readable). Asserted by a CLI smoke test.
- **AC-2**: `orchestrator run lifecycle-agent@0.1.0 --intake workItemPath=tests/fixtures/work-items/IMP-fixture.md` with `LLM_PROVIDER=stub` and a scripted stub that picks the first available tool at each stage completes without error and produces: `tasks/IMP-fixture-tasks.md` (placeholder content from the stub), at least one `plans/plan-T-*.md`, a review markdown, and the fixture's Status flipped to `Completed`. Asserted by an integration test with a temporary working directory. Composition-integrity (AD-3) proven end-to-end for the lifecycle agent.
- **AC-3**: Same invocation with `LLM_PROVIDER=anthropic` (mocked via `respx` against the Anthropic Messages endpoint with recorded tool-call fixtures) produces coherent artifacts: tasks document validates against `.ai-framework/prompts/feature-tasks.md`'s shape, each plan document has the expected sections per `plan-generation.md`, and the review verdict is structurally valid. Asserted by an integration test.
- **AC-4**: The `implementation` stage suspends the run. The run's status is `running`; the current step's status is `in_progress`; no engine dispatch has been emitted (respx assertion). `POST /api/v1/runs/{id}/signals` with `name=implementation-complete, task_id=T-001, commit_sha=abc1234` returns `202`, wakes the loop, and the loop advances to `review`. Asserted by an integration test.
- **AC-5**: `POST /api/v1/runs/{id}/signals` with unknown `name` returns `400` Problem Details (`type=/errors/invalid-signal-name`); with unknown `task_id` returns `404`; with a duplicate `{run_id, name, task_id}` is idempotent — second call is `202` but the loop is not re-woken (assertion on supervisor wake count).
- **AC-6**: Review `verdict=fail` routes the policy to the `corrections` node, which re-enters `implementation`, which re-suspends for another signal. A second `fail` plus a third retry exhausts the default `LIFECYCLE_MAX_CORRECTIONS=2` and terminates the run with `stop_reason=error`, `final_state.reason="correction_budget_exceeded"`. Asserted by an integration test.
- **AC-7**: `close_work_item` atomically edits the brief's Status to `Completed`; concurrent read by a separate process during the edit never sees a truncated file (temp-file-rename proven by a race test). A brief whose Status isn't `In Progress` at close time raises `PolicyError` and the run ends `error` — the brief is not modified.
- **AC-8**: The `load_work_item` tool refuses to proceed (with a clear `PolicyError`) for: missing file, Status already `Completed`/`Cancelled`, type not in `{FEAT,BUG,IMP}`. The tool accepts any of the three valid types, routing task generation through the matching `.ai-framework/prompts/` file (per `CLAUDE.md`'s routing table).
- **AC-9**: `generate_tasks`, `generate_plan`, and `review_implementation` each refuse to overwrite an existing output file at their target path. Attempting to run the agent twice against the same work item surfaces a clear error at the first write stage; the brief is not modified.
- **AC-10**: Trace completeness. Every stage's policy call is a `PolicyCall` row; every tool invocation is a `Step` row; every operator signal is persisted with `kind=operator_signal` and is visible via `orchestrator runs trace --follow --kind operator_signal`. The review's `git diff` input is included in the `PolicyCall.prompt_inputs` (or an equivalent field) so a reader can reconstruct the exact context the policy saw.
- **AC-11**: Live self-hosted proof. `docs/work-items/IMP-002-lifecycle-proof.md` exists, is driven through the orchestrator end-to-end on a dedicated branch, and ends with `Status: Completed`. The PR description for FEAT-005 links to the branch and to the run's trace file. The `@pytest.mark.live` contract test that replays this exact brief is green when `--run-live` is passed.
- **AC-12**: `uv run pyright`, `uv run ruff check .`, and the full `uv run pytest` suite are green. The adapter-thin quarantine test (`tests/test_adapters_are_thin.py`) is extended to forbid `subprocess` imports outside `modules/ai/tools/lifecycle/git.py`, `anthropic` outside FEAT-003's existing allow-list, and `yaml` outside the agent loader.
- **AC-13**: Docs discipline — `CLAUDE.md`, `ARCHITECTURE.md`, `data-model.md`, `api-spec.md`, `ui-specification.md`, and `README.md` all updated in the same PR stack; every changelog gets an entry dated 2026-04-18 (or the actual merge date). `stakeholder-definition.md` Success Metric #1 flips to "Met (see IMP-002)" with a citation.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|-----------------|--------------------|
| `AgentDefinition` | Additive field: `policy.system_prompts: dict[str, Path]`. Referenced prompt files validated at load time. | Unknown-node keys in `system_prompts` fail loader. Missing files fail loader. Prompts read once per run, cached. |
| `RunSignal` (**new**) | Persists operator-injected signals (`implementation-complete`, future siblings). Columns: `id UUID`, `run_id UUID FK`, `name text`, `task_id text NULL`, `payload JSONB`, `received_at timestamptz`, `dedupe_key text UNIQUE`. | `dedupe_key = hash(run_id, name, task_id)`. Idempotent via unique constraint. Persisted before the supervisor is woken (mirror of webhook ordering). |
| `Run` | Unchanged schema. `final_state` grows a new optional `reason` discriminator for `correction_budget_exceeded`. | Additive to FEAT-002's `final_state` shape — no schema change, new string value is documented. |
| `Step` | Unchanged schema. Steps at the `implementation` stage never have a `engine_run_id` — they are runtime-local suspensions. | A NULL `engine_run_id` on a step is now valid *only* for the `implementation` stage. Schema-level check relaxed; service-layer validation enforces the stage constraint. |
| `RunMemory` | `LifecycleMemory` is the first concrete typed shape. Serialized to JSON in the trace at every policy call. | Per-run scope only (AD-4). Never shared across runs. |
| `PolicyCall.prompt_inputs` (or equivalent) | `review` stage includes the raw `git diff` output as part of prompt context. | Diff output truncated at 64 KB to keep traces small; truncation marker included. |

**New entities required:** `RunSignal` — add to `docs/data-model.md` with full columns + changelog.

**Alternative considered:** extending `WebhookEvent` with a new `kind=operator_signal` and nullable `task_id`. Rejected because `WebhookEvent` is semantically "from the engine" per AD-2 — signals are from operators, and conflating the two muddies auth (HMAC vs. API key), retry semantics (engine retries vs. operator idempotency), and trace filtering (`--kind webhook_event` shouldn't surface human input). A separate table is clearer.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs/{id}/signals` | POST | **New** | Body `{ name, task_id?, payload? }`. Auth: `X-API-Key`. Responses: `202 Accepted` on persist+wake, `400` unknown signal name, `404` unknown run or task, `409` on terminal-state run (cannot signal a closed run). |
| *(everything else)* | — | Unchanged | No changes to `/runs`, `/runs/{id}`, `/runs/{id}/trace`, `/steps`, `/policy-calls`, `/agents`, `/hooks/engine/*`. |

**New endpoints required:** `POST /api/v1/runs/{id}/signals` — add to `docs/api-spec.md` with request/response schemas, error cases, and a curl example. Changelog entry.

Response envelope identical to the rest of the control plane: `{ data: RunSignalDto, meta?: {...} }` — `RunSignalDto` mirrors the new row.

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| CLI (`orchestrator tasks mark-implemented`) | **New** | Thin client over `POST /runs/{id}/signals`. Options: `--run-id` (required), `--commit-sha`, `--notes`. Exit codes: `0` accepted, `1` run not found, `2` run already terminal (409), `3` auth / server 5xx. |
| CLI (`orchestrator run`) | Unchanged surface | The lifecycle agent is just another agent; the existing `run` command drives it. |
| CLI (`orchestrator agents show`) | Unchanged surface | Lists the lifecycle agent's nodes + resolved prompt paths like any other agent. |

**New screens required:** None (CLI-only per stakeholder scope).

---

## 9. Edge Cases

- **Brief references a type not in the routing table.** `load_work_item` reads `type` from the frontmatter; unknown types (`DOC`, `SPIKE`, etc.) fail with a clear `PolicyError`. The v1 contract covers `FEAT|BUG|IMP` only.
- **Brief's body refers to files that don't exist** (e.g., a task says "modify `src/old_module.py`" but the module is gone). The agent does NOT validate file references during task generation — that's the operator's concern during the implementation stage. Review catches mismatches via the diff check (empty diff for a claimed-completed task fails the review).
- **Task list generated is empty** (LLM decides no tasks are needed). Treated as a `PolicyError` — a valid lifecycle run requires at least one task. The run ends `error`. Operator can split or enrich the brief and re-run.
- **Plan file slug collision.** Two tasks with identical titles produce identical slugs. The second write fails (refuse-to-overwrite). The LLM is expected to title tasks uniquely; if it doesn't, the run ends `error` — operator fixes the task list and re-runs with a fresh work-item directory.
- **Operator sends `implementation-complete` before the run reaches the implementation stage.** Signal is persisted but the loop is in an earlier stage — `supervisor.wake` is a no-op if the step awaiting wake isn't present. The signal is consumed (idempotent) by the time the run actually reaches implementation: at entry, the runtime checks for existing unconsumed signals for the current task and immediately advances. Documented as "signal preload" behavior.
- **Operator sends `implementation-complete` for a task not yet scheduled** (e.g., `task_id=T-003` while on `T-001`). Returns `404` — the signal endpoint consults the run's memory to verify the task exists and is `pending` or `in_progress`.
- **`git diff` produces output > 64 KB.** Truncated; trailing `\n...<truncated N bytes>...\n` marker appended. The policy sees a clearly marked truncation; review quality on huge diffs is already pathological and operators should split the task.
- **`git` binary missing** (Docker image without git, or not on PATH). `review_implementation`'s upstream `git diff` call fails with `FileNotFoundError`, surfaced as `PolicyError("git not available")`, run ends `error`. README's prerequisites call this out.
- **Working tree dirty at review time** (operator has uncommitted changes in files outside the task's scope). Diff scope is narrowed to files tracked in memory per task, so stray changes elsewhere don't pollute the review. A stray change *inside* the task's declared files is included in the diff — the review sees it and the policy can flag it.
- **Brief's Status was manually edited to `Completed` mid-run** (operator confused themselves). The `close_work_item` tool's precondition check fails (Status != `In Progress`), run ends `error`, the brief is untouched.
- **Signal endpoint receives a POST while the runtime loop is mid-`await` on a DB session.** The operator-signal handler opens its own session (per existing convention); there is no shared session between the handler and the runtime loop. The `supervisor.wake` call is an in-memory event set — no DB contention.
- **Run is `cancel`ed** while suspended at `implementation`. The supervisor cancels the loop task; the pending `RunSignal` (if any arrives later) is persisted but triggers no wake — the run is terminal. `orchestrator runs trace` still shows the signal row for forensics.
- **Very long task list** (e.g., 50 tasks). Each task is a full iteration: one plan policy call, one pause, one review policy call (or more with corrections). The run-level `max_steps` budget must be sized accordingly — default is 50 per FEAT-002; the lifecycle agent's YAML declares `default_budget.max_steps: 300` to cover a reasonable ceiling. Budget-exceeded terminates the run normally (`stop_reason=budget_exceeded`, FEAT-002 contract).
- **Partial write crash** (process dies while `generate_tasks` is mid-rename). Temp-file-rename is atomic; either the final file exists complete, or it doesn't exist at all. A zombie run with no tasks file is fine to re-run (per-run scope; the tools' refuse-to-overwrite check passes).

---

## 10. Constraints

- MUST NOT spawn `claude` or any other AI-agent subprocess in v1. Implementation is operator-driven; FEAT-006 is the home for headless execution.
- MUST NOT modify `.ai-framework/prompts/*.md`. The lifecycle agent reads them verbatim; prompt evolution is a separate concern.
- MUST NOT introduce new dependencies. `PyYAML` is already present (FEAT-002 loader); `subprocess` is stdlib; `git` is a system binary.
- MUST respect AD-3: composition integrity. A stub-policy run must complete and produce placeholder artifacts, even if content is trivial. Proven by AC-2.
- MUST respect AD-4: per-run memory only. The `LifecycleMemory` shape is explicitly scoped to one run.
- MUST respect AD-5 v1: JSONL-first. Every new trace entry (policy calls at each stage, operator signals, correction attempts) lands in the existing JSONL writer. No new trace backend.
- MUST respect AD-6: eat our own dog food. The proof is a real work item in *this* repo, driven end-to-end.
- MUST preserve the adapter-thin rule: tool implementations in `modules/ai/tools/lifecycle/` delegate to service-layer functions. `subprocess` is quarantined to `tools/lifecycle/git.py`; no other module imports it.
- MUST NOT weaken existing tests. Every FEAT-001/002/003/004 test stays green with no modification. The extended `tests/test_adapters_are_thin.py` adds forbidden-import rules; it does not relax existing ones.
- MUST NOT leak the work item's content (or git diff content) into logs at INFO or lower. Traces persist the data by design; logs summarize (e.g., "generated tasks for IMP-002: 4 tasks, 1.2 KB written").
- MUST NOT assume a specific shell. The `git diff` invocation uses `subprocess.run([...], shell=False)` with an argv list.
- Work ships as a stack of PRs: (1) `AgentDefinition.policy.system_prompts` field + loader + validation; (2) the 7 lifecycle tools (one PR per 2–3 tools, each with unit tests); (3) `POST /runs/{id}/signals` endpoint + `RunSignal` model + migration; (4) CLI `orchestrator tasks mark-implemented`; (5) the `agents/lifecycle-agent@0.1.0.yaml` + integration tests; (6) the self-hosted proof (IMP-002 work item, dedicated branch, replayed end-to-end); (7) docs + changelog sweep. Each PR green on `pyright`, `ruff`, and the full pytest suite.

---

## 11. Motivation and Priority Justification

**Motivation:** Every feature before this one builds infrastructure that is only useful if some agent uses it. FEAT-002 built a runtime loop with no real agent in it. FEAT-003 plugged in a real LLM that made decisions for no real product. FEAT-004 shipped an observability tool for runs that weren't yet driving anything consequential. FEAT-005 is the feature that retires the "and then…" in every prior brief. It is also the only feature in the v1 plan whose *absence* leaves the stakeholder's primary success metric at 0. Philosophy #5 — "The first real consumer of the orchestrator is the orchestrator's own delivery loop" — is either demonstrated here or it becomes a broken promise.

**Impact if delayed:** Every future agent (code review bot, migration agent, scaffolding agent — the "consumers of the orchestrator" named in the stakeholder's out-of-scope list) assumes a working lifecycle agent exists as both template and precedent. Delay pushes all downstream features back and keeps the "can this orchestrator actually drive features?" question open. The v1 scope ships unfinished without FEAT-005; every other FEAT ships *infrastructure for* this one.

**Dependencies on this feature:** 
- FEAT-006 (headless Claude Code executor — spawns a subprocess to implement a task without operator involvement) layers directly on FEAT-005's pause/resume contract.
- Any future domain-specific agent (code reviewer, migration agent) reuses the YAML schema, the tool registry, and the operator-signal pattern landed here.
- The `stakeholder-definition.md` Success Metric #1 flips to "Met" in this feature's final PR.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` — the tech lead who already drives the ia-framework loop by hand and wants the orchestrator to run it for them, without losing rigor. |
| **Stakeholder Scope Item** | Primary: "An **end-to-end lifecycle agent** that drives the ia-framework loop: feature creation → task generation → task assignment → plan creation → implementation → review → corrections → closure." Also touches: "A **serialization schema** for agent definitions (YAML/JSON)" (extends FEAT-002's loader). Also touches: "**Observability hooks**" (operator signals and correction attempts become new trace kinds). |
| **Success Metric** | Directly targets: "Self-hosted feature delivery" (≥1 feature shipped end-to-end — proved by IMP-002). "Lifecycle stage automation coverage" (8/8 stages executable; implementation is operator-signaled in v1, which still counts per the metric's wording of "executable without manual routing"). "Composition integrity" (AC-2 proves stub-policy run). "Time-to-task-list from brief" (first measurable instance — the timer starts here). |
| **Related Work Items** | Predecessors: FEAT-001 (skeleton), FEAT-002 (runtime loop), FEAT-003 (Anthropic provider), FEAT-004 (trace streaming) — all must be Completed before this feature's integration tests can pass. New child work item: IMP-002-lifecycle-proof (the proof target) — authored as part of this feature's delivery. Successors: FEAT-006 (headless executor — removes the implementation-stage pause), future domain-specific agents. |

---

## 13. Usage Notes for AI Task Generation

When generating tasks from this Feature Brief:

1. **Stage the work in the PR order stated in Section 10.** Each stage ends on a green CI and a working repo — no half-wired merges. Tasks that span stages (e.g., "add a tool that depends on the signal endpoint") must be split so the dependent stage's PR lands after the prerequisite.
2. **Every tool is its own task** with unit tests. The 7 lifecycle tools (`load_work_item`, `generate_tasks`, `assign_task`, `generate_plan`, `wait_for_implementation`, `review_implementation`, `close_work_item`) each get a dedicated task; bundling tools produces reviewer fatigue.
3. **Workflow classification per task:**
   - Tools that only write files (and their tests) → `standard`.
   - The signal endpoint + CLI → `standard` (no new UI surface beyond a CLI flag).
   - The lifecycle-agent YAML + end-to-end integration test → `investigation-first` (the task should document the exact `system_prompts` mapping, the exact `flow.transitions` edges, and the chosen `max_steps` ceiling before code lands).
   - The self-hosted proof (IMP-002) → `investigation-first` (pick the trivial-but-real scope for the proof target first, then drive it).
4. **Update `docs/data-model.md` before the migration PR** — the data model is the contract per CLAUDE.md; the migration follows it.
5. **Update `docs/api-spec.md` before the endpoint PR** — the API contract is the source of truth for request/response shapes.
6. **Reuse `.ai-framework/prompts/*.md` verbatim.** Every task that involves wiring a stage's policy prompt must reference the existing file by path, never re-author the prompt.
7. **No task may modify `carestechs-flow-engine`.** AD-1. The only seam is the engine's HTTP API.
8. **Include the Feature Brief ID** (FEAT-005) in the task generation output summary for cross-referencing.
