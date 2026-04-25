#!/bin/sh
# Container entrypoint — run Alembic migrations against the configured
# DATABASE_URL, then exec the CMD (uvicorn by default).
#
# Migrating on every container start is safe because Alembic is idempotent
# (no-ops when the schema is already at head). It also means the project
# boots cold against an empty database under the DevTools umbrella, where
# the shared postgres volume's per-project DB is created by
# infra/init-databases.sql but contains no schema yet.
#
# Skip with SKIP_MIGRATIONS=1 if you need to start the API without
# touching the schema (e.g. while debugging a migration).
set -eu

if [ "${SKIP_MIGRATIONS:-0}" = "1" ]; then
    echo "entrypoint: SKIP_MIGRATIONS=1 — skipping alembic upgrade"
else
    echo "entrypoint: running alembic upgrade head"
    # Invoke via `python -m` because the venv is built at /build/.venv in
    # the Docker builder stage and copied to /home/appuser/.venv at runtime;
    # script shebangs (`#!/build/.venv/bin/python`) are stale at runtime.
    python -m alembic upgrade head
fi

exec "$@"
