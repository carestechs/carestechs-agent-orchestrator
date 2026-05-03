# Plan: T-270 — Swappable reviewer binding + stub-pass mode

> **Task source:** IMP-003 (`docs/work-items/IMP-003-swappable-reviewer-binding-and-stub-pass-mode.md`).
> **Workflow:** standard.

---

## Task definition

| Field | Value |
|-------|-------|
| **ID** | T-270 |
| **Title** | Add `LIFECYCLE_REVIEWER` env var; introduce `StubPassReviewerExecutor`; refactor `register_lifecycle_v03` to select reviewer binding from the setting |
| **Type** | Backend / Bootstrap |
| **Complexity** | S |
| **Workflow** | standard |
| **Description** | Today's `register_lifecycle_v03` hardcodes `LLMContentExecutor` as the binding for `review_implementation`. Replace with a small selector that reads a new `LIFECYCLE_REVIEWER` setting (default `llm-content`, alternate `stub-pass`, future-reserved `remote`). Add a `StubPassReviewerExecutor` — a `LocalExecutor` that writes the canonical `lifecycle.v1.reviewHistory` entry with `verdict=pass` and `feedback="stub-pass: smoke / CI shortcut"`. |
| **Files to Modify** | `src/app/config.py`, `src/app/modules/ai/executors/bootstrap.py`, `src/app/modules/ai/executors/stub_reviewer.py` (new), `tests/modules/ai/executors/test_stub_reviewer.py` (new), `tests/integration/test_lifecycle_v03_review_pass_path.py` (new), `CLAUDE.md`, `.env.example` (if present) |
| **Acceptance Criteria** | (1) `LIFECYCLE_REVIEWER=stub-pass` makes a deterministic smoke run reach `close_work_item` with `status=completed`. (2) `LIFECYCLE_REVIEWER` unset / `llm-content` is a no-op vs. today. (3) Unknown value refuses boot with `ConfigError`. (4) Stub `reviewHistory` shape matches the LLM path bit-for-bit (canonical namespace, camelCase, per-task `attempt` counter, BUG-010 contract preserved). (5) Lifespan boot logs the active reviewer binding at INFO. |
| **Dependencies** | None. PR #72 (BUG-010) merged. |

---

## Context summary

`bootstrap.py:726` is where today's `LLMContentExecutor` for `review_implementation` is registered:

```python
registry.register(
    agent_ref,
    "review_implementation",
    LLMContentExecutor(
        ref="llm:review_implementation",
        system_prompt=_load_prompt("review_implementation"),
        user_prompt_template=...,
        result_schema=ReviewImplementationResult,
        llm_provider=llm_provider,
        memory_patch_builder=_patch_review,
        prompt_context_loader=_load_review_context,
        session_factory=session_factory,
    ),
)
```

`_patch_review` (the BUG-010 builder) is the canonical `reviewHistory` writer. The stub must produce the same patch shape. Cleanest is for the stub to *call* `_patch_review` with a synthesized `result` dict — that way the canonical-shape contract has exactly one writer and the stub piggybacks on it.

`Settings` lives in `src/app/config.py` and uses `pydantic-settings`. New literal-typed field. The `Settings` instance is built once at boot.

---

## Implementation steps

### Step 1 — Settings field

`src/app/config.py`:

```python
LIFECYCLE_REVIEWER: Literal["llm-content", "stub-pass"] = "llm-content"
"""Which executor binding registers for ``review_implementation``.

* ``llm-content`` (default) — production binding; in-process LLM call
  via ``LLMContentExecutor`` (the FEAT-011 baseline).
* ``stub-pass`` — smoke / CI binding; ``StubPassReviewerExecutor``
  always returns ``verdict=pass``.  NEVER use in production.

Future ``remote`` value reserved for the external review service when
it ships; intentionally not in the literal yet (premature contract
freeze).
"""
```

Notes:
- Use `Literal[...]` so unknown values fail at `Settings()` construction with a clear pydantic error → translates to a boot-time refusal.
- DON'T add `remote` to the literal yet. When the real external service ships, that PR widens the literal.

### Step 2 — `StubPassReviewerExecutor`

New file `src/app/modules/ai/executors/stub_reviewer.py`:

```python
"""Stub-pass reviewer for smoke / CI (IMP-003 / T-270).

Synthesises ``verdict=pass`` for ``review_implementation`` so smoke
runs that lack real PR evidence can still reach ``close_work_item``.
NEVER use in production — every implementation gets rubber-stamped.

Wire-shape: a ``LocalExecutor`` whose handler builds the same dict
the LLM path would produce, then runs it through ``_patch_review``
to write the canonical ``lifecycle.v1.reviewHistory`` entry.  The
stub writes the same memory shape as the production reviewer; the
``feedback`` text is the only discriminator.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.models import RunMemory


_STUB_FEEDBACK = "stub-pass: smoke / CI shortcut"


def make_stub_pass_reviewer(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    patch_review_builder,  # MemoryPatchBuilder from llm_content
) -> LocalExecutor:
    """Build a ``LocalExecutor`` that always returns ``verdict=pass``.

    Reuses ``_patch_review`` (the BUG-010 canonical writer) so the
    stub can never drift from the LLM path's memory shape.
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        task_id = str(ctx.intake.get("taskId") or ctx.intake.get("task_id") or "")
        result_dict: dict[str, Any] = {
            "task_id": task_id,
            "verdict": "pass",
            "feedback": _STUB_FEEDBACK,
        }
        # Read current memory + run the canonical builder.  Keeps the
        # stub locked to whatever shape ``_patch_review`` writes; if
        # the builder's contract evolves the stub follows automatically.
        async with session_factory() as session:
            row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == ctx.run_id)
            )
        current_memory: Mapping[str, Any] = (row.data if row is not None else {}) or {}
        patch = patch_review_builder(result_dict, current_memory)
        return {**result_dict, "__memory_patch": patch}

    return LocalExecutor(ref="local:stub-pass-reviewer", handler=_handler)
```

Why this shape:
- Reuses `_patch_review` from `bootstrap.py` so the stub can NEVER drift from canonical `reviewHistory` shape (BUG-010 lock-in).
- `_patch_review` is currently a closure inside `register_lifecycle_v03`. T-270 extracts it to module scope so the stub can import it. Trivial refactor; no behaviour change.
- The stub is a *factory function*, not a class. The `mode` discriminator is `LocalExecutor`'s `mode="local"` — the runtime treats this exactly like any other LocalExecutor.

### Step 3 — Selector in `register_lifecycle_v03`

`bootstrap.py`, replace the current `register_lifecycle_v03` reviewer block:

```python
# IMP-003: select the reviewer binding from settings.
from app.config import settings as _settings  # lazy import preserves test override
reviewer_choice = _settings.LIFECYCLE_REVIEWER

if reviewer_choice == "llm-content":
    reviewer_executor = LLMContentExecutor(
        ref="llm:review_implementation",
        system_prompt=_load_prompt("review_implementation"),
        user_prompt_template=...,  # unchanged from today
        result_schema=ReviewImplementationResult,
        llm_provider=llm_provider,
        memory_patch_builder=_patch_review,
        prompt_context_loader=_load_review_context,
        session_factory=session_factory,
    )
elif reviewer_choice == "stub-pass":
    from app.modules.ai.executors.stub_reviewer import make_stub_pass_reviewer
    logger.warning(
        "register_lifecycle_v03: LIFECYCLE_REVIEWER=stub-pass — every "
        "implementation will be auto-approved.  Smoke / CI only."
    )
    reviewer_executor = make_stub_pass_reviewer(
        session_factory=session_factory,
        patch_review_builder=_patch_review,
    )
else:
    # Pydantic literal validation prevents this branch in practice; the
    # explicit raise documents the closed enumeration.
    raise ValueError(f"unknown LIFECYCLE_REVIEWER={reviewer_choice!r}")

logger.info(
    "register_lifecycle_v03: reviewer binding=%s (LIFECYCLE_REVIEWER)",
    reviewer_choice,
)

registry.register(agent_ref, "review_implementation", reviewer_executor)
```

The else-branch is defensive — pydantic's `Literal` already gates this. If a `remote` slot lands later, it's a third elif here.

### Step 4 — Extract `_patch_review` to module scope

Currently a closure inside `register_lifecycle_v03`. To let the stub import it, lift it to module scope at the top of `bootstrap.py`. Behaviour preserved; only the def site moves.

```python
def _patch_review(
    result: Mapping[str, Any], current_memory: Mapping[str, Any]
) -> dict[str, Any]:
    ...  # unchanged BUG-010 implementation
```

Make sure no captured locals break the move — currently it captures nothing run-specific (the result and memory are the only inputs). Should be a clean lift.

### Step 5 — Tests

#### `tests/modules/ai/executors/test_stub_reviewer.py` (new)

- `test_stub_writes_canonical_review_history_entry` — invoke the stub handler directly with a `ctx` that carries a `taskId`; assert the `__memory_patch` lands under `lifecycle.v1.reviewHistory` with `verdict="pass"`, `feedback` starts with `"stub-pass:"`, and `attempt=1`.
- `test_stub_appends_to_existing_review_history` — seed memory with one review entry for the same task; invoke the stub; assert `attempt=2` and the existing entry is preserved (BUG-010 lock-in).
- `test_stub_preserves_other_namespace_keys` — seed memory with `lifecycle.v1.tasks` + `workItem`; assert the patch includes both unchanged.

#### `tests/integration/test_lifecycle_v03_review_pass_path.py` (new)

- `test_review_pass_advances_to_close_work_item` — boot the deterministic runtime against a tiny test agent that exercises the `review_implementation → approve_review (T10) → close_work_item` shape, with `LIFECYCLE_REVIEWER=stub-pass` set on the settings instance. Assert the run terminates at `completed` (not `failed`) and the engine `T10` transition fired exactly once. Mirrors the existing live-smoke shape but bounded to the review-pass slice.

(Skip the full lifecycle E2E test for the stub — the existing v03 tests cover the rest of the flow.)

#### `tests/test_settings.py` (or wherever)

- `test_lifecycle_reviewer_unknown_value_refuses_boot` — set env var to `"xyz"`, assert `Settings()` construction raises pydantic validation error.

### Step 6 — Doc updates

- `CLAUDE.md` Runtime Loop section: add a one-line bullet — "Reviewer binding is selected at bootstrap by `LIFECYCLE_REVIEWER`. Default `llm-content`. Smoke / CI use `stub-pass`. Future external service slots into a `remote` value (not yet implemented)."
- `.env.example` if it exists: add the var with the default and a comment.
- `docs/api-spec.md` env-vars section if such a section exists.

No `data-model.md` change — this is bootstrap-layer behaviour, no entity field added.

---

## Out of scope (explicit non-goals)

- **`remote` reviewer binding.** Reserved as a future literal value. Implementing it now would freeze the contract before the external reviewer exists.
- **Production guard against accidental stub-pass.** A `if ENV=production: refuse` check is a runbook concern, not a code one — the explicit warning log + the env-var-default is sufficient. If we add an env classification later (`ENVIRONMENT=prod|staging|dev`) we revisit.
- **Stub-pass with deterministic verdict variation** (e.g. always-fail, alternating) for testing the corrections-loop. The corrections-loop already has live coverage from the BUG-011 fix; a parameterised stub would be fixture overengineering.
- **Replacing the LLM path's prompts / context loader.** Out of scope; this IMP only adds an alternate binding.
- **GitHub diff effector** (FEAT-013) — independent. Even after diff fetch lands, smoke environments without real PRs still need stub-pass.

---

## Verification

- `uv run pytest tests/modules/ai/executors/test_stub_reviewer.py tests/integration/test_lifecycle_v03_review_pass_path.py` — new suite green.
- `uv run pytest` — full suite green; nothing regressed in the LLM-content review path.
- `uv run pyright` and `uv run ruff check .` clean.
- Manual: `LIFECYCLE_REVIEWER=stub-pass uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=docs/work-items/FEAT-SMOKE-001.md --follow` against a real engine reaches `close_work_item` with `status=completed`. Logs show `register_lifecycle_v03: reviewer binding=stub-pass`.
- `LIFECYCLE_REVIEWER=xyz uv run orchestrator serve` refuses to boot with a pydantic validation error.

---

## Rollback

Single-file revert covers the bootstrap changes. Stub module deletion. Env var becomes unrecognized but harmless if previously set (Settings tolerates extra env vars by default).

---

## Notes for the implementer

- Match `_patch_review`'s shape contract exactly. The cheap path is to call it from the stub (Step 2 does this) — drift is impossible by construction.
- The selector pattern is the load-bearing piece, not the stub itself. Future external-reviewer FEAT slots in by adding a third value; don't optimise the selector before that arrives.
- Don't make `LIFECYCLE_REVIEWER` a magic string in `bootstrap.py`. Use the typed setting; pydantic enforces the closed enumeration at boot.
- Lifespan log line is small but important — operators reading the boot log should see the binding choice in the first 50 lines.
