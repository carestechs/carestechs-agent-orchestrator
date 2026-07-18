# Feature Brief: FEAT-020 — Validator Integration in Human Lifecycle Flow

> **Purpose**: Run `validate-tasks.py` and `validate-specs.py` from `carestechs-ia-framework/tools/` at the right points in the lifecycle flow and pass their output as structured context to the human reviewer. Right now every review checkpoint asks the operator to judge quality with no mechanical evidence — this feature closes that gap by making the orchestrator produce the evidence before the reviewer sees the node.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-020 |
| **Name** | Validator integration in human lifecycle flow |
| **Type** | Feature |
| **Status** | Proposed |
| **Priority** | High |
| **Proposed By** | Engineering (2026-07-18) |
| **Date Created** | 2026-07-18 |

---

## 2. Motivation

The `carestechs-ia-framework` ships two compliance tools:

- **`validate-tasks.py`** — checks a task list for schema conformance, AC coverage, dependency cycles, and complexity bounds. The framework documentation describes this as "the single biggest measured compliance lever."
- **`validate-specs.py`** — checks an implementation against the work-item's acceptance criteria, looking for untested ACs, missing migration entries, and doc-update gaps.

Neither tool is called anywhere in `lifecycle-agent@0.6.0-human`. The flow has two human review checkpoints (`confirm_task_review`, `human_review_implementation`) and a pre-closure gate (`confirm_docs_update`), but none of them receives validator output. The reviewer sees:

- At `confirm_task_review`: the raw task list JSON that the author just typed.
- At `human_review_implementation`: a free-text summary the developer typed at `implementation-complete` time.
- At `confirm_docs_update`: a summary of completed task IDs.

Reviews happen, but they are uninformed. An operator approving at `confirm_task_review` has no way to know whether T-002 actually covers AC-3, or whether the `dependencies` field creates a cycle. An operator approving at `human_review_implementation` has no way to know whether the test suite passed.

This feature adds three new executor nodes that run the validators and surface their structured output as additional `nodeInputs` fields at the points where a human review follows.

---

## 3. Scope

### In scope

1. **`run_validate_tasks` node** — runs `validate-tasks.py` against the in-memory task list immediately after `confirm_tasks` (before `confirm_task_review`). Adds `validatorResult` to `confirm_task_review`'s `nodeInputs`.
2. **`run_tests` node** — runs the project's test suite (`uv run pytest`) after `submit_implementation` (before `human_review_implementation`). Adds `testResult` to `human_review_implementation`'s `nodeInputs`. `validate-specs.py` is **not** run here — it requires a full repo checkout and is a repo-level check, not a per-task one (see below).
3. **`run_validate_specs_strict` node** — runs `validate-specs.py --strict` against the project's `docs/` tree after `confirm_docs_update`'s approval. This is the repo-level spec completeness gate — it checks that all spec shards, stamps, and index files are consistent. If validation fails, the flow loops back to `confirm_docs_update` with the failure output surfaced as `priorFeedback` (operator must fix the docs gap and re-approve). Hard block, not a warning. Requires the project repo to be accessible on the orchestrator host (see OQ-4).

### Out of scope

- Auto-fixing validator failures (the orchestrator never edits files).
- Running validators as a pre-condition that silently skips the checkpoint on pass — the checkpoint always fires; the validator output is additional context, not a gate that bypasses the human.
- Validator changes or new validator scripts — this feature calls the existing tools as-is.

---

## 4. Proposed Flow Change

```
Before FEAT-020:

  confirm_tasks           ← operator authors task list
    ↓
  confirm_task_review     ← reviewer sees: task list JSON only


After FEAT-020:

  confirm_tasks           ← operator authors task list
    ↓
  run_validate_tasks      ← LocalExecutor: runs validate-tasks.py, writes result to memory
    ↓
  confirm_task_review     ← reviewer sees: task list JSON + validatorResult


Before FEAT-020:

  submit_implementation   ← developer marks complete
    ↓
  human_review_implementation  ← reviewer sees: implementation summary only


After FEAT-020:

  submit_implementation
    ↓
  run_tests               ← LocalExecutor: runs uv run pytest (per-task)
    ↓
  human_review_implementation  ← reviewer sees: summary + testResult


Before FEAT-020:

  confirm_docs_update [approve]
    ↓
  log_run_completed


After FEAT-020:

  confirm_docs_update [approve]
    ↓
  run_validate_specs_strict  ← LocalExecutor: validate-specs.py --strict
    ↓ [pass]               ↓ [fail → loop back]
  log_run_completed     confirm_docs_update  (with failure as priorFeedback)
```

---

## 5. Acceptance Criteria

1. A new `run_validate_tasks` node runs `validate-tasks.py` after `confirm_tasks`. Its result is stored in `RunMemory` under a `validatorResults.tasks` key.
2. `confirm_task_review`'s `nodeInputs` includes a `validatorResult` field populated from `validatorResults.tasks` in memory. If the validator was not run (e.g., tool not found on PATH), the field is `null` and the run does not fail.
3. A new `run_tests` node runs `uv run pytest` after `submit_implementation`. Results are stored in `testResults[task_id]`.
4. `human_review_implementation`'s `nodeInputs` includes a `testResult` field from `testResults[current_task_id]` in memory. It is `null` if the test runner was unavailable or timed out.
5. A new `run_validate_specs_strict` node runs after `confirm_docs_update` approval. If `validate-specs.py --strict` exits non-zero, the node writes the failure output to a `rejections["confirm_docs_update"]` patch and the flow loops back to `confirm_docs_update` with `priorFeedback` set to the failure text.
6. All three validator nodes are `LocalExecutor` bindings — they run in-process (via `asyncio.create_subprocess_exec`) and return structured results.
7. If any validator binary is not found (e.g., `carestechs-ia-framework` is not checked out alongside the orchestrator), the node returns `{"skipped": True, "reason": "validator not found"}` and the flow continues. Validator absence is never a run-terminating error.
8. `lifecycle-agent@0.6.0-human.yaml` includes the three new nodes and their transitions.
9. `register_lifecycle_v06_human` in `executors/bootstrap.py` registers the three new `LocalExecutor` bindings.
10. Unit tests cover: (a) the memory-patch builders for `validatorResult` and `testResult`; (b) the skip-on-not-found path; (c) the loop-back path when `--strict` fails.
11. `docs/data-model.md` updated with `validatorResults` and `testResults` memory keys.

---

## 6. Technical Notes

### Validator invocation

Both tools live at `tools/validate-tasks.py` and `tools/validate-specs.py` inside `carestechs-ia-framework`. The orchestrator does not own these scripts. The executor should look for them via a config key `IA_FRAMEWORK_TOOLS_PATH` (default: `../carestechs-ia-framework/tools`). If the path does not exist, the node skips non-fatally (AC-7).

**`validate-tasks.py` (answered):** Takes a markdown file as a positional argument — `tasks/<WI_ID>-tasks.md` at the canonical path. It cannot accept JSON from stdin or a run ID. This means `run_validate_tasks` must render the in-memory task list to a markdown file before calling the tool. `commit_tasks` already renders and commits exactly this file when `GITHUB_PAT` is configured; without a PAT the file never lands on disk. Implementation options:

- **Preferred:** `run_validate_tasks` renders the task markdown to a temp path (e.g. `/tmp/{run_id}/tasks/{WI_ID}-tasks.md`) using the same `render_task_list_markdown` function that `commit_tasks` uses, then calls the validator against that temp path. No dependency on `GITHUB_PAT` or the artifact branch.
- **Alternative:** only run the validator when `commit_tasks` succeeds (i.e., the committed file exists at the artifact branch). Simpler but tightly coupled to PAT availability — the validator silently skips whenever commits are disabled.

The preferred option decouples validation from artifact commits, which is the right separation.

**`validate-specs.py` (answered):** Validates spec document structure — it requires the `docs/` tree with frontmattered spec shards, stamps, and index files from the project repo. It does not validate code itself and cannot work from a PR URL or commit SHA in memory. It only makes sense when the project repo is checked out locally on the orchestrator's host. This means `run_validate_specs` as a per-task post-submit check is not viable — the repo checkout is not currently wired up, and even if it were, the spec tree is static per run and doesn't change per task. `validate-specs.py` belongs only at the `run_validate_specs_strict` stage (before `confirm_docs_update`), as a repo-level pre-closure gate.

**Consequence for `run_validate_specs`:** the per-task node after `submit_implementation` should be tests only (`uv run pytest tests/` or a filtered subset), not `validate-specs.py`. Rename the node to `run_tests` to reflect this.

Invocation patterns:
```bash
# run_validate_tasks — render to temp, then validate
uv run python {tools_path}/validate-tasks.py /tmp/{run_id}/tasks/{WI_ID}-tasks.md

# run_tests — per-task test run (scope TBD, see OQ-3)
uv run pytest tests/ --tb=short -q

# run_validate_specs_strict — repo-level, requires local checkout
uv run python {tools_path}/validate-specs.py docs/ --strict
```

### Memory shape (new keys)

```
RunMemory.data = {
  "lifecycle.v1": { ... },       # unchanged
  "plans": { ... },              # unchanged
  "assignments": { ... },        # unchanged
  "validatorResults": {
    "tasks": {                   # written by run_validate_tasks
      "exit_code": 0,
      "output": "...",
      "passed": true
    },
    "specs": {                   # written by run_validate_specs_strict (repo-level, once)
      "exit_code": 0,
      "output": "...",
      "passed": true
    }
  },
  "testResults": {               # written by run_tests (per-task)
    "T-001": { "exit_code": 0, "output": "3 passed in 0.4s", "passed": true },
    "T-002": { "exit_code": 1, "output": "1 failed: test_cost_usd...", "passed": false }
  }
}
```

### Loop-back on `--strict` failure

The `run_validate_specs_strict` node's failure path should reuse the existing `_rejection_patch("confirm_docs_update", failure_output, current_memory)` helper so that `confirm_docs_update`'s `nodeInputs.priorFeedback` is populated on the next dispatch, exactly like an operator rejection.

### New agent YAML nodes

```yaml
nodes:
  - name: run_validate_tasks
    kind: local
  - name: run_tests
    kind: local
  - name: run_validate_specs_strict
    kind: local
```

The `confirm_task_review` intake builder must be updated to read `validatorResults.tasks` from memory and include it in `nodeInputs.validatorResult`. The `human_review_implementation` intake builder must be updated to read `testResults[current_task_id]` and include it in `nodeInputs.testResult`. `validate-specs.py` output is not surfaced at this step — it only appears via the `run_validate_specs_strict` loop-back.

---

## 7. Open Questions

1. ~~Does `validate-tasks.py` accept task data via stdin/flag, or does it read from the engine's API directly?~~ **Answered:** takes a markdown file at the canonical path. `run_validate_tasks` renders to a temp path using `render_task_list_markdown` — no dependency on PAT or artifact commits.

2. ~~Does `validate-specs.py` require the actual source code to be checked out?~~ **Answered:** requires the `docs/` tree from the project repo on disk. This is a repo-level check, not a per-task check. It only runs at `run_validate_specs_strict` (before `confirm_docs_update`). The orchestrator host must have the project repo checked out for this node to do anything other than skip. `IA_FRAMEWORK_TOOLS_PATH` plus a `LIFECYCLE_PROJECT_REPO_PATH` config key (or deriving from `codeSource.repo` + a local clone directory) is the remaining design decision. Until that is wired, `run_validate_specs_strict` skips non-fatally.

3. Should `run_tests` run the full `pytest` suite or filter to files touched by the current task? Filtering (via `--collect-only` + `codeSource.workBranch` diff) is more precise but adds implementation complexity. A full suite run is simpler and catches regressions but may be too slow for runs with many tasks (each task triggers its own `run_tests` dispatch). The timeout for `LocalExecutor` subprocess invocations is not currently bounded — this needs a cap before implementation (suggested: 5 minutes, configurable via `LIFECYCLE_TEST_TIMEOUT_SECONDS`).

4. `run_validate_specs_strict` requires the project repo to be checked out at a known local path. Is the orchestrator host always the same machine that has the working tree? In the Docker-compose deployment the orchestrator runs in a container — the project repo would need to be bind-mounted in. This mount is not in the current `docker-compose.yml` or `docker-compose.prod.yml`. Decide: (a) add the bind mount as an opt-in volume, or (b) skip `run_validate_specs_strict` non-fatally when the path is absent (same as OQ-2 fallback).
