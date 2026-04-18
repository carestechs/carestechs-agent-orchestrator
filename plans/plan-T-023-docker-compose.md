# Implementation Plan: T-023 — Docker Compose (dev + prod) + env examples

## Task Reference
- **Task ID:** T-023
- **Type:** DevOps
- **Workflow:** standard
- **Complexity:** S

## Overview
Dev compose with Postgres 16, prod compose for the API service on a shared network, and .env example files listing every Settings field.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docker-compose.yml` | Create | Dev: Postgres 16 + orchestrator service |
| `docker-compose.prod.yml` | Create | Prod: API service on external network |
| `.env.example` | Create | All env vars with descriptions |
| `.env.production.example` | Create | Prod-specific env vars |
