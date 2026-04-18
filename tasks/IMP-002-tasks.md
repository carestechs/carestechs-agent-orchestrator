# IMP-002 — Lifecycle self-hosted delivery proof

## Tasks

- [ ] **TASK-001** · Review work item requirements and acceptance criteria
  Carefully read `docs/work-items/IMP-002-lifecycle-proof.md` to identify all stated goals, constraints, and definition-of-done criteria.

- [ ] **TASK-002** · Audit existing lifecycle agent codebase
  Locate and review all source files related to `lifecycle-agent@0.1.0`, including entry points, node definitions, policy handlers, and configuration schemas.

- [ ] **TASK-003** · Map the full agent run lifecycle (state machine)
  Document every node (`load_work_item`, `generate_tasks`, review nodes, correction nodes, termination) and the allowed transitions between them, verifying they match the policy rules enforced each turn.

- [ ] **TASK-004** · Validate tool/function contract coverage
  Confirm that every tool listed in the agent's function schema (`generate_tasks`, `terminate`, and any others) has a corresponding implementation and that parameter types, required fields, and side-effects are correctly specified.

- [ ] **TASK-005** · Stand up self-hosted runner environment
  Configure the self-hosted execution environment (runtime dependencies, secrets, environment variables, working directory layout) required to run the lifecycle agent end-to-end without external SaaS services.

- [ ] **TASK-006** · Author end-to-end proof harness
  Write a harness (script or test suite) that drives the agent through a complete run — intake → load → generate_tasks → (review/correction cycle) → terminate — using a representative synthetic work item.

- [ ] **TASK-007** · Execute proof run and capture evidence
  Run the harness against the self-hosted environment, capture logs, memory snapshots at each step, and tool-call traces as verifiable evidence artifacts.

- [ ] **TASK-008** · Verify memory consistency across steps
  Assert that `memory.tasks`, `memory.currentTaskId`, `memory.reviewHistory`, `memory.correctionAttempts`, and `memory.filesTouchedPerTask` are correctly mutated and persisted after each node completes.

- [ ] **TASK-009** · Validate task file output
  Confirm that `generate_tasks` writes a correctly formatted markdown file to `tasks/<work_item_id>-tasks.md` and that the content matches the in-memory `memory.tasks` array.

- [ ] **TASK-010** · Test error-path and correction-attempt handling
  Simulate node failures and out-of-order tool calls; verify the policy rejects illegal transitions and that `correctionAttempts` is incremented and bounded correctly.

- [ ] **TASK-011** · Document self-hosted delivery runbook
  Write a concise runbook (`docs/runbooks/IMP-002-self-hosted-delivery.md`) covering environment setup, harness invocation, expected outputs, and troubleshooting steps.

- [ ] **TASK-012** · Peer-review evidence artifacts and close work item
  Share logs, memory snapshots, and the runbook with a reviewer; address any feedback; mark IMP-002 as delivered once all acceptance criteria are signed off.
