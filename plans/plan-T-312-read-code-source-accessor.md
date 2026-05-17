# Implementation Plan: T-312 — `read_code_source` accessor with memory-sidecar precedence

## Task Reference
- **Task ID:** T-312
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** One accessor, one place. Every future code-touching executor calls this. Centralizing the read precedence in one ≤30-line module prevents drift the first time a producer executor lands.

## Overview
Create `src/app/modules/ai/executors/code_source.py` exporting `read_code_source(ctx, memory=None) -> CodeSourceDto`. Implements the fixed precedence: memory sidecar's `workBranch` → intake's `workBranch` → raise if no intake `codeSource` at all.

## Implementation Steps

### Step 1: Confirm `DispatchContext.intake` shape
**File:** `src/app/modules/ai/executors/base.py`
**Action:** Read

Confirm `DispatchContext.intake: Mapping[str, Any]` is already the existing surface (it is — line 46). The accessor reads from here.

### Step 2: Create the accessor module
**File:** `src/app/modules/ai/executors/code_source.py`
**Action:** Create

```python
"""Accessor for the run-intake `codeSource` block.

This is the sole sanctioned read path. Executors must NOT call
``ctx.intake["codeSource"]`` directly — doing so bypasses the
memory-sidecar precedence and is a review blocker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.schemas import CodeSourceDto


def read_code_source(
    ctx: DispatchContext,
    *,
    memory: Mapping[str, Any] | None = None,
) -> CodeSourceDto:
    """Resolve the run's code-source block.

    Precedence for ``workBranch``: memory sidecar (if non-empty) → intake → None.
    All other fields come from intake.

    Raises ``ValueError`` if intake carries no ``codeSource`` key.
    """
    raw = ctx.intake.get("codeSource")
    if raw is None:
        raise ValueError("codeSource missing from intake")

    dto = CodeSourceDto.model_validate(raw)

    if memory is not None:
        sidecar = memory.get("codeSource") or {}
        memory_work_branch = sidecar.get("workBranch")
        if memory_work_branch:
            dto = dto.model_copy(update={"work_branch": memory_work_branch})

    return dto
```

### Step 3: No exports change needed
**File:** N/A
**Action:** Verify

The module stands alone; no `__init__.py` re-export needed (executors import the accessor by fully-qualified path).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/code_source.py` | Create | New ~30-line module. |

## Edge Cases & Risks
- **Memory sidecar shape:** `memory.get("codeSource") or {}` handles both "key absent" and "key present but None". Without the `or {}`, a `None` value would `.get()`-crash.
- **Empty string is not a valid override.** The `if memory_work_branch:` guard treats `""` as "no override". Aligns with the DTO's branch validator that rejects empty strings.
- **No mutation contract.** `model_copy(update=...)` returns a fresh instance — never edits the input DTO. `ctx` and `memory` are never written.
- **Don't add a fallback path that constructs a partial DTO from memory alone.** Memory only overrides `workBranch`; `repo` and `baseBranch` must come from intake. Constructing from memory alone would erode the intake-as-source-of-truth contract.
- **`Mapping[str, Any]` keeps the test fixtures simple.** Tests pass `{"codeSource": {"workBranch": "feat/x"}}` directly without constructing a `RunMemory`.

## Acceptance Verification
- [ ] `read_code_source(ctx)` returns the intake DTO when no `memory` is passed.
- [ ] `read_code_source(ctx, memory={"codeSource": {"workBranch": "feat/x"}})` returns DTO with `work_branch="feat/x"` even if intake `workBranch is None`.
- [ ] `read_code_source(ctx, memory={"codeSource": {"workBranch": None}})` does NOT override an intake-supplied `workBranch`.
- [ ] `read_code_source(ctx, memory={"codeSource": {"workBranch": ""}})` does NOT override (empty string treated as absent).
- [ ] `read_code_source(ctx, memory={})` returns the intake DTO unchanged.
- [ ] Missing intake `codeSource` raises `ValueError("codeSource missing from intake")`.
- [ ] After call, `ctx.intake` and `memory` are byte-identical to before (mutation guard via `deepcopy` snapshot).
- [ ] `pyright` clean.
