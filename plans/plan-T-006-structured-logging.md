# Implementation Plan: T-006 — Structured logging with `run_id` / `step_id` contextvars

## Task Reference
- **Task ID:** T-006
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** CLAUDE.md requires structured logs with run/step correlation; every runtime task depends on this. Addresses the contextvars null-edge-case in FEAT-001 §9.

## Overview
Configure stdlib `logging` with a JSON formatter that injects `run_id`/`step_id` from `contextvars` only when set. Provide async-safe `bind_run_id` / `bind_step_id` context managers. Wire at app startup using `Settings.log_level`.

## Implementation Steps

### Step 1: Define contextvars and binders
**File:** `src/app/core/logging.py`
**Action:** Modify

```python
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_step_id: ContextVar[str | None] = ContextVar("step_id", default=None)

@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)

# same for bind_step_id
```

Async-safe because `ContextVar` is per-task in asyncio. Both binders are sync-context (async code just `with bind_run_id(x):` works from async too).

### Step 2: JSON formatter
**File:** `src/app/core/logging.py`
**Action:** Modify

```python
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid, sid = _run_id.get(), _step_id.get()
        if rid is not None:
            payload["run_id"] = rid
        if sid is not None:
            payload["step_id"] = sid
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Carry extras passed via logger.info("msg", extra={...})
        for key, value in record.__dict__.items():
            if key not in _LOGRECORD_STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)
```

Crucial: `run_id` / `step_id` keys are **omitted** when the contextvar is `None`, per FEAT-001 §9.

### Step 3: `configure_logging()`
**File:** `src/app/core/logging.py`
**Action:** Modify

```python
def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Quiet noisy libs
    logging.getLogger("uvicorn.access").setLevel("WARNING")
```

Called from `create_app()` (T-012) using `get_settings().log_level`.

### Step 4: Tests
**File:** `tests/core/test_logging.py`
**Action:** Create

- **Unbound log line omits run_id/step_id.** Capture via `caplog` or a custom handler; assert the parsed JSON does NOT contain `"run_id"` or `"step_id"` keys.
- **With `bind_run_id`:** capture a log emitted inside the context, assert `payload["run_id"] == "r-1"`.
- **Nesting resets correctly:** bind r-1 → emit → bind r-2 → emit → exit inner → emit. Assert the three messages have `r-1`, `r-2`, `r-1` respectively.
- **Async isolation:** two concurrent tasks each bind different run ids; assert each task's log line carries its own id. Use `asyncio.gather` with `asyncio.sleep(0)` yields to force interleaving.
- **`extra={}`:** emit `logger.info("x", extra={"foo": 1})` and assert `payload["foo"] == 1` makes it through.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/core/logging.py` | Modify | Contextvars, binders, `JsonFormatter`, `configure_logging` |
| `tests/core/test_logging.py` | Create | Bind/unbind, nesting, async isolation, extra |

## Edge Cases & Risks

- **`caplog` bypasses custom formatters.** `pytest`'s `caplog` uses its own handler, so you can't assert on the formatter's output via `caplog`. Attach a test-local `StreamHandler(io.StringIO())` with `JsonFormatter` and parse its output.
- **`_LOGRECORD_STANDARD_FIELDS`** — there's no public constant. Define our own set (copy from CPython's `logging.LogRecord.__init__` fields) or filter by matching `logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()`.
- **Timezone correctness.** Use `datetime.fromtimestamp(record.created, UTC)` — NOT `datetime.utcnow()` (deprecated) and NOT naive datetimes. ISO format includes `+00:00` offset.
- **Uvicorn access logs** remain plain-text unless we configure them separately — intentional for v1 (don't fight uvicorn). If we want fully-JSON access logs later, pass a custom log config dict to uvicorn.
- **`logger.exception(...)` carries exc_info.** Our formatter adds it as a string block, which is fine for JSON log ingestion tools.

## Acceptance Verification

- [ ] **JSON-valid output:** every log line is parseable as a JSON object.
- [ ] **Omission rule:** unbound line has no `run_id` / `step_id` keys (not even as `null`).
- [ ] **Bind roundtrip:** bound line contains the expected value.
- [ ] **Async-safe:** concurrent-task test passes without cross-contamination.
- [ ] **Config wiring:** `configure_logging("DEBUG")` yields DEBUG-level root logger.
