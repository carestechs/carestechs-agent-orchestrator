# Plan: T-022 — Dockerfile (multi-stage)

## Overview

Create a multi-stage Dockerfile and `.dockerignore` for the orchestrator service.

## Steps

1. **Create `.dockerignore`** — exclude `.env`, `.git`, `tests/`, `docs/`, `plans/`, `tasks/`, `__pycache__`, `.ai-framework/`, and other non-runtime files.
2. **Create `Dockerfile`** — two stages:
   - **Stage 1 (builder):** `python:3.12-slim`, install `uv`, copy `pyproject.toml` + `uv.lock`, run `uv sync --frozen --no-dev` to create a venv with all production deps.
   - **Stage 2 (runtime):** `python:3.12-slim`, create non-root user, copy the synced venv from builder, copy `src/`, expose port 8000, `CMD` runs uvicorn pointing at `app.main:app`.
3. **Verify** — run ruff and pyright to confirm no regressions.

## Key Decisions

- Use `COPY --from=ghcr.io/astral-sh/uv:latest` to get the `uv` binary (smaller than pip-installing it).
- Install the project in the builder via `uv sync` which resolves deps + installs the project wheel into `.venv`.
- Non-root user `appuser` with UID 1000 in the final stage.
- Final image contains only the venv and `src/app` — no dev deps, no docs, no tests.
