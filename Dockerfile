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

# Alembic config lives at repo root; copy it so the entrypoint can run
# migrations inside the container before the API starts serving.
COPY alembic.ini .

# Entrypoint runs ``alembic upgrade head`` then execs the CMD. Lives under
# /usr/local/bin so it's on PATH for any user. Set SKIP_MIGRATIONS=1 to
# bypass the migration step (e.g. while debugging a broken migration).
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Activate the venv by prepending it to PATH.
ENV PATH="/home/appuser/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Switch to non-root user.
USER appuser

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
