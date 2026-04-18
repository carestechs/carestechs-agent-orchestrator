# Implementation Plan: T-005 — Exception hierarchy + RFC 7807 handler + response envelope

## Task Reference
- **Task ID:** T-005
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Addresses AC-6 (control-plane stubs return 501 as Problem Details) and the envelope convention from `api-spec.md`. All routes depend on this.

## Overview
Define the `AppError` hierarchy, the global exception handler that converts `AppError` and `RequestValidationError` into RFC 7807 responses, and the `envelope()` helper for 2xx responses. One canonical place for error and response shapes so routes stay thin.

## Implementation Steps

### Step 1: `AppError` hierarchy
**File:** `src/app/core/exceptions.py`
**Action:** Modify

```python
class AppError(Exception):
    code: ClassVar[str]            # e.g. "not-found"
    http_status: ClassVar[int]     # e.g. 404
    title: ClassVar[str]           # short human title

    def __init__(self, detail: str, *, errors: dict[str, list[str]] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or {}
```

Subclasses (each sets the three ClassVars):
- `ValidationError` — `"validation-error"`, 400, "Validation error".
- `NotFoundError` — `"not-found"`, 404, "Not found".
- `ConflictError` — `"conflict"`, 409, "Conflict".
- `AuthError` — `"unauthorized"`, 401, "Unauthorized".
- `PolicyError` — `"policy-error"`, 500, "Policy error".
- `EngineError` — `"engine-error"`, 502, "Flow engine error".
- `ProviderError` — `"provider-error"`, 502, "LLM provider error".
- `NotImplementedYet` — `"not-implemented"`, 501, "Not implemented".

Base `problem_type` URI builder: `def problem_type(code: str) -> str: return f"https://orchestrator.local/problems/{code}"` — parametrized by `code` so subclasses don't each hard-code.

### Step 2: Problem Details schema
**File:** `src/app/core/exceptions.py`
**Action:** Modify

`class ProblemDetails(BaseModel)` with fields `type: str`, `title: str`, `status: int`, `detail: str`, `errors: dict[str, list[str]] | None = None`. `model_config` emits `application/problem+json`-compatible JSON. No alias_generator — RFC 7807 field names are already the right shape.

### Step 3: Global handler
**File:** `src/app/core/exceptions.py`
**Action:** Modify

`async def app_error_handler(request: Request, exc: AppError) -> JSONResponse` returns a `JSONResponse(status_code=exc.http_status, content=ProblemDetails(...).model_dump(exclude_none=True), media_type="application/problem+json")`.

Second handler `request_validation_error_handler` adapts `fastapi.exceptions.RequestValidationError` to a 400 `ProblemDetails` with `errors` populated per-field: `{".".join(str(x) for x in err["loc"][1:]): [err["msg"]]}` (skip the "body"/"query" sentinel).

Third handler for unhandled `Exception` → 500 `ProblemDetails` with `code="internal-error"`, `detail="internal error"` — never leak tracebacks. Log at `ERROR` with `exc_info=True`.

Export `register_exception_handlers(app: FastAPI)` that attaches all three — called from `create_app()` in T-012.

### Step 4: Response envelope helper
**File:** `src/app/core/envelope.py`
**Action:** Modify

```python
T = TypeVar("T")

class Meta(BaseModel):
    total_count: int = Field(serialization_alias="totalCount")
    page: int
    page_size: int = Field(serialization_alias="pageSize")

class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta | None = None
```

Helper `def envelope(data: T, meta: Meta | None = None) -> Envelope[T]: return Envelope(data=data, meta=meta)`. Routes return `Envelope[RunDto]` etc. — FastAPI picks up the generic for OpenAPI.

### Step 5: Tests
**File:** `tests/core/test_exceptions.py`
**Action:** Create

- One parameterized test per `AppError` subclass asserting `(exc.http_status, exc.code, exc.title)` mapping.
- Test handler round-trip: register handlers on a dummy FastAPI app with a route that `raise NotFoundError("x missing")`; assert `response.status_code == 404`, `response.headers["content-type"] == "application/problem+json"`, body matches schema.
- Test `RequestValidationError`: POST an invalid body to a test route, assert 400 body has per-field `errors`.
- Test unhandled `Exception` → 500 without traceback in body.

**File:** `tests/core/test_envelope.py`
**Action:** Create

- `envelope({"id": "x"}).model_dump(by_alias=True) == {"data": {"id": "x"}}` (no meta).
- With meta: `meta` serialized as `{"totalCount": ..., "page": ..., "pageSize": ...}`.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/core/exceptions.py` | Modify | `AppError` hierarchy + handlers + `ProblemDetails` |
| `src/app/core/envelope.py` | Create | `Envelope[T]` generic + `Meta` DTO + `envelope()` |
| `tests/core/test_exceptions.py` | Create | Per-subclass mapping + handler round-trip |
| `tests/core/test_envelope.py` | Create | Envelope shape + camelCase meta |

## Edge Cases & Risks

- **`RequestValidationError` loc ordering.** Nested fields come through as `("body", "intake", "featureBriefPath")`. The join logic must handle nested dicts without producing `body.intake.featureBriefPath` OR `featureBriefPath` inconsistently — pick one (recommend nested-dot path minus the `"body"` prefix) and stick with it.
- **OpenAPI response models.** Registering a default 500 response model via `responses={...}` on the app gives users a discoverable Problem Details schema in `/docs` — do this in T-012.
- **`NotImplementedYet` is our own, not Python's `NotImplementedError`.** Don't raise `NotImplementedError` in service stubs — the handler won't catch it cleanly. Always `raise NotImplementedYet("start_run")` etc.
- **`application/problem+json` content type.** Some clients only accept `application/json`. Setting `media_type` on `JSONResponse` is technically correct per RFC 7807; if it causes trouble with the Typer CLI's JSON mode later, relax to `application/json` with a note.

## Acceptance Verification

- [ ] **Code mapping:** parameterized test over all subclasses green.
- [ ] **RFC 7807 body:** handler round-trip test asserts `type` URI, `title`, `status`, `detail` all present.
- [ ] **Envelope shape:** `envelope()` test asserts camelCase `meta` and omitted `meta` when `None`.
- [ ] **Validation surface:** `RequestValidationError` test asserts per-field `errors` dict matches the pattern in `api-spec.md` example.
- [ ] **No traceback leaks:** unhandled exception test asserts body has no stack trace text.
