# Implementation Plan: T-171 — Startup exhaustiveness validation

## Task Reference
- **Task ID:** T-171
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-3. Silent transitions are the failure mode that would undo FEAT-008's value over time. Exhaustiveness check at startup is the correctness guard that keeps the "effectors are the product" rule from decaying.

## Overview
On app startup, iterate all declared transitions in `work_item_workflow` + `task_workflow` and assert each has a registered effector **or** a `@no_effector("reason")` exemption claim. Missing coverage fails startup with a clear error listing the uncovered transitions.

## Implementation Steps

### Step 1: Enumerate declared transitions
**File:** `src/app/modules/ai/lifecycle/effectors/validation.py`
**Action:** Create

Read transition declarations from `declarations.py` — they already exist as `work_item_workflow` + `task_workflow` constants with explicit from/to state pairs.

```python
from __future__ import annotations
from dataclasses import dataclass
from app.modules.ai.lifecycle.declarations import (
    work_item_workflow, task_workflow,
)
from app.modules.ai.lifecycle.effectors.registry import (
    EffectorRegistry, build_transition_key,
)
from app.modules.ai.lifecycle.effectors.base import no_effector_exemptions


@dataclass(frozen=True)
class ValidationResult:
    covered: list[str]
    exempt: list[tuple[str, str]]   # (transition_key, reason)
    uncovered: list[str]


def validate_effector_coverage(registry: EffectorRegistry) -> ValidationResult:
    """Cross-check declared transitions against registered effectors.

    Every declared transition must map to at least one registered effector
    OR appear in the ``no_effector`` exemption registry with a reason.
    """
    covered: list[str] = []
    exempt: list[tuple[str, str]] = []
    uncovered: list[str] = []

    transitions = _enumerate_transitions()
    for key in transitions:
        if registry.has(key):
            covered.append(key)
        elif key in no_effector_exemptions:
            exempt.append((key, no_effector_exemptions[key]))
        else:
            uncovered.append(key)

    return ValidationResult(
        covered=covered, exempt=exempt, uncovered=uncovered
    )


def _enumerate_transitions() -> list[str]:
    """Build the full set of expected transition keys from declarations."""
    keys: list[str] = []
    for transition in work_item_workflow.transitions:
        keys.append(build_transition_key("work_item", transition.from_state, transition.to_state))
        # Also include entry-only keys so entry-triggered effectors are validated
        keys.append(build_transition_key("work_item", None, transition.to_state))
    for transition in task_workflow.transitions:
        keys.append(build_transition_key("task", transition.from_state, transition.to_state))
        keys.append(build_transition_key("task", None, transition.to_state))
    # Deduplicate
    return sorted(set(keys))
```

Two-key-per-transition (with and without `from_state`) means registration can be state-specific *or* entry-agnostic, whichever fits the effector. Only one of the two keys needs coverage for the transition to be valid.

Revised: count a transition as covered if *either* its state-transition key or its entry-state key has coverage. The simple "per-key" check above is too strict. Rework:

```python
def _transition_is_covered(
    registry: EffectorRegistry,
    entity: str, from_state: str, to_state: str,
) -> str | None:
    """Return ``"covered" | "exempt" | None (uncovered)``."""
    specific = build_transition_key(entity, from_state, to_state)
    entry_only = build_transition_key(entity, None, to_state)
    if registry.has(specific) or registry.has(entry_only):
        return "covered"
    if specific in no_effector_exemptions or entry_only in no_effector_exemptions:
        return "exempt"
    return None
```

Use this per-transition check in the outer loop.

### Step 2: `no_effector` decorator implementation
**File:** `src/app/modules/ai/lifecycle/effectors/base.py`
**Action:** Modify

T-161 declared the decorator shape. Implement it as a module-level registry:

```python
no_effector_exemptions: dict[str, str] = {}


def no_effector(transition_key: str, reason: str) -> None:
    """Mark a transition as intentionally effector-free.

    Called at module load (not as a decorator, despite the name) — keep
    it simple, one line per exemption in the bootstrap module.

        no_effector("task:deferred->deferred", "terminal state, no outbound effect")
    """
    if not reason or len(reason.strip()) < 10:
        raise ValueError(f"no_effector reason must be descriptive; got {reason!r}")
    no_effector_exemptions[transition_key] = reason
```

Simpler than a real decorator — caller just invokes once per exemption at bootstrap time. Ten-character minimum prevents drive-by "idk" reasons.

### Step 3: Wire validation into lifespan
**File:** `src/app/lifespan.py`
**Action:** Modify

After `register_all_effectors(...)`:

```python
from app.modules.ai.lifecycle.effectors.validation import validate_effector_coverage

result = validate_effector_coverage(app.state.effector_registry)
if result.uncovered:
    raise RuntimeError(
        f"Effector coverage incomplete: {len(result.uncovered)} transition(s) "
        f"have no registered effector and no `no_effector` exemption:\n"
        + "\n".join(f"  - {k}" for k in result.uncovered)
        + "\n\nRegister an effector in `effectors/bootstrap.py` or add a "
        "`no_effector(<key>, <reason>)` call with a justification."
    )

logger.info(
    "effector coverage: %d covered, %d exempt",
    len(result.covered), len(result.exempt),
)
for key, reason in result.exempt:
    logger.debug("effector exemption: %s — %s", key, reason)
```

### Step 4: Populate known exemptions
**File:** `src/app/modules/ai/lifecycle/effectors/bootstrap.py`
**Action:** Modify

Not every transition has an obvious outbound effect. Reasonable exemptions at T-171 time:

- Terminal states (`closed`, `deferred`, `done`): no outbound effector unless you count a "work completed" notification. V1: exempt, reason "terminal state; post-completion effects belong to later FEAT."
- Lock / unlock: today these are administrative; no external notification. Exempt with reason.
- Approve-task / approve-plan / approve-assignment: the approval itself is the ingress; the next transition's effector (e.g., `request_assignment` on entering `assigning`) is the outbound effect. Exempt.

Be liberal with exemptions in v1 — the point is forcing a deliberate choice, not covering every transition with a real effector on day one. Document each exemption's reason clearly so future reviewers can revisit.

### Step 5: Unit tests
**File:** `tests/modules/ai/lifecycle/effectors/test_validation.py`
**Action:** Create

Cases:
- **All covered.** Mock registry has every transition. `uncovered=[]`.
- **Some exempt.** Exemption claimed via `no_effector`. Exempt count matches.
- **One uncovered.** `result.uncovered` contains that key.
- **Invalid reason.** `no_effector("k", "")` raises.
- **Reason too short.** `no_effector("k", "TODO")` raises.
- **Both state-specific and entry-only registered.** Counted as covered once, not twice.

### Step 6: Lifespan integration test
**File:** `tests/integration/test_lifespan_effector_validation.py`
**Action:** Create

- Start app under test with all effectors registered + exemptions declared → lifespan completes.
- Start app with one effector deliberately unregistered → lifespan raises at startup. Assert the error message lists the uncovered transition.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/effectors/validation.py` | Create | `validate_effector_coverage`. |
| `src/app/modules/ai/lifecycle/effectors/base.py` | Modify | `no_effector` registry. |
| `src/app/modules/ai/lifecycle/effectors/bootstrap.py` | Modify | Populate exemptions. |
| `src/app/lifespan.py` | Modify | Run validation at startup. |
| `tests/modules/ai/lifecycle/effectors/test_validation.py` | Create | Unit tests. |
| `tests/integration/test_lifespan_effector_validation.py` | Create | Startup integration. |

## Edge Cases & Risks
- **New transitions added without effector.** This is exactly what we want — startup fails, developer either registers or exempts. The "fix it to unblock the build" pressure enforces the rule. Reviewers should scrutinize exemptions more heavily than registrations over time.
- **Registry introspection.** `EffectorRegistry.has(key)` is a new method. Add to T-161's registry if missing — trivial `key in self._effectors and self._effectors[key]`.
- **Keys from declarations might not match runtime keys.** `build_transition_key` needs to be bit-identical between declarations-enumeration and effector registration. Centralized helper (already in T-161) prevents drift. Test that registration + validation see the same key shape.
- **Module-load ordering.** `no_effector_exemptions` is a module global populated at bootstrap time. If bootstrap isn't called before validation, the dict is empty and everything looks uncovered. Ordering is guaranteed by lifespan (step 3), but write a regression test: "validation before bootstrap raises" — then "after bootstrap passes."
- **Exemption reasons decay.** An exemption made legitimately today might become illegitimate once the transition needs a real effector. No automated way to detect — rely on code review. Prepend a date to the reason? Probably overkill; skip unless we see drift in practice.

## Acceptance Verification
- [ ] `validate_effector_coverage` returns `ValidationResult` with correct buckets.
- [ ] Lifespan startup raises when uncovered transitions exist; error message lists them.
- [ ] `no_effector` rejects empty / too-short reasons.
- [ ] All current transitions either have an effector or an exemption — lifespan passes on main.
- [ ] Unit + integration tests green.
- [ ] `uv run pyright`, `ruff` clean.
