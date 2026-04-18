# ---------------------------------------------------------------------------
# Stage 1 — builder: install dependencies + build the project wheel
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy only dependency-related files first for layer caching.
# README.md is required by hatchling (build backend) for metadata.
COPY pyproject.toml uv.lock README.md ./

# Create a virtual-env with production deps only (no dev group).
RUN uv sync --frozen --no-dev --no-install-project

# Copy the source tree and install the project itself.
COPY src/ src/
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2 — runtime: lean image with just the venv + source
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user for security.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /home/appuser

# Copy the fully-populated virtual-env from the builder.
COPY --from=builder /build/.venv .venv/

# Copy the application source (needed if any runtime path references src/).
COPY --from=builder /build/src/ src/

# Alembic config lives at repo root; copy it so migrations can run inside the
# container if needed.
COPY alembic.ini .

# Activate the venv by prepending it to PATH.
ENV PATH="/home/appuser/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Switch to non-root user.
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
