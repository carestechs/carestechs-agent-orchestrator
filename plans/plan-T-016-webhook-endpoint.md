# Implementation Plan: T-016 — Webhook events endpoint (fully implemented)

## Task Reference
- **Task ID:** T-016
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M

## Overview
Implement POST /hooks/engine/events with HMAC verification, payload validation, delegation to service.ingest_engine_event, and 401 on bad signature (event still persisted).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/router.py` | Modify | hooks_router with webhook endpoint |
| `src/app/main.py` | Modify | Register hooks_router |
