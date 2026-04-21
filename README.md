# carestechs-agent-orchestrator

Agent-driven orchestration layer on top of [`carestechs-flow-engine`](https://github.com/carestechs/carestechs-flow-engine). Drives the ia-framework feature lifecycle — brief → tasks → plans → implementation → review → closure — as an agent loop, with the flow engine as a passive HTTP executor and webhook-reported progress.

## Start Here

- **Architecture & decisions:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Code conventions & commands:** [`CLAUDE.md`](CLAUDE.md)
- **Work items:** [`docs/work-items/`](docs/work-items/)
- **Task breakdowns:** [`tasks/`](tasks/) · **Implementation plans:** [`plans/`](plans/)

## Getting Started

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (Astral's Python package manager)
- Docker + Docker Compose (for local Postgres)

### Setup

```bash
# 1. Install dependencies into a local venv.
uv sync

# 2. Start backing services (Postgres on :5432 with a named volume).
docker compose up -d

# 3. Copy the env template and fill in secrets.
cp .env.example .env
# edit .env — at minimum set ORCHESTRATOR_API_KEY and ENGINE_WEBHOOK_SECRET.

# 4. Apply database migrations.
uv run alembic upgrade head

# 5. Verify the setup.
uv run orchestrator doctor
```

`doctor` should end with every check green. If any `✗` appears, fix it before moving on.

### Running

```bash
# Start the FastAPI service (webhooks + control plane) on :8000.
uv run orchestrator serve --reload

# In another shell — the control plane is behind Bearer auth.
curl -H "Authorization: Bearer $ORCHESTRATOR_API_KEY" http://localhost:8000/api/v1/runs
```

OpenAPI docs render at <http://localhost:8000/docs>.

### Using Anthropic

The default provider is a deterministic stub — no network, no cost. To drive
runs with a real Claude policy:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic
uv run orchestrator doctor   # validates key shape; no API call
```

Optional knobs: `LLM_MODEL` (default `claude-opus-4-7`), `ANTHROPIC_MAX_TOKENS`
(default 4096), `ANTHROPIC_TIMEOUT_SECONDS` (default 60). A live contract test
lives at `tests/contract/test_anthropic_provider_contract.py`; opt in with
`uv run pytest --run-live tests/contract/`.

### First Run

Drop an agent definition into `AGENTS_DIR` and drive it with the built-in stub
policy — no LLM SDK needed:

```bash
# 1. Make a local agents dir and copy the sample linear agent.
mkdir -p agents
cp tests/fixtures/agents/sample-linear.yaml agents/sample-linear@1.0.yaml

# 2. Start the service in one terminal.
uv run orchestrator serve

# 3. In a second terminal, start a run and block until it terminates.
uv run orchestrator run sample-linear@1.0 \
  --intake brief="Ship FEAT-003 by Friday" \
  --wait
# Prints the run summary (id, status, stop reason) and exits 0 on `completed`.

# 4. Inspect what happened (the --wait output printed the run id).
uv run orchestrator runs show <run-id>
uv run orchestrator runs policy <run-id>

# 5. Stream the live trace over HTTP (`--follow` tails through terminal state).
uv run orchestrator runs trace <run-id> --follow
# Or dump it once (`--json` forwards raw NDJSON; suitable for `jq`).
uv run orchestrator runs trace <run-id> --json | jq
```

The sample agent runs a 3-node linear flow (`analyze_brief → draft_plan →
review_plan`) driven by a deterministic stub policy — no LLM is invoked. See
[`docs/work-items/FEAT-002-runtime-loop.md`](docs/work-items/FEAT-002-runtime-loop.md)
for the runtime loop's architecture.

### Self-Hosted Feature Delivery

The orchestrator ships with a concrete lifecycle agent that drives the
ia-framework's 8-stage loop (intake → task generation → task assignment →
plan creation → implementation → review → corrections → closure) against
any FEAT / BUG / IMP work item in this repo.

```bash
# 1. Drop a filled-in work item at docs/work-items/FEAT-XXX.md.

# 2. Start the run (Anthropic recommended for real decisions).
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic
uv run orchestrator run lifecycle-agent@0.1.0 \
  --intake workItemPath=docs/work-items/FEAT-XXX.md

# 3. Tail the trace. When the run pauses at `wait_for_implementation`,
#    read the plan the agent wrote at plans/plan-T-001-*.md and land the
#    change (Claude Code, by hand, whatever — implementation stage is
#    operator-driven in v1).
uv run orchestrator runs trace <run-id> --follow

# 4. Signal back; the agent reviews and closes the work item.
uv run orchestrator tasks mark-implemented T-001 --run-id <run-id> \
  --commit-sha $(git rev-parse HEAD)
```

See [`agents/lifecycle-agent@0.1.0.yaml`](agents/lifecycle-agent@0.1.0.yaml)
for the flow definition and
[`docs/work-items/FEAT-005-lifecycle-agent.md`](docs/work-items/FEAT-005-lifecycle-agent.md)
for the design.

### GitHub Merge-Gating (FEAT-007)

When credentials are configured, the orchestrator posts an
`orchestrator/impl-review` GitHub check for every task PR and flips it to
`success` or `failure` on review approve/reject. Paired with a branch
protection rule requiring that check, reviewer approval becomes the merge
gate — no second click on GitHub.

**Credentials.** Configure exactly one strategy; the factory resolves
`App > PAT > Noop`:

```bash
# Single-maintainer setups — simplest.
export GITHUB_PAT=ghp_...            # classic PAT with `repo` scope

# Multi-user orgs — GitHub App (Checks: write, Pull requests: read).
export GITHUB_APP_ID=12345
export GITHUB_PRIVATE_KEY=@file:/absolute/path/to/app.pem
```

`orchestrator doctor` prints the resolved strategy
(`github_checks: Merge-gating: PAT` / `App (id …)` / `no-op`). No
`GITHUB_REPO` is needed — the target repo is derived per task from the
PR URL, so one credential covers every repo it has `repo` scope on.

**Branch protection.** Under *GitHub → Settings → Rules → Rulesets* on
`main`, enable:

- ✅ *Require a pull request before merging*
- ✅ *Require status checks to pass* → add **`orchestrator/impl-review`**
- ✅ *Block force pushes*
- ✅ *Restrict deletions*

Leave the rest off (linear history, merge queue, deployments, signed
commits, code scanning) — they're orthogonal to the gate.

> **Gotcha:** the `orchestrator/impl-review` check only appears in the
> status-check picker *after* the orchestrator has posted it at least
> once. Open a throwaway PR, run it through `submit_implementation` +
> `approve_review`, then add the check to the ruleset.

**Known limits.**

- *Force-merge bypass.* Admins with bypass permission can merge without
  the gate. FEAT-006's audit trail records the approval, but FEAT-007
  cannot prevent the merge itself.
- *PR reopened after approval.* The check is not reset — if a reviewer
  already approved, the old `success` check stays.
- *Transient GitHub 5xx.* The state machine always commits first; a
  failed check call logs a structured warning and moves on. Operators
  see a trace entry; the PR's check may be stale.

See [`docs/work-items/FEAT-007-github-merge-gating.md`](docs/work-items/FEAT-007-github-merge-gating.md)
for the full design + acceptance criteria.

### Tests

```bash
# Fast unit tests.
uv run pytest tests/modules tests/core

# Full suite (requires the Postgres container to be up).
uv run pytest

# Type check + lint.
uv run pyright
uv run ruff check .
```

The test harness creates a unique `orchestrator_test_<uuid>` database per session and drops it at the end — safe to run alongside dev.

Opt-in live smoke tests (off by default):

```bash
# FEAT-007: verify PAT scope + GitHub API shape against a scratch PR.
export GITHUB_PAT=ghp_...
export GITHUB_SMOKE_PR_URL=https://github.com/you/scratch/pull/1
uv run pytest --run-live tests/contract/test_github_checks_live.py
```

## Project Layout

```
src/app/           — FastAPI service + Typer CLI (one codebase, two entry points)
src/app/modules/ai — the only feature module in v1 (routes, service, models, tools)
src/app/migrations — Alembic
docs/              — Architecture, API spec, data model, work items
tests/             — pytest suite (unit + integration + contract)
```

See [`CLAUDE.md`](CLAUDE.md) → "Key Directories" for the full tree and rationale.

## Status

v0.2.0 — FEAT-002 runtime loop complete: stub-policy end-to-end, webhook reconciliation, per-run memory, JSONL traces, and the full control-plane surface (less trace streaming). See `docs/work-items/FEAT-002-runtime-loop.md`.
