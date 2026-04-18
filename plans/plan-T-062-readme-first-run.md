# Implementation Plan: T-062 — README extension (first real run walkthrough)

## Task Reference
- **Task ID:** T-062
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-046, T-054

## Overview
Extend README with a compact "First Run" section so a fresh-clone reader can see the orchestrator do something in under 2 minutes. Stays under the 150-line README cap.

## Steps

### 1. Modify `README.md`

Add a new section after "Running":

```markdown
### First Run

Drop an agent definition into `AGENTS_DIR`, then run it:

```bash
# 1. Make a local agents dir and copy the sample linear agent.
mkdir -p agents
cp tests/fixtures/agents/sample-linear.yaml agents/

# 2. Start a run (deterministic stub policy — no LLM needed).
uv run orchestrator run sample-linear@1.0 \
  --intake brief="Ship FEAT-003 by Friday" \
  --wait

# 3. Inspect what happened.
uv run orchestrator runs show <run-id>
uv run orchestrator runs policy <run-id>

# 4. Read the raw trace.
cat .trace/<run-id>.jsonl | jq
```

The sample agent runs a 3-node linear flow driven by the deterministic stub
policy — no LLM SDK is invoked. See `docs/work-items/FEAT-002-runtime-loop.md`
for the runtime loop's architecture.
```

### 2. Verify size

- Run `wc -l README.md` → ≤ 150 lines.
- If over, trim other sections (e.g., shrink "Project Layout" to link out).

### 3. Verify the commands actually work

Manual smoke test on a fresh clone:
1. `uv sync`.
2. `docker compose up -d`.
3. `cp .env.example .env` + fill in secrets.
4. `uv run alembic upgrade head`.
5. `mkdir -p agents && cp tests/fixtures/agents/sample-linear.yaml agents/`.
6. Run in one terminal: `uv run orchestrator serve`.
7. In another terminal, run the quoted commands exactly.
8. Confirm `status=completed`, `stop_reason=done_node`, 3 policy calls, and a populated JSONL file.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `README.md` | Modify | Add "First Run" section. |

## Edge Cases & Risks
- The quoted commands use `<run-id>` as a placeholder — readers might copy-paste literally. Document that the `run --wait` output prints the run id as the last line.
- `jq` is a suggested-but-not-required dependency — if a user doesn't have it, `cat` alone is fine. Document that.

## Acceptance Verification
- [ ] README ≤ 150 lines.
- [ ] Commands run verbatim on a fresh clone (after setup) and produce a completed run.
- [ ] Section links to FEAT-002 brief for deeper reading.
- [ ] Final `git diff README.md` reviewed for clarity + no duplicate content with CLAUDE.md / ARCHITECTURE.md.
