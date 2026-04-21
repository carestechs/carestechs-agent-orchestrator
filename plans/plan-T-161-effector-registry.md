# Implementation Plan: T-161 — Effector protocol + registry + trace kind

## Task Reference
- **Task ID:** T-161
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1, AC-11. The load-bearing seam for every subsequent task. Registry + protocol + trace kind together define how all future outbound actions plug in.

## Overview
Ship the `Effector` protocol, the `EffectorRegistry`, the `EffectorContext` carrier, and the `effector_call` trace kind. No concrete effectors yet — those land in T-162/T-163/T-164. Focus is the interface and dispatch loop.

## Implementation Steps

### Step 1: Define `EffectorContext` + `EffectorResult`
**File:** `src/app/modules/ai/lifecycle/effectors/context.py`
**Action:** Create

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings

EffectorStatus = Literal["ok", "error", "skipped"]


@dataclass(frozen=True, slots=True)
class EffectorContext:
    """Carrier passed to every effector fire.  Immutable by design."""
    entity_type: Literal["work_item", "task"]
    entity_id: uuid.UUID
    from_state: str | None       # None for entry-only effectors
    to_state: str
    transition: str              # e.g. "T4", "W1", "impl_review->done"
    correlation_id: uuid.UUID | None
    db: AsyncSession
    settings: Settings
    # Extensibility: effectors that need more (engine client, github client,
    # policy provider) pull from settings or from a DI registry — not from
    # widening this context.


@dataclass(frozen=True, slots=True)
class EffectorResult:
    effector_name: str
    status: EffectorStatus
    duration_ms: int
    error_code: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Key design call: context is **immutable**. If an effector needs state beyond what's in the context, it pulls from `settings` or from a DI registry — the context doesn't grow per-effector. Keeps the seam narrow.

### Step 2: Define the `Effector` Protocol
**File:** `src/app/modules/ai/lifecycle/effectors/base.py`
**Action:** Create

```python
from __future__ import annotations
from typing import ClassVar, Protocol, runtime_checkable

from app.modules.ai.lifecycle.effectors.context import EffectorContext, EffectorResult


@runtime_checkable
class Effector(Protocol):
    """A named outbound action fired on a state transition.

    MUST be pure (no hidden global state) and MUST NOT raise — failures
    are returned as an ``EffectorResult`` with ``status="error"``.  The
    registry isolates failures so one misbehaving effector cannot stop
    the dispatch pipeline.
    """

    name: ClassVar[str]

    async def fire(self, ctx: EffectorContext) -> EffectorResult: ...
```

### Step 3: `no_effector` decorator for explicit exemptions
**File:** `src/app/modules/ai/lifecycle/effectors/base.py`
**Action:** Modify

```python
from typing import Callable

def no_effector(reason: str) -> Callable[[str], str]:
    """Marker decorator for transitions that intentionally fire no effector.

    Used by T-171's startup validator to distinguish "forgotten" vs
    "intentionally silent" transitions.  The reason is logged at boot.
    """
    ...
```

Implementation: stores `(transition_key, reason)` in a module-level exemption registry that T-171 will cross-check.

### Step 4: `EffectorRegistry`
**File:** `src/app/modules/ai/lifecycle/effectors/registry.py`
**Action:** Create

Key design:
- **Transition key** is a string like `"task:implementing->impl_review"` or `"work_item:entry:in_progress"` (entry-only effectors — no from-state). Consistent scheme critical for T-162/T-163 to register against.
- `register(transition_key, effector)` appends to a list — insertion order is dispatch order.
- `fire_all(ctx)` iterates the list for `build_transition_key(ctx)`, calling each effector in try/except. Each result is appended to the output list.
- Trace emission happens *inside* the registry's dispatch, not the effector — uniform.

```python
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def build_transition_key(
    entity_type: str, from_state: str | None, to_state: str
) -> str:
    if from_state is None:
        return f"{entity_type}:entry:{to_state}"
    return f"{entity_type}:{from_state}->{to_state}"


class EffectorRegistry:
    def __init__(self) -> None:
        self._effectors: dict[str, list[Effector]] = defaultdict(list)

    def register(self, key: str, effector: Effector) -> None:
        self._effectors[key].append(effector)

    async def fire_all(self, ctx: EffectorContext) -> list[EffectorResult]:
        key = build_transition_key(ctx.entity_type, ctx.from_state, ctx.to_state)
        results: list[EffectorResult] = []
        for effector in self._effectors.get(key, []):
            start = time.monotonic()
            try:
                result = await effector.fire(ctx)
            except Exception as exc:
                result = EffectorResult(
                    effector_name=effector.name,
                    status="error",
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error_code="effector-exception",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                logger.exception(
                    "effector raised",
                    extra={"effector": effector.name, "entity_id": str(ctx.entity_id)},
                )
            results.append(result)
        return results
```

Registry is **per-process**; composition-root instantiates one and hands it to lifespan.

### Step 5: `effector_call` trace kind
**File:** `src/app/modules/ai/trace.py`
**Action:** Modify

Extend the `TraceKind` enum / union with `effector_call`. Trace payload shape:

```python
{
    "kind": "effector_call",
    "effector_name": "github_check_create",
    "entity_id": "...",
    "entity_type": "task",
    "transition": "implementing->impl_review",
    "status": "ok" | "error" | "skipped",
    "duration_ms": 42,
    "error_code": null | "string",
    "detail": null | "string",
    "timestamp": "..."
}
```

Add a helper `emit_effector_trace(trace, ctx, result)` in the effectors module that the registry calls after each result. Keep the trace backend agnostic — the JSONL writer already handles arbitrary dicts.

### Step 6: Trace emission inside `fire_all`
**File:** `src/app/modules/ai/lifecycle/effectors/registry.py`
**Action:** Modify

Thread a `TraceStore` (or equivalent) into the registry, either via constructor or via `EffectorContext` extension — lean toward constructor to keep context immutable. After each `result` is produced, call `trace.emit_effector_call(ctx, result)`.

### Step 7: Unit tests
**File:** `tests/modules/ai/lifecycle/effectors/test_registry.py`
**Action:** Create

Cases:
- **Register + lookup** — register two effectors under the same key, assert both fire in order.
- **Empty dispatch** — fire against a key with no registrations → `[]`, no errors.
- **Failure isolation** — first effector raises, second still fires. Both results in the returned list; first is `status="error"`.
- **`skipped` propagates** — an effector returning `skipped` is traced like any other.
- **Trace emission** — stub `TraceStore`, assert exactly N emits for N effectors.
- **Transition key shape** — `build_transition_key` round-trips correctly for entry-only and transition variants.

```python
# tests/modules/ai/lifecycle/effectors/__init__.py — empty package init
# tests/modules/ai/lifecycle/effectors/conftest.py — stub TraceStore + EffectorContext builder
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/effectors/__init__.py` | Create | Package init + public re-exports. |
| `src/app/modules/ai/lifecycle/effectors/base.py` | Create | `Effector` protocol + `no_effector`. |
| `src/app/modules/ai/lifecycle/effectors/context.py` | Create | `EffectorContext` + `EffectorResult`. |
| `src/app/modules/ai/lifecycle/effectors/registry.py` | Create | `EffectorRegistry` + dispatch. |
| `src/app/modules/ai/trace.py` | Modify | Add `effector_call` trace kind. |
| `tests/modules/ai/lifecycle/effectors/__init__.py` | Create | Test package init. |
| `tests/modules/ai/lifecycle/effectors/conftest.py` | Create | Shared fixtures. |
| `tests/modules/ai/lifecycle/effectors/test_registry.py` | Create | Registry unit tests. |

## Edge Cases & Risks
- **Async effector exception handling.** `try/except Exception` is right here — a misbehaving effector must not take down the pipeline. But the `logger.exception` call captures the full traceback for forensics; don't swallow it silently.
- **Registry instantiation timing.** Must land on `app.state` before the first webhook arrives. Lifespan wires it in T-162 (`register_all_effectors`). Don't anticipate that here; this task just ships the primitive.
- **Context growth pressure.** First time an effector "needs" a new field in `EffectorContext`, scrutinize hard — the right answer is usually "pull it from settings" or "inject via DI in the effector's constructor." A wide context rots the seam.
- **Trace backend coupling.** `TraceStore` is already a protocol — reuse it. Don't invent a new sink for effector traces.

## Acceptance Verification
- [ ] Protocol + registry + context exist in the documented paths.
- [ ] Registry preserves insertion order in dispatch.
- [ ] Failing effector never halts the pipeline; result returned instead.
- [ ] Trace emission is per-result, not per-fire-call.
- [ ] `no_effector` decorator registers exemptions for T-171 to consume.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/lifecycle/effectors/` green.
