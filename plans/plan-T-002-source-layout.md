# Implementation Plan: T-002 — Create source layout and package scaffolding

## Task Reference
- **Task ID:** T-002
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Locks the directory structure before services land (AC-10).

## Overview
Materialize the directory tree documented in `CLAUDE.md` → Key Directories with empty packages and placeholder docstrings. No logic; just the shape. This freezes the layout so later PRs don't drift and makes every later import resolve out of the gate.

## Implementation Steps

### Step 1: Create `src/app/` package skeleton
**File:** `src/app/__init__.py` and all subpackages listed below
**Action:** Create

Create each of these as an empty-ish Python file with a one-line module docstring stating its role. Each `__init__.py` is empty (no re-exports).

- `src/app/__init__.py` — `"""carestechs-agent-orchestrator application package."""`
- `src/app/main.py` — `"""FastAPI app factory and module-level `app`."""` + `from fastapi import FastAPI` + `app = FastAPI()` (placeholder; real factory in T-012).
- `src/app/cli.py` — `"""Typer CLI entry point."""` + `import typer` + `main = typer.Typer()` + `if __name__ == "__main__": main()`. Keep it minimal so T-001 AC-5 passes.
- `src/app/config.py` — docstring only.
- `src/app/core/__init__.py` + `core/database.py`, `core/dependencies.py`, `core/exceptions.py`, `core/llm.py` — docstrings only.
- `src/app/contracts/__init__.py` + `contracts/ai.py` — docstrings only.
- `src/app/modules/__init__.py`, `modules/ai/__init__.py`, `modules/ai/{router,service,models,schemas,dependencies}.py` — docstrings only.
- `src/app/modules/ai/tools/__init__.py` — `"""Policy action space: one tool per file."""`.
- `src/app/migrations/__init__.py` — docstring only (Alembic populates the rest in T-011).

### Step 2: Create `tests/` skeleton
**File:** `tests/` tree
**Action:** Create

- `tests/__init__.py` — empty.
- `tests/conftest.py` — empty (real fixtures in T-024).
- `tests/modules/__init__.py`, `tests/modules/ai/__init__.py` — empty.
- `tests/integration/__init__.py`, `tests/contract/__init__.py` — empty.
- `tests/core/__init__.py` — empty (used by T-004/T-005/T-006/T-007/T-008 test files).

### Step 3: Verify import graph
**File:** — (verification only)
**Action:** —

Run the import sanity check from the task definition: `python -c "import app.main, app.cli, app.config, app.core.database, ..."`. If any module errors, fix the missing stub.

### Step 4: Update `pyproject.toml` source layout hint
**File:** `pyproject.toml`
**Action:** Modify

Add `[tool.hatch.build.targets.wheel] packages = ["src/app"]` (or the equivalent for the chosen build backend). Ensures `uv sync` installs `app` as an importable package from `src/`. If using `uv`'s default build backend, set `[tool.uv.sources]` appropriately.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/**/*.py` | Create | Empty packages + placeholder stubs with docstrings |
| `tests/**/*.py` | Create | Empty test package tree |
| `pyproject.toml` | Modify | Add `src/`-layout package declaration |

## Edge Cases & Risks

- **`src/`-layout vs flat-layout.** `src/app/` layout requires the build backend to declare it; a missed declaration causes `ImportError: No module named 'app'` at test time. Verify via `uv sync && uv run python -c "import app"`.
- **Docstrings triggering ruff rules.** `D`-family rules aren't enabled in T-001's ruff config, so single-line docstrings without period won't fail. If docstring rules are enabled later, revisit.
- **`main.py`'s placeholder `app = FastAPI()`.** OpenAPI will show an empty spec for a short window (until T-012). Acceptable — `/health` and routes aren't promised until T-014/T-015.

## Acceptance Verification

- [ ] **Directory match:** `tree src/app` output matches `CLAUDE.md` → Key Directories exactly (subtree-level).
- [ ] **Every package has a docstring:** grep `src/app -name __init__.py -exec head -1 {} \;` shows a docstring on each.
- [ ] **Import sanity:** `uv run python -c "import app.main, app.cli, app.config, app.core.database, app.core.dependencies, app.core.exceptions, app.core.llm, app.modules.ai.router, app.modules.ai.service, app.modules.ai.models, app.modules.ai.schemas, app.modules.ai.dependencies"` exits 0.
- [ ] **Tests still collect:** `uv run pytest --collect-only` exits 0 (no tests yet; no errors).
