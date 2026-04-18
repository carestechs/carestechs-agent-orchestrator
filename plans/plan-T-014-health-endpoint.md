# Implementation Plan: T-014 — /health endpoint

## Task Reference
- **Task ID:** T-014
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S

## Overview
Implement `GET /health` with database, LLM provider, and flow engine checks. Returns 200 with status "ok" or "degraded".

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/health.py` | Create | Health router with dependency checks |
| `src/app/main.py` | Modify | Register health router |
